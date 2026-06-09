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

from ..calculators import build_calculator, apply_calc_to_atoms, MOPAC_SOLVENT_EPS
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
    preopt_fmax: float = 0.01,
    preopt_steps: int = 500,
    cli: str = "",
) -> Dict[str, Any]:
    """Opt-freq pipeline. The frequency calculation is performed on the
    optimized geometry by default; pass preopt=False to skip the optimization
    step (useful when the caller is sure the input is already a stationary
    point and re-optimization would waste cycles).
    """
    method = method.lower()
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    preopt_info: Dict[str, Any] = {}
    freq_input_path = input_path

    if preopt:
        # Optimize first so the Hessian is taken at a true stationary point.
        # Use a tighter fmax than `opt` default (0.05) since residual forces
        # propagate into imaginary modes near 0 cm⁻¹.
        opt_work = tempfile.mkdtemp(prefix="chemkit_optfreq_")
        opt_xyz = os.path.join(opt_work, "preopt.xyz")
        opt_result = opt_task.run(
            input_path=input_path,
            method=method,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            fmax=preopt_fmax,
            steps=preopt_steps,
            out_xyz=opt_xyz,
            cli="(internal preopt for freq)",
        )
        # Reload atoms from the optimized xyz so the freq step works on the
        # exact geometry written to disk.
        atoms = read_geometry(opt_xyz)
        symbols = atoms.get_chemical_symbols()
        freq_input_path = opt_xyz
        preopt_info = {
            "performed": True,
            "fmax_target_eV_per_A": preopt_fmax,
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
        )

    # Always report the original user-supplied input as `input_file`, even if
    # the Hessian was actually computed on the optimized geometry. The opt
    # block records the path to the optimized xyz for transparency.
    result["input_file"] = os.path.abspath(input_path)
    if preopt_info:
        result["preopt"] = preopt_info
        # Surface a warning if the pre-opt didn't converge — the freq numbers
        # are still computed, but the user should know the input wasn't fully
        # relaxed before the Hessian was taken.
        if not preopt_info["converged"]:
            warns = result.setdefault("warnings", [])
            warns.append(
                "Pre-optimization did NOT converge to fmax="
                f"{preopt_fmax} eV/Å. Frequencies are taken at a non-stationary "
                "point; imaginary modes may be spurious."
            )
    else:
        result["preopt"] = {"performed": False}
    return result


def _run_ase(
    *, input_path, atoms, symbols, method, charge, multiplicity, solvent,
    temperature_K, pressure_Pa, geometry, symmetrynumber, cli,
) -> Dict[str, Any]:
    from ase.thermochemistry import IdealGasThermo
    from ase.vibrations import Vibrations

    calc = build_calculator(
        method, charge=charge, multiplicity=multiplicity, solvent=solvent
    )
    apply_calc_to_atoms(atoms, calc)

    workdir = tempfile.mkdtemp(prefix="chemkit_freq_")
    cache = os.path.join(workdir, "vib")

    energy_eV = atoms.get_potential_energy()
    vib = Vibrations(atoms, name=cache)
    vib.run()
    energies_eV = vib.get_energies()
    frequencies_cm = vib.get_frequencies()
    vib.clean()

    # ASE Vibrations returns 3N modes — the 6 translational/rotational ones
    # show up as tiny near-zero values (often slightly imaginary due to
    # finite-difference noise). Only flag a mode as a genuine imaginary
    # saddle if its magnitude exceeds the rot/trans noise floor.
    NEAR_ZERO_CM = 30.0
    real_vib_energies = [e.real for e in energies_eV if np.isreal(e) and e.real > 0]
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
        method="GFN2-xTB",
        program="xtb",
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
