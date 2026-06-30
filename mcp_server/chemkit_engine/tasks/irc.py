"""Intrinsic Reaction Coordinate — walk down the gradient from a TS in both
directions along the reaction-coordinate (imaginary-frequency) mode.

Confirms which reactant and product the TS connects.

MOPAC backend: native IRC=1 keyword (forward + reverse from a TS geometry).

xtb backend: simple Python steepest-descent on mass-weighted Cartesian
coordinates, seeded by the eigenvector of the imaginary mode pulled from
ASE's Vibrations (with the Eckart-frame projection we use in freq.py).
Not as polished as MOPAC's IRC but works for small systems.

Output:
  - <stem>_forward.xyz, <stem>_reverse.xyz: trajectories (concatenated xyz)
  - JSON summary with endpoint energies and a flag indicating whether the
    forward/reverse endpoints look distinct (different from the TS by a
    meaningful energy difference).
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..calculators import (build_calculator, apply_calc_to_atoms, MOPAC_SOLVENT_EPS,
                            mopac_spin_keyword, register_auto_tempdir,
                            resolve_dielectric)
from ..io import read_geometry
from ..schema import base_result, element_warnings, EV_TO_KCAL
from ._mopac_parsers import _find_with_ext


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    max_points: int = 40,
    step: float = 0.05,
    out_stem: Optional[str] = None,
    cli: str = "",
    # Accepted for CLI uniformity; IRC walks are xtb/mopac only today.
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    method = method.lower()
    if method in ("dft", "hf"):
        raise NotImplementedError(
            f"chemkit irc does not yet support --method {method}. Use xtb or "
            "mopac for the IRC walk; you can re-optimize the endpoints with "
            "--method dft/hf afterwards."
        )
    del tier, functional, basis, density_fit, solvent_model  # silenced; no PySCF route yet
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    result = base_result(
        task="intrinsic_reaction_coordinate",
        method=("GFN2-xTB" if method == "xtb" else "PM7"),
        program=method,
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=symbols,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )

    if method == "mopac":
        body, fwd_traj, rev_traj = _irc_mopac(
            atoms, symbols,
            charge=charge, multiplicity=multiplicity, solvent=solvent,
            max_points=max_points,
        )
    elif method == "xtb":
        body, fwd_traj, rev_traj = _irc_xtb(
            atoms, charge=charge, multiplicity=multiplicity, solvent=solvent,
            max_points=max_points, step=step,
        )
    else:
        raise ValueError(f"Unknown method {method!r}")

    result.update(body)

    if out_stem:
        fwd_xyz = f"{out_stem}_forward.xyz"
        rev_xyz = f"{out_stem}_reverse.xyz"
        if fwd_traj:
            _write_trajectory(fwd_xyz, fwd_traj)
            result["forward_trajectory"] = os.path.abspath(fwd_xyz)
        if rev_traj:
            _write_trajectory(rev_xyz, rev_traj)
            result["reverse_trajectory"] = os.path.abspath(rev_xyz)

    el_warns = element_warnings(symbols, method)
    if el_warns:
        result.setdefault("warnings", []).extend(el_warns)

    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def _write_trajectory(path: str, traj: List[Tuple[List[str], np.ndarray, float]]):
    """traj is list of (symbols, positions_A, energy_eV)."""
    with open(path, "w") as f:
        for i, (syms, pos, e) in enumerate(traj):
            f.write(f"{len(syms)}\n")
            f.write(f"frame {i}  E = {e:.6f} eV\n")
            for s, (x, y, z) in zip(syms, pos):
                f.write(f"{s:<3s} {x:15.8f} {y:15.8f} {z:15.8f}\n")


# ---------------------------------------------------------------------------
# MOPAC IRC
# ---------------------------------------------------------------------------

def _irc_mopac(atoms, symbols, *, charge, multiplicity, solvent, max_points):
    mopac_exe = shutil.which("mopac")
    if mopac_exe is None:
        raise FileNotFoundError("mopac executable not found in PATH.")

    fwd_traj, fwd_msg = _run_one_irc_direction(
        atoms, symbols, +1, charge, multiplicity, solvent, max_points,
    )
    rev_traj, rev_msg = _run_one_irc_direction(
        atoms, symbols, -1, charge, multiplicity, solvent, max_points,
    )

    body: Dict[str, Any] = {}
    if fwd_traj:
        body["forward_endpoint_energy_eV"] = fwd_traj[-1][2]
        body["forward_n_points"] = len(fwd_traj)
    if rev_traj:
        body["reverse_endpoint_energy_eV"] = rev_traj[-1][2]
        body["reverse_n_points"] = len(rev_traj)
    # TS energy = first frame of either trajectory (both start at the TS)
    ts_e = None
    for t in (fwd_traj, rev_traj):
        if t:
            ts_e = t[0][2]
            break
    if ts_e is not None:
        body["ts_energy_eV"] = ts_e
        if fwd_traj:
            body["forward_drop_kcal_mol"] = (fwd_traj[-1][2] - ts_e) * EV_TO_KCAL
        if rev_traj:
            body["reverse_drop_kcal_mol"] = (rev_traj[-1][2] - ts_e) * EV_TO_KCAL

    body["forward_status"] = fwd_msg
    body["reverse_status"] = rev_msg
    body["distinct_endpoints"] = (
        bool(fwd_traj and rev_traj) and
        abs(fwd_traj[-1][2] - rev_traj[-1][2]) > 0.01  # > 0.01 eV ≈ 0.23 kcal/mol
    )
    return body, fwd_traj, rev_traj


def _run_one_irc_direction(atoms, symbols, direction, charge, multiplicity,
                            solvent, max_points):
    """direction = +1 (forward) or -1 (reverse)."""
    mopac_exe = shutil.which("mopac")
    workdir = tempfile.mkdtemp(prefix=f"chemkit_irc_{('fwd' if direction>0 else 'rev')}_")
    mop_path = os.path.join(workdir, "irc.mop")

    # MOPAC IRC=N runs the IRC in direction N (+1 forward, -1 reverse).
    # Trailing '*' is NOT valid syntax — that turns IRC=N into a different keyword.
    irc_key = f"IRC={direction}"
    keywords = ["PM7", irc_key, "AUX", "GEO-OK", "X-PRIORITY=0.0"]
    if charge != 0:
        keywords.append(f"CHARGE={charge}")
    if multiplicity > 1:
        keywords.append(mopac_spin_keyword(multiplicity))
        keywords.append("UHF")
    if solvent:
        eps = resolve_dielectric(solvent, MOPAC_SOLVENT_EPS, backend="mopac")
        keywords.append(f"EPS={eps}")
    keywords += ["THREADS=1", "T=3600"]

    with open(mop_path, "w") as f:
        f.write(" ".join(keywords) + "\n")
        f.write(f"chemkit IRC {'forward' if direction>0 else 'reverse'}\n\n")
        for sym, (x, y, z) in zip(symbols, atoms.get_positions()):
            f.write(f"{sym:<3s} {x:15.8f} 1 {y:15.8f} 1 {z:15.8f} 1\n")

    subprocess.run([mopac_exe, "irc.mop"], cwd=workdir,
                   capture_output=True, text=True, timeout=3600)

    out_path = _find_with_ext(workdir, ".out")
    aux_path = _find_with_ext(workdir, ".aux")
    traj = _parse_mopac_irc_trajectory(out_path, aux_path, symbols, max_points)

    msg = "no .out file"
    if out_path:
        with open(out_path) as f:
            text = f.read()
        if "JOB ENDED NORMALLY" in text:
            msg = "JOB ENDED NORMALLY"
        elif "WAS NOT OBTAINED" in text.upper():
            msg = "IRC did not converge"
        else:
            msg = "completed (status unclear)"
    return traj, msg


def _parse_mopac_irc_trajectory(out_path, aux_path, symbols, max_points):
    """Pull (geom, energy_kcal_mol) frames out of a MOPAC IRC run.

    MOPAC writes the trajectory to a separate `irc.xyz` (or `<job>.xyz`) file
    alongside the .out, as a concatenated multi-frame xyz with each frame's
    comment line of the form 'Profile. N HEAT OF FORMATION = X KCAL'. Read
    that file directly — much more robust than parsing the .out tabular
    output, which varies by MOPAC build.

    Returns list of (symbols_list, positions_Angstrom_np, energy_eV).
    """
    if not out_path:
        return []
    workdir = os.path.dirname(out_path)
    # MOPAC's trajectory xyz lives alongside the .out with the same stem
    stem = os.path.splitext(os.path.basename(out_path))[0]
    xyz_path = os.path.join(workdir, f"{stem}.xyz")
    if not os.path.isfile(xyz_path):
        return []

    n = len(symbols)
    frames: List[Tuple[List[str], np.ndarray, float]] = []
    with open(xyz_path) as f:
        lines = f.readlines()
    i = 0
    while i < len(lines) and len(frames) < max_points:
        # Expect: atom count, comment, n atom lines
        try:
            count = int(lines[i].split()[0])
        except (ValueError, IndexError):
            i += 1
            continue
        if count != n:
            i += 1
            continue
        comment = lines[i + 1] if i + 1 < len(lines) else ""
        e_match = re.search(r"HEAT OF FORMATION\s*=\s*([-+]?\d+\.\d+)", comment)
        if e_match is None:
            i += 2 + count
            continue
        hof = float(e_match.group(1))
        coords = np.zeros((n, 3))
        ok = True
        for j in range(n):
            line = lines[i + 2 + j].split()
            if len(line) < 4:
                ok = False
                break
            try:
                coords[j] = [float(line[1]), float(line[2]), float(line[3])]
            except ValueError:
                ok = False
                break
        if ok:
            frames.append((list(symbols), coords, hof / EV_TO_KCAL))
        i += 2 + count
    return frames


# ---------------------------------------------------------------------------
# xtb IRC (steepest descent on mass-weighted coords)
# ---------------------------------------------------------------------------

def _irc_xtb(atoms, *, charge, multiplicity, solvent, max_points, step):
    """Find the imaginary-mode eigenvector at the TS, then walk down the
    mass-weighted gradient in both directions from initial displacements
    ±step along that eigenvector."""
    # 1) Get the imaginary mode eigenvector via the same Eckart-projected
    #    Hessian we use in freq.
    eigvec_mw = _imag_mode_eigenvector_xtb(atoms, charge, multiplicity, solvent)
    masses = atoms.get_masses()
    sqm = np.sqrt(np.repeat(masses, 3))

    # 2) Convert mass-weighted eigenvector back to Cartesian displacement
    cart_disp = (eigvec_mw / sqm).reshape(-1, 3)
    # Normalize so initial step has the requested magnitude in A
    cart_disp /= np.linalg.norm(cart_disp)

    fwd_traj = _xtb_descend(
        atoms, +cart_disp, charge, multiplicity, solvent, max_points, step,
    )
    rev_traj = _xtb_descend(
        atoms, -cart_disp, charge, multiplicity, solvent, max_points, step,
    )

    body: Dict[str, Any] = {}
    if fwd_traj:
        body["forward_endpoint_energy_eV"] = fwd_traj[-1][2]
        body["forward_n_points"] = len(fwd_traj)
    if rev_traj:
        body["reverse_endpoint_energy_eV"] = rev_traj[-1][2]
        body["reverse_n_points"] = len(rev_traj)
    if fwd_traj and rev_traj:
        ts_e = fwd_traj[0][2]
        body["ts_energy_eV"] = ts_e
        body["forward_drop_kcal_mol"] = (fwd_traj[-1][2] - ts_e) * EV_TO_KCAL
        body["reverse_drop_kcal_mol"] = (rev_traj[-1][2] - ts_e) * EV_TO_KCAL
        body["distinct_endpoints"] = abs(fwd_traj[-1][2] - rev_traj[-1][2]) > 0.01
    return body, fwd_traj, rev_traj


def _imag_mode_eigenvector_xtb(atoms, charge, multiplicity, solvent):
    """Compute the lowest-eigenvalue mode of the Eckart-projected Hessian at
    the input geometry (assumed to be a TS). Returns the mass-weighted
    eigenvector (length 3N).
    """
    from ase.vibrations import Vibrations

    calc = build_calculator(
        "xtb", charge=charge, multiplicity=multiplicity, solvent=solvent
    )
    apply_calc_to_atoms(atoms, calc)
    workdir = register_auto_tempdir(tempfile.mkdtemp(prefix="chemkit_irc_vib_"))
    vib = Vibrations(atoms, name=os.path.join(workdir, "vib"),
                     delta=0.005, nfree=4)
    vib.run()
    vd = vib.get_vibrations()
    H = np.asarray(vd.get_hessian_2d())  # eV/A^2

    n_atoms = len(atoms)
    masses = atoms.get_masses()
    sqm = np.sqrt(np.repeat(masses, 3))
    Hmw = H / np.outer(sqm, sqm)

    # Build trans/rot subspace (Eckart) and project out
    pos = atoms.get_positions()
    com = (masses[:, None] * pos).sum(0) / masses.sum()
    r = pos - com
    basis = []
    for alpha in range(3):
        v = np.zeros(3 * n_atoms)
        v[alpha::3] = np.sqrt(masses)
        basis.append(v)
    for alpha in range(3):
        e = np.zeros(3); e[alpha] = 1.0
        cross = np.cross(r, e)
        v = (np.sqrt(masses)[:, None] * cross).reshape(-1)
        basis.append(v)
    B = np.array(basis).T
    U, S, _ = np.linalg.svd(B, full_matrices=False)
    Q = U[:, S > 1e-6 * S.max()]
    P = np.eye(3 * n_atoms) - Q @ Q.T
    Hproj = P @ Hmw @ P
    eigvals, eigvecs = np.linalg.eigh(Hproj)
    vib.clean()
    # eigvals are ascending; first n_tr are projected-out zeros, next is lowest
    n_tr = Q.shape[1]
    # The reaction-coordinate mode = lowest genuine eigenvalue
    return eigvecs[:, n_tr]


def _xtb_descend(atoms, init_disp_A, charge, multiplicity, solvent,
                  max_points, step):
    """Simple gradient-following descent starting from atoms.positions
    displaced by init_disp_A * step. Stops when |grad| < threshold or
    max_points reached."""
    work = atoms.copy()
    work.set_positions(atoms.get_positions() + init_disp_A * step)
    calc = build_calculator(
        "xtb", charge=charge, multiplicity=multiplicity, solvent=solvent
    )
    apply_calc_to_atoms(work, calc)

    masses = work.get_masses()
    sqm = np.sqrt(np.repeat(masses, 3)).reshape(-1, 3)

    traj = []
    for i in range(max_points):
        try:
            e = work.get_potential_energy()
            f = work.get_forces()
        except Exception:
            break
        traj.append((list(work.get_chemical_symbols()),
                     work.get_positions().copy(),
                     float(e)))
        # Gradient norm in eV/A
        fnorm = np.linalg.norm(f)
        if fnorm < 0.01:   # converged
            break
        # Mass-weighted steepest descent step
        mw_grad = -f / sqm
        mw_grad /= np.linalg.norm(mw_grad)
        # Unweight and step
        cart_step = (mw_grad / sqm) * step
        work.set_positions(work.get_positions() + cart_step)
    return traj
