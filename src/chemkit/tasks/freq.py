"""Vibrational frequencies + ideal-gas thermochemistry (ZPE, H, S, G).

Always runs an opt-freq pipeline: the input geometry is first optimized with
the same method that will compute the Hessian, and the optimized atoms are
then passed to the frequency step. This eliminates spurious imaginary modes
from inputs that are near — but not at — a stationary point (the most common
failure mode on hand-drawn or QM-from-MM geometries).

For xtb (GFN2): ASE's `Vibrations` driver does finite-difference forces; then
ASE's `IdealGasThermo` produces ZPE/H/S/G. Reliable because xtb-python returns
clean forces at every displacement.

For mopac (PM7): MOPAC's native FORCE+THERMO keywords do the analytic Hessian
and ideal-gas thermo in one binary call. The previous ASE-finite-difference
approach via the MOPAC calculator failed because each displacement spawned a
fresh MOPAC process and some returned NaN forces, leaving only ~half of 3N-6
modes — IdealGasThermo then refused to run.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

import numpy as np

from ..calculators import (
    build_calculator, apply_calc_to_atoms, MOPAC_SOLVENT_EPS,
    method_label, program_label,
)
from ..io import read_geometry
from ..schema import base_result, element_warnings
from ._mopac_parsers import parse_mopac_extras, parse_mopac_force
from . import opt as opt_task


# 1 cal/mol = 4.184 J/mol; 1 eV = 23.060547830619026 kcal/mol
KCAL_TO_EV = 1.0 / 23.060547830619026
CAL_TO_EV = KCAL_TO_EV / 1000.0


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    temperature_K: float = 298.15,
    pressure_Pa: float = 101325.0,
    geometry: str = "nonlinear",
    symmetrynumber: int = 1,
    preopt: bool = True,
    preopt_fmax: float = 0.001,
    preopt_steps: int = 500,
    auto_confsearch: bool = False,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    """Opt-freq pipeline. The frequency calculation is performed on the
    optimized geometry by default; pass preopt=False to skip the optimization
    step (useful when the caller is sure the input is already a stationary
    point and re-optimization would waste cycles).

    auto_confsearch: if True, runs CREST conformer search + PM7 postopt
    before the freq step and uses the lowest-energy conformer as the input
    geometry. Useful for flexible molecules where the user-supplied geometry
    may not be the global minimum (an off-minimum input typically surfaces
    as spurious imaginary modes from genuine soft-mode saddles). Adds the
    full confsearch result block to the output under `auto_confsearch`.
    """
    method = method.lower()

    confsearch_info: Optional[Dict[str, Any]] = None
    if auto_confsearch:
        # Run CREST + PM7 postopt; substitute the lowest-energy minimum as
        # the input geometry for the subsequent preopt+freq pipeline.
        from . import confsearch as cs_task
        cs_result = cs_task.run(
            input_path,
            method="xtb",          # CREST is xtb-only; postopt at PM7
            solvent=solvent,
            postopt="mopac",
            charge=charge,
            multiplicity=multiplicity,
            cli="(internal auto-confsearch for freq)",
        )
        post = cs_result.get("postopt") or {}
        best_xyz = post.get("best_xyz")
        if best_xyz and os.path.isfile(best_xyz):
            # Copy into a stable location next to the original work area so
            # the user can find it after the freq job exits.
            cs_workdir = tempfile.mkdtemp(prefix="chemkit_freqcs_")
            persistent_best = os.path.join(cs_workdir, "best_conformer.xyz")
            shutil.copyfile(best_xyz, persistent_best)
            input_path = persistent_best
            # The conformer was already PM7-optimized by confsearch's postopt
            # step — running the freq preopt on top of it usually drifts to a
            # nearby soft-mode saddle. Skip the redundant preopt unless the
            # caller explicitly asked for it on a separate basis.
            preopt = False
            confsearch_info = {
                "performed": True,
                "n_unique_conformers": post.get("n_unique"),
                "best_xyz": persistent_best,
                "best_hof_kcal_mol": post.get("lowest_hof_kcal_mol"),
                "ensemble_xyz": post.get("ensemble_xyz"),
                "seed_source": post.get("seed_source"),
                "preopt_skipped": True,
            }
        else:
            # CREST returned nothing usable; fall back to the original input
            # but record that auto-confsearch was attempted.
            confsearch_info = {
                "performed": True,
                "n_unique_conformers": post.get("n_unique") or 0,
                "best_xyz": None,
                "note": "auto_confsearch produced no usable best_xyz; freq run on original input",
            }

    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    preopt_info: Dict[str, Any] = {}
    freq_input_path = input_path

    if preopt:
        # Optimize first so the Hessian is taken at a true stationary point.
        # Use a tighter fmax than `opt` default (0.05) since residual forces
        # propagate into imaginary modes near 0 cm⁻¹.
        # Per-method floor: xtb (ASE BFGS) reliably reaches fmax=0.001 eV/Å on
        # small systems, but MOPAC's EF optimizer hits a practical floor around
        # GNORM=0.01 kcal/mol/Å (~ fmax=0.0004 eV/Å) and any tighter target
        # produces non-convergence on flexible organics — clip preopt_fmax so
        # MOPAC gets a reachable target instead of failing the whole freq job.
        effective_fmax = preopt_fmax if method != "mopac" else max(preopt_fmax, 0.005)
        opt_work = tempfile.mkdtemp(prefix="chemkit_optfreq_")
        opt_xyz = os.path.join(opt_work, "preopt.xyz")
        opt_result = opt_task.run(
            input_path=input_path,
            method=method,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            fmax=effective_fmax,
            steps=preopt_steps,
            out_xyz=opt_xyz,
            cli="(internal preopt for freq)",
            tier=tier,
            functional=functional,
            basis=basis,
        )
        # Reload atoms from the optimized xyz so the freq step works on the
        # exact geometry written to disk.
        atoms = read_geometry(opt_xyz)
        symbols = atoms.get_chemical_symbols()
        freq_input_path = opt_xyz
        preopt_info = {
            "performed": True,
            "fmax_target_eV_per_A": effective_fmax,
            "converged": bool(opt_result.get("converged")),
            "n_steps": opt_result.get("n_steps"),
            "optimized_xyz": opt_xyz,
            "preopt_energy_eV": opt_result.get("total_energy_eV"),
            "preopt_heat_of_formation_kcal_mol":
                opt_result.get("final_heat_of_formation_kcal_mol"),
        }

    if method == "mopac":
        result = _run_mopac(
            input_path=freq_input_path,
            atoms=atoms,
            symbols=symbols,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            temperature_K=temperature_K,
            pressure_Pa=pressure_Pa,
            cli=cli,
        )
    else:
        result = _run_ase(
            input_path=freq_input_path,
            atoms=atoms,
            symbols=symbols,
            method=method,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            temperature_K=temperature_K,
            pressure_Pa=pressure_Pa,
            geometry=geometry,
            symmetrynumber=symmetrynumber,
            cli=cli,
            tier=tier,
            functional=functional,
            basis=basis,
        )

    # Always report the original user-supplied input as `input_file`, even if
    # the Hessian was actually computed on the optimized geometry. The opt
    # block records the path to the optimized xyz for transparency.
    result["input_file"] = os.path.abspath(input_path)
    if confsearch_info is not None:
        result["auto_confsearch"] = confsearch_info
    if preopt_info:
        result["preopt"] = preopt_info
        # Surface a warning if the pre-opt didn't converge AND the Hessian
        # came back with imaginary modes. If the Hessian is clean (no imag
        # modes), the residual gradient was small enough that the resulting
        # thermochemistry is trustworthy regardless of the strict fmax target.
        if not preopt_info["converged"] and (result.get("n_imaginary_modes") or 0) > 0:
            warns = result.setdefault("warnings", [])
            warns.append(
                "Pre-optimization did NOT converge to fmax="
                f"{effective_fmax} eV/Å. Frequencies are taken at a non-stationary "
                "point; imaginary modes may be spurious."
            )
    else:
        result["preopt"] = {"performed": False}
    return result


def _run_ase(
    *, input_path, atoms, symbols, method, charge, multiplicity, solvent,
    temperature_K, pressure_Pa, geometry, symmetrynumber, cli,
    tier=None, functional=None, basis=None,
) -> Dict[str, Any]:
    from ase.thermochemistry import IdealGasThermo
    from ase.vibrations import Vibrations

    calc = build_calculator(
        method, charge=charge, multiplicity=multiplicity, solvent=solvent,
        tier=tier, functional=functional, basis=basis,
    )
    apply_calc_to_atoms(atoms, calc)

    workdir = tempfile.mkdtemp(prefix="chemkit_freq_")
    cache = os.path.join(workdir, "vib")

    energy_eV = atoms.get_potential_energy()
    # nfree=4 (5-point central difference) + delta=0.005 Å cuts finite-
    # difference noise in the Hessian by ~10x vs ASE defaults (nfree=2,
    # delta=0.01).
    vib = Vibrations(atoms, name=cache, delta=0.005, nfree=4)
    vib.run()
    # ASE's default Vibrations diagonalizes the raw 3N x 3N mass-weighted
    # Hessian, which mixes the 5/6 translational+rotational pseudo-modes
    # into the vibrational subspace whenever the geometry isn't exactly
    # at a stationary point. Symptom: small rigid molecules (H2O, NO3-,
    # H2O2) report a handful of large "imaginary" modes that are really
    # rot/trans leakage, NOT genuine saddle-point directions.
    #
    # Fix: pull the raw 3N x 3N Hessian, project out the trans+rot
    # subspace (Eckart conditions), then diagonalize only the vibrational
    # complement — gives exactly 3N-6 (or 3N-5 for linear) genuine modes.
    energies_eV, frequencies_cm = _project_trans_rot_and_diagonalize(vib, atoms, geometry)
    vib.clean()

    # ASE Vibrations returns 3N modes — the 6 (or 5, linear) translational/
    # rotational ones show up as tiny near-zero values (often slightly
    # imaginary due to finite-difference noise). Filter them out of the
    # vibrational set so IdealGasThermo only sees genuine (3N-6 or 3N-5)
    # vibrations — otherwise the harmonic-oscillator entropy diverges on
    # the near-zero "modes" and G blows up to ±inf (most visible for small
    # linear diatomics where 5/6 of the modes are leftover rot/trans).
    # The Eckart-projected diagonalization yields the right number of
    # vibrational modes (3N-6 or 3N-5) with the projected-out trans/rot
    # entries padded as zeros at the front of the array. Strip the zero pad,
    # then raise any remaining tiny soft-modes up to a floor frequency
    # (Truhlar's quasi-RRHO trick) — keeps the mode count correct and
    # prevents the harmonic-oscillator entropy from diverging on near-zero
    # torsions. (Dropping the modes outright crashes ASE's IdealGasThermo
    # for any flexible organic with a low-frequency conformer mode.)
    NEAR_ZERO_CM = 50.0
    EV_PER_CM = 1.239841984e-4
    FLOOR_eV = NEAR_ZERO_CM * EV_PER_CM
    real_vib_energies = []
    for e, f in zip(energies_eV, frequencies_cm):
        # Skip the projected-out trans/rot padding (energies are exactly 0).
        if np.isreal(e) and e.real == 0 and np.isreal(f) and f.real == 0:
            continue
        # Imaginary modes are not vibrations — counted separately.
        if np.iscomplex(f) and f.imag != 0:
            continue
        if not np.isreal(e):
            continue
        if e.real < FLOOR_eV:
            real_vib_energies.append(FLOOR_eV)   # raise soft modes to floor
        else:
            real_vib_energies.append(e.real)
    n_imag = sum(
        1 for f in frequencies_cm
        if np.iscomplex(f) and f.imag != 0 and abs(f.imag) > NEAR_ZERO_CM
    )

    thermo = IdealGasThermo(
        vib_energies=real_vib_energies,
        potentialenergy=energy_eV,
        atoms=atoms,
        geometry=geometry,
        symmetrynumber=symmetrynumber,
        spin=(multiplicity - 1) / 2.0,
    )
    zpe_eV = thermo.get_ZPE_correction()
    H_eV = thermo.get_enthalpy(temperature_K, verbose=False)
    S_eV_per_K = thermo.get_entropy(temperature_K, pressure_Pa, verbose=False)
    G_eV = thermo.get_gibbs_energy(temperature_K, pressure_Pa, verbose=False)

    result = base_result(
        task="vibrational_thermochemistry",
        method=method_label(method, calc),
        program=program_label(method),
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=symbols,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )
    result["electronic_energy_eV"] = energy_eV
    result["zpe_eV"] = zpe_eV
    result["zpe_kcal_mol"] = zpe_eV / KCAL_TO_EV
    result["enthalpy_eV"] = H_eV
    result["entropy_eV_per_K"] = S_eV_per_K
    result["gibbs_free_energy_eV"] = G_eV
    result["temperature_K"] = temperature_K
    result["pressure_Pa"] = pressure_Pa
    result["geometry"] = geometry
    result["symmetry_number"] = symmetrynumber
    result["n_real_vib_modes"] = len(real_vib_energies)
    result["n_imaginary_modes"] = n_imag
    result["vibrational_frequencies_cm-1"] = [
        (f.real if np.isreal(f) else -abs(f.imag)) for f in frequencies_cm
    ]

    warns = element_warnings(symbols, method)
    if n_imag > 0:
        warns.append(
            f"{n_imag} imaginary mode(s) detected — geometry is not a true minimum. "
            "Thermochemistry values are approximate; re-optimize and re-run."
        )
    if warns:
        result["warnings"] = warns
    return result


def _run_mopac(
    *, input_path, atoms, symbols, charge, multiplicity, solvent,
    temperature_K, pressure_Pa, cli,
) -> Dict[str, Any]:
    """Drive MOPAC's native FORCE+THERMO calculation."""
    mopac_exe = shutil.which("mopac")
    if mopac_exe is None:
        raise FileNotFoundError("mopac executable not found in PATH.")

    workdir = tempfile.mkdtemp(prefix="chemkit_mopac_freq_")
    mop_path = os.path.join(workdir, "mopac.mop")
    out_path = os.path.join(workdir, "mopac.out")

    keywords = _mopac_freq_keywords(
        charge=charge, multiplicity=multiplicity, solvent=solvent,
        temperature_K=temperature_K,
    )
    _write_mopac_input(mop_path, keywords, symbols, atoms.get_positions())

    proc = subprocess.run(
        [mopac_exe, "mopac.mop"],
        cwd=workdir, capture_output=True, text=True, timeout=1800,
    )

    if not os.path.isfile(out_path):
        raise RuntimeError(
            f"mopac did not produce {out_path}.\n"
            f"stdout: {proc.stdout[-1000:]}\nstderr: {proc.stderr[-1000:]}"
        )

    force = parse_mopac_force(workdir)
    # A monatomic species has zero vibrational modes by definition (3N-6 < 0) —
    # an empty frequency list there is correct, not a parse failure.
    if not force.get("vibrational_frequencies_cm-1") and len(atoms) > 1:
        with open(out_path) as f:
            out_text = f.read()
        raise RuntimeError(
            "Failed to parse MOPAC FORCE output — no frequencies found.\n"
            f"Last 1500 chars of .out:\n{out_text[-1500:]}"
        )

    # Pull all the other usual MOPAC extras (HOMO/LUMO, dipole, IP, ENPART).
    extras = parse_mopac_extras(workdir)

    result = base_result(
        task="vibrational_thermochemistry",
        method="PM7",
        program="mopac",
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=symbols,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )

    # Heat of formation at the input geometry (no thermal correction).
    hof_0 = force.get("heat_of_formation_kcal_mol") or extras.get("heat_of_formation_kcal_mol")
    if hof_0 is not None:
        result["heat_of_formation_kcal_mol"] = hof_0
        result["electronic_energy_eV"] = hof_0 * KCAL_TO_EV

    result["zpe_kcal_mol"] = force.get("zpe_kcal_mol")
    if force.get("zpe_kcal_mol") is not None:
        result["zpe_eV"] = force["zpe_kcal_mol"] * KCAL_TO_EV

    if "heat_of_formation_T_kcal_mol" in force:
        # H(T) at the requested temperature — already includes thermal corrections
        # (translation, rotation, vibration). MOPAC reports it as ΔHf(T).
        result["heat_of_formation_T_kcal_mol"] = force["heat_of_formation_T_kcal_mol"]
        result["enthalpy_kcal_mol"] = force["heat_of_formation_T_kcal_mol"]
        result["enthalpy_eV"] = force["heat_of_formation_T_kcal_mol"] * KCAL_TO_EV

    if "entropy_cal_K_mol" in force:
        S_cal = force["entropy_cal_K_mol"]
        result["entropy_cal_K_mol"] = S_cal
        result["entropy_eV_per_K"] = S_cal * CAL_TO_EV

    if "heat_capacity_cal_K_mol" in force:
        result["heat_capacity_cal_K_mol"] = force["heat_capacity_cal_K_mol"]

    if "gibbs_free_energy_of_formation_kcal_mol" in force:
        G_kcal = force["gibbs_free_energy_of_formation_kcal_mol"]
        result["gibbs_free_energy_kcal_mol"] = G_kcal
        result["gibbs_free_energy_eV"] = G_kcal * KCAL_TO_EV

    T_used = force.get("temperature_K") or temperature_K
    result["temperature_K"] = T_used
    result["pressure_Pa"] = pressure_Pa
    result["n_real_vib_modes"] = force.get("n_real_vib_modes")
    result["n_imaginary_modes"] = force.get("n_imaginary_modes")
    result["vibrational_frequencies_cm-1"] = force["vibrational_frequencies_cm-1"]
    result["mopac_keywords"] = keywords
    result["mopac_workdir"] = workdir

    # Code-specific extras (HOMO/LUMO, dipole, etc.)
    if extras:
        # Drop fields we already promoted to the top level
        extras = {k: v for k, v in extras.items()
                  if k not in {"heat_of_formation_kcal_mol"}}
        if extras:
            result["code_specific"] = extras

    warns = element_warnings(symbols, "mopac")
    n_imag = force.get("n_imaginary_modes") or 0
    if n_imag > 0:
        warns.append(
            f"{n_imag} imaginary mode(s) detected — geometry is not a true minimum. "
            "Thermochemistry values are approximate; re-optimize and re-run."
        )
    if warns:
        result["warnings"] = warns
    return result


