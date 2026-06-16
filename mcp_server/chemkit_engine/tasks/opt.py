"""Geometry optimization task.

For xtb (GFN2): ASE's BFGS drives the optimization using xtb-python forces.
For mopac (PM7): MOPAC's native EF optimizer drives the optimization in a single
binary invocation. ASE/BFGS is bypassed for MOPAC because line searches starting
from chemically nonsensical geometries can step into atomic collisions whose
gradients overflow MOPAC's fixed-width force printout, which then breaks ASE's
output parser. MOPAC's own optimizer uses internal coordinates and handles such
inputs gracefully.
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from ..calculators import MOPAC_SOLVENT_EPS, mopac_spin_keyword
from ..io import read_geometry
from ..schema import (
    base_result,
    energy_block_from_eV,
    element_warnings,
)


KCAL_PER_MOL_TO_EV = 1.0 / 23.060547830619026  # eV per kcal/mol


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    fmax: float = 0.05,    # eV/Å
    steps: int = 500,
    out_xyz: Optional[str] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    method = method.lower()
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    if out_xyz is None:
        stem = os.path.splitext(os.path.basename(input_path))[0]
        out_xyz = os.path.abspath(f"{stem}_{method}_opt.xyz")

    if method == "mopac":
        result = _run_mopac(
            input_path=input_path,
            atoms=atoms,
            symbols=symbols,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            fmax=fmax,
            steps=steps,
            out_xyz=out_xyz,
            cli=cli,
        )
    else:
        result = _run_ase(
            input_path=input_path,
            atoms=atoms,
            symbols=symbols,
            method=method,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            fmax=fmax,
            steps=steps,
            out_xyz=out_xyz,
            cli=cli,
            tier=tier,
            functional=functional,
            basis=basis,
            density_fit=density_fit,
        )

    # The .xyz was already written inside the sub-path, so evidence is on disk
    # before the gate can raise.
    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def _run_ase(
    *, input_path, atoms, symbols, method, charge, multiplicity, solvent,
    fmax, steps, out_xyz, cli,
    tier=None, functional=None, basis=None, density_fit=False,
) -> Dict[str, Any]:
    from ase.io import write as ase_write
    from ase.optimize import BFGS
    from ..calculators import (
        build_calculator, apply_calc_to_atoms,
        method_label, program_label,
    )

    calc = build_calculator(
        method, charge=charge, multiplicity=multiplicity, solvent=solvent,
        tier=tier, functional=functional, basis=basis, density_fit=density_fit,
    )
    apply_calc_to_atoms(atoms, calc)

    dyn = BFGS(atoms, logfile=None)
    converged = dyn.run(fmax=fmax, steps=steps)
    final_energy = atoms.get_potential_energy()
    ase_write(out_xyz, atoms, format="xyz")

    result = base_result(
        task="geometry_optimization",
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
    result.update(energy_block_from_eV(final_energy))
    result["converged"] = bool(converged)
    result["n_steps"] = int(dyn.get_number_of_steps())
    result["fmax_target_eV_per_A"] = fmax
    result["optimized_xyz"] = out_xyz

    warns = element_warnings(symbols, method)
    if not converged:
        if len(symbols) <= 1:
            warns.append(
                "Single-atom (zero-DOF) system: no geometry to relax, so the "
                "optimizer reports converged=False vacuously; the energy is valid."
            )
        else:
            warns.append(f"Optimization did NOT converge within {steps} steps (fmax={fmax}).")
    if warns:
        result["warnings"] = warns
    return result


def _run_mopac(
    *, input_path, atoms, symbols, charge, multiplicity, solvent,
    fmax, steps, out_xyz, cli,
) -> Dict[str, Any]:
    mopac_exe = shutil.which("mopac")
    if mopac_exe is None:
        raise FileNotFoundError("mopac executable not found in PATH.")

    workdir = tempfile.mkdtemp(prefix="chemkit_mopac_opt_")
    mop_path = os.path.join(workdir, "mopac.mop")
    out_path = os.path.join(workdir, "mopac.out")
    arc_path = os.path.join(workdir, "mopac.arc")

    keywords = _mopac_opt_keywords(
        charge=charge, multiplicity=multiplicity, solvent=solvent,
        fmax=fmax, steps=steps,
    )
    _write_mopac_input(mop_path, keywords, symbols, atoms.get_positions())

    proc = subprocess.run(
        [mopac_exe, "mopac.mop"],
        cwd=workdir, capture_output=True, text=True, timeout=600,
    )

    if not os.path.isfile(out_path):
        raise RuntimeError(
            f"mopac did not produce {out_path}.\n"
            f"stdout: {proc.stdout[-1000:]}\nstderr: {proc.stderr[-1000:]}"
        )

    with open(out_path) as f:
        out_text = f.read()

    converged, conv_msg = _parse_mopac_convergence(out_text)
    hof_kcal = _parse_mopac_hof(out_text)
    grad_norm = _parse_mopac_gradient_norm(out_text)
    final_symbols, final_positions = _parse_mopac_final_geometry(arc_path, out_text)

    if final_symbols and final_positions:
        atoms.set_chemical_symbols(final_symbols)
        atoms.set_positions(final_positions)

    from ase.io import write as ase_write
    ase_write(out_xyz, atoms, format="xyz")

    energy_eV = (
        hof_kcal * KCAL_PER_MOL_TO_EV if hof_kcal is not None else float("nan")
    )

    result = base_result(
        task="geometry_optimization",
        method="PM7",
        program="mopac",
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=atoms.get_chemical_symbols(),
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )
    if hof_kcal is not None:
        result.update(energy_block_from_eV(energy_eV))
        result["final_heat_of_formation_kcal_mol"] = hof_kcal
    result["converged"] = bool(converged)
    result["fmax_target_eV_per_A"] = fmax
    if grad_norm is not None:
        result["mopac_gradient_norm_kcal_per_A"] = grad_norm
    if conv_msg:
        result["mopac_status"] = conv_msg
    result["optimized_xyz"] = out_xyz
    result["mopac_workdir"] = workdir
    result["mopac_keywords"] = keywords

    warns = element_warnings(symbols, "mopac")
    if not converged:
        if len(symbols) <= 1:
            warns.append(
                "Single-atom (zero-DOF) system: no geometry to relax, so MOPAC "
                "reports non-convergence vacuously; the energy is valid."
            )
        else:
            warns.append(
                f"MOPAC reported the optimization did NOT converge "
                f"({conv_msg or 'see mopac.out'}); final geometry returned anyway."
            )
    if hof_kcal is not None and abs(hof_kcal) > 10000:
        warns.append(
            f"Final heat of formation is extreme ({hof_kcal:.1f} kcal/mol). "
            "The optimizer may have settled into a non-physical (collapsed/exploded) "
            "geometry; inspect the optimized xyz before trusting the energy."
        )
    if warns:
        result["warnings"] = warns
    return result


def _mopac_opt_keywords(
    *, charge: int, multiplicity: int, solvent: Optional[str],
    fmax: float, steps: int,
) -> List[str]:
    # MOPAC default = full geometry optimization with EF. We give it loose
    # GNORM (gradient norm) target derived from fmax (which is in eV/Å). MOPAC's
    # GNORM is in kcal/(mol·Å); convert and convert per-atom-component fmax
    # roughly into a system gradient norm threshold by scaling by sqrt(3N).
    # 1 eV/Å ≈ 23.06 kcal/(mol·Å)
    gnorm = max(0.01, fmax * 23.060547830619026)
    kw = [
        "PM7",
        f"GNORM={gnorm:.3f}",
        "AUX",
        "GEO-OK",
    ]
    if charge != 0:
        kw.append(f"CHARGE={charge}")
    if multiplicity > 1:
        kw.append(mopac_spin_keyword(multiplicity))
        kw.append("UHF")
    if solvent:
        eps = MOPAC_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(f"mopac: unknown solvent {solvent!r}")
        kw.append(f"EPS={eps}")
    kw.append("THREADS=1")
    return kw


def _write_mopac_input(
    path: str, keywords: List[str], symbols: List[str], positions,
) -> None:
    lines = [" ".join(keywords), "chemkit geometry optimization", ""]
    for sym, (x, y, z) in zip(symbols, positions):
        lines.append(
            f"{sym:<3s} {x:15.8f} 1 {y:15.8f} 1 {z:15.8f} 1"
        )
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


_HOF_RE = re.compile(
    r"FINAL HEAT OF FORMATION\s*=\s*(-?\d+\.\d+)\s*KCAL"
)
_GNORM_RE = re.compile(
    r"GRADIENT NORM\s*=\s*(-?[\d.]+(?:[eE][+-]?\d+)?)"
)


def _parse_mopac_hof(text: str) -> Optional[float]:
    matches = _HOF_RE.findall(text)
    return float(matches[-1]) if matches else None


def _parse_mopac_gradient_norm(text: str) -> Optional[float]:
    matches = _GNORM_RE.findall(text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def _parse_mopac_convergence(text: str) -> Tuple[bool, Optional[str]]:
    # Success markers MOPAC prints when EF/BFGS reach the gradient target.
    if "GRADIENT TEST PASSED" in text:
        return True, "GRADIENT TEST PASSED"
    # SCF-only success isn't enough — we want geometry to be converged.
    if "GEOMETRY OPTIMISED" in text or "GEOMETRY OPTIMIZED" in text:
        # Check for explicit failure annotations alongside.
        if "HERBERTS TEST" in text or "TRUST RADIUS NOW LESS" in text:
            return False, "EF terminated abnormally (trust radius collapsed)."
        return True, "EF reported geometry optimised."
    if "EXCESS NUMBER OF OPTIMIZATION CYCLES" in text:
        return False, "Exceeded CYCLES limit."
    if "HEAT OF FORMATION IS UNCHANGED" in text:
        return False, "EF stalled (HoF unchanged for several cycles)."
    if "GEOMETRY IS NOT CONVERGED" in text:
        return False, "MOPAC reported geometry not converged."
    # Fall back: assume not converged if we can't find a positive marker.
    return False, None


def _parse_mopac_final_geometry(
    arc_path: str, out_text: str,
) -> Tuple[List[str], List[Tuple[float, float, float]]]:
    """Prefer the .arc 'FINAL GEOMETRY OBTAINED' block; fall back to .out."""
    if os.path.isfile(arc_path):
        with open(arc_path) as f:
            arc_text = f.read()
        syms, pos = _extract_arc_geometry(arc_text)
        if syms:
            return syms, pos
    return _extract_out_geometry(out_text)


_ARC_ATOM_RE = re.compile(
    r"^\s*([A-Z][a-z]?)"
    r"\s+(-?\d+\.\d+)\s*[+\-]?\d?"
    r"\s+(-?\d+\.\d+)\s*[+\-]?\d?"
    r"\s+(-?\d+\.\d+)\s*[+\-]?\d?",
    re.MULTILINE,
)


def _extract_arc_geometry(text: str):
    marker = "FINAL GEOMETRY OBTAINED"
    idx = text.find(marker)
    if idx < 0:
        return [], []
    block = text[idx:]
    syms, pos = [], []
    for m in _ARC_ATOM_RE.finditer(block):
        sym = m.group(1)
        if sym in ("PM", "PM7"):  # skip the keyword line accidentally matched
            continue
        syms.append(sym)
        pos.append((float(m.group(2)), float(m.group(3)), float(m.group(4))))
    return syms, pos


def _extract_out_geometry(text: str):
    # Find the LAST "CARTESIAN COORDINATES" block in the .out file.
    blocks = list(re.finditer(r"CARTESIAN COORDINATES", text))
    if not blocks:
        return [], []
    start = blocks[-1].end()
    tail = text[start:start + 8000]
    syms, pos = [], []
    for line in tail.splitlines():
        m = re.match(
            r"\s*\d+\s+([A-Z][a-z]?)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)",
            line,
        )
        if m:
            syms.append(m.group(1))
            pos.append((float(m.group(2)), float(m.group(3)), float(m.group(4))))
        elif syms and line.strip() == "":
            if len(syms) > 0:
                break
    return syms, pos