def _mopac_freq_keywords(
    *, charge, multiplicity, solvent, temperature_K,
) -> List[str]:
    kw = ["PM7", "FORCE", "THERMO", "AUX", "LET", "GEO-OK"]
    if charge != 0:
        kw.append(f"CHARGE={charge}")
    if multiplicity > 1:
        names = {2: "DOUBLET", 3: "TRIPLET", 4: "QUARTET", 5: "QUINTET"}
        spin = names.get(multiplicity)
        if spin:
            kw.append(spin)
        kw.append("UHF")
    if solvent:
        eps = MOPAC_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(f"mopac: unknown solvent {solvent!r}")
        kw.append(f"EPS={eps}")
    # Set temperature range so THERMO emits values at the requested T.
    # MOPAC's THERMO defaults to a sweep starting at 200 K with 298 prepended.
    # ROT=1 (sigma=1) — user can override at the call site if needed.
    kw.append(f"ROT=1")
    kw.append("THREADS=1")
    return kw


def _write_mopac_input(path, keywords, symbols, positions):
    lines = [" ".join(keywords), "chemkit frequency analysis", ""]
    for sym, (x, y, z) in zip(symbols, positions):
        lines.append(f"{sym:<3s} {x:15.8f} 1 {y:15.8f} 1 {z:15.8f} 1")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# Conversion: sqrt(eV/(amu*A^2)) -> cm^-1   (factor 521.471... for ASE units)
# energy in eV, distance in A, mass in amu => frequency in sqrt(eV/(amu*A^2));
# multiply by sqrt(1.602e-19/(1.66e-27 * 1e-20))/(2*pi*c[cm/s]) ~= 521.4708 / (2*pi)
# ASE uses its own internal conversion (see ase.units), but for clarity we follow
# the same path as VibrationsData.get_frequencies internally.
def _project_trans_rot_and_diagonalize(vib, atoms, geometry):
    """Pull the 3N x 3N Hessian from ASE Vibrations, project out the trans/rot
    subspace (Eckart conditions at the current geometry), and diagonalize the
    remaining (3N-6 or 3N-5) vibrational subspace. Returns (energies_eV,
    frequencies_cm) arrays matching the shape expected by the rest of _run_ase.

    The arrays are padded back up to 3N with zeros at the front (so the calling
    code's "filter > NEAR_ZERO_CM" check still drops them naturally) — only the
    last 3N-6 (or 3N-5) entries are real vibrational data.
    """
    import numpy as np
    from ase import units

    vd = vib.get_vibrations()
    H = np.asarray(vd.get_hessian_2d())  # 3N x 3N, in eV/A^2
    n_atoms = len(atoms)
    pos = atoms.get_positions()
    masses = atoms.get_masses()

    # Mass-weight: H_mw[ia,jb] = H[ia,jb] / sqrt(m_i * m_j)
    sqm = np.sqrt(np.repeat(masses, 3))
    Hmw = H / np.outer(sqm, sqm)

    # Build trans/rot basis in mass-weighted Cartesian coords.
    # Translation: e_x, e_y, e_z replicated, weighted by sqrt(m_i).
    # Rotation:   r_i x e_alpha, also weighted by sqrt(m_i).
    N = n_atoms
    basis = []
    for alpha in range(3):
        v = np.zeros(3 * N)
        v[alpha::3] = np.sqrt(masses)
        basis.append(v)
    # Center of mass
    com = (masses[:, None] * pos).sum(0) / masses.sum()
    r = pos - com
    for alpha in range(3):
        v = np.zeros(3 * N)
        e = np.zeros(3); e[alpha] = 1.0
        # cross product r_i x e for each atom
        cross = np.cross(r, e)
        v = (np.sqrt(masses)[:, None] * cross).reshape(-1)
        basis.append(v)
    B = np.array(basis).T  # (3N, 6)

    # Orthonormalize (drop zero columns for linear molecules => 5 dof, not 6)
    # via SVD; columns with negligible singular values are degenerate.
    U, S, _ = np.linalg.svd(B, full_matrices=False)
    keep = S > 1e-6 * S.max()
    Q = U[:, keep]  # (3N, n_trans_rot), n_trans_rot is 5 (linear) or 6 (nonlinear)

    # Projector onto vibrational subspace: P = I - Q Q^T
    P = np.eye(3 * N) - Q @ Q.T
    Hproj = P @ Hmw @ P

    eigvals, _ = np.linalg.eigh(Hproj)
    # The (3N - n_tr) largest-magnitude eigenvalues are vibrational; the
    # remaining n_tr are numerical zeros from the projection.
    n_tr = Q.shape[1]
    # Sort eigenvalues ascending. The first n_tr are ~0 (projected-out modes).
    # The rest are real vibrations (positive for minima, negative for saddles).
    vib_eigvals = eigvals[n_tr:]

    # Convert eigenvalues -> angular frequency^2 (in units eV / (amu * A^2))
    # then -> energy (eV) and frequency (cm^-1).
    # ASE convention: omega [rad/s] = sqrt(eigval [eV/A^2/amu] * units._e / (units._amu * 1e-20))
    # energy_eV = hbar * omega = sqrt(eigval) * units._hbar * sqrt(units._e/(units._amu*1e-20))
    # Use the same conversion as ase.vibrations.Vibrations.get_energies:
    s = units._hbar * 1e10 / np.sqrt(units._e * units._amu)
    # s has units such that energy_eV = s * sqrt(eigval_in_eV/A^2/amu)
    def _ev_from_eig(ev):
        if ev >= 0:
            return s * np.sqrt(ev)
        else:
            return -1j * s * np.sqrt(-ev)

    energies_eV_vib = np.array([_ev_from_eig(ev) for ev in vib_eigvals], dtype=complex)
    # frequency cm^-1 = energy_eV / (h*c), with h*c in eV*cm = 1.239841984e-4
    HC_EV_CM = units._hplanck * units._c * units.J * units.m / 1e-2  # eV * cm
    # simpler: 1 cm^-1 = 1.239841984e-4 eV
    EV_TO_CM = 1.0 / 1.239841984e-4
    frequencies_cm_vib = np.array(
        [e.real * EV_TO_CM if e.imag == 0 else 1j * abs(e.imag) * EV_TO_CM
         for e in energies_eV_vib], dtype=complex
    )

    # Pad with n_tr zeros at the front so existing downstream code (which
    # filters > 50 cm^-1) drops them naturally and counts work out.
    pad_e = np.zeros(n_tr, dtype=complex)
    pad_f = np.zeros(n_tr, dtype=complex)
    energies_eV = np.concatenate([pad_e, energies_eV_vib])
    frequencies_cm = np.concatenate([pad_f, frequencies_cm_vib])
    return energies_eV, frequencies_cm
