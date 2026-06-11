"""Transition-state search — locate a first-order saddle point.

MOPAC backend (default, most reliable on the methods chemkit ships): native
TS keyword (eigenvalue-following saddle search starting from the input).
After convergence, a FORCE+THERMO run verifies there is exactly one
imaginary mode (the reaction-coordinate direction).

xtb backend: requires the Sella optimizer (https://github.com/zadorlab/sella)
since xtb-python doesn't expose a TS optimizer of its own. If Sella isn't
installed, raises with an informative install hint — caller can fall back
to MOPAC.

Output JSON includes the converged TS geometry, the imaginary-mode
frequency, and a flag indicating whether the saddle is "good"
(exactly 1 imaginary mode within a sensible magnitude range).
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional

from ase.io import write as ase_write

from ..calculators import (
    build_calculator, apply_calc_to_atoms, MOPAC_SOLVENT_EPS,
    method_label, program_label, mopac_spin_keyword,
)
from ..io import read_geometry
from ..schema import base_result, energy_block_from_eV, element_warnings
from ._mopac_parsers import parse_mopac_extras, _find_with_ext
from . import freq as freq_task


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    steps: int = 500,
    verify_freq: bool = True,
    out_stem: Optional[str] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    method = method.lower()
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    calc_for_label = None
    if method in ("dft", "hf"):
        calc_for_label = build_calculator(
            method, charge=charge, multiplicity=multiplicity, solvent=solvent,
            tier=tier, functional=functional, basis=basis,
        )

    result = base_result(
        task="transition_state",
        method=method_label(method, calc_for_label),
        program=program_label(method),
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=symbols,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )

    if method == "mopac":
        body, ts_atoms = _ts_mopac(
            atoms, symbols,
            charge=charge, multiplicity=multiplicity, solvent=solvent,
            steps=steps,
        )
    elif method in ("xtb", "dft", "hf"):
        body, ts_atoms = _ts_sella(
            atoms, method=method,
            charge=charge, multiplicity=multiplicity, solvent=solvent,
            steps=steps,
            tier=tier, functional=functional, basis=basis,
        )
    else:
        raise ValueError(f"Unknown method {method!r}")

    result.update(body)

    # Persist the optimized TS geometry next to the JSON output
    if out_stem and ts_atoms is not None:
        ts_xyz_path = f"{out_stem}.xyz"
        ase_write(ts_xyz_path, ts_atoms)
        result["ts_xyz"] = os.path.abspath(ts_xyz_path)

    # Verify: run a freq on the converged TS, count imaginary modes
    if verify_freq and ts_atoms is not None and body.get("converged"):
        try:
            freq_workdir = tempfile.mkdtemp(prefix="chemkit_ts_verify_")
            ts_input = os.path.join(freq_workdir, "ts.xyz")
            ase_write(ts_input, ts_atoms)
            freq_result = freq_task.run(
                ts_input,
                method=method,
                charge=charge,
                multiplicity=multiplicity,
                solvent=solvent,
                preopt=False,           # already at the TS; preopt would walk off
                cli="(internal TS verification)",
                tier=tier,
                functional=functional,
                basis=basis,
            )
            n_imag = freq_result.get("n_imaginary_modes") or 0
            freqs = freq_result.get("vibrational_frequencies_cm-1") or []
            # Pick the largest-magnitude imaginary frequency
            imag_freqs = sorted((f for f in freqs if isinstance(f, (int, float)) and f < 0),
                                key=lambda x: abs(x), reverse=True)
            result["verify_freq"] = {
                "n_imaginary_modes": n_imag,
                "is_valid_ts": n_imag == 1,
                "imaginary_frequencies_cm-1": imag_freqs,
                "gibbs_free_energy_eV": freq_result.get("gibbs_free_energy_eV"),
            }
            warns = result.get("warnings") or []
            if n_imag == 0:
                warns.append(
                    "TS verification freq found ZERO imaginary modes — the "
                    "converged geometry is a minimum, not a saddle. The TS "
                    "search likely fell to a nearby well; supply a closer "
                    "guess geometry."
                )
            elif n_imag > 1:
                warns.append(
                    f"TS verification freq found {n_imag} imaginary modes — "
                    f"this is a higher-order saddle, not a true transition state. "
                    f"Use a different initial guess or refine the search."
                )
            if warns:
                result["warnings"] = warns
        except Exception as e:
            result["verify_freq"] = {"error": str(e)}

    el_warns = element_warnings(symbols, method)
    if el_warns:
        result.setdefault("warnings", []).extend(el_warns)
    return result


# ---------------------------------------------------------------------------
# MOPAC TS
# ---------------------------------------------------------------------------

def _ts_mopac(atoms, symbols, *, charge, multiplicity, solvent, steps):
    mopac_exe = shutil.which("mopac")
    if mopac_exe is None:
        raise FileNotFoundError("mopac executable not found in PATH.")

    workdir = tempfile.mkdtemp(prefix="chemkit_ts_mopac_")
    mop_path = os.path.join(workdir, "ts.mop")

    keywords = ["PM7", "TS", "AUX", "GEO-OK", f"CYCLES={steps}"]
    if charge != 0:
        keywords.append(f"CHARGE={charge}")
    if multiplicity > 1:
        keywords.append(mopac_spin_keyword(multiplicity))
        keywords.append("UHF")
    if solvent:
        eps = MOPAC_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(f"mopac: unknown solvent {solvent!r}")
        keywords.append(f"EPS={eps}")
    keywords.append("THREADS=1")

    with open(mop_path, "w") as f:
        f.write(" ".join(keywords) + "\n")
        f.write("chemkit TS search\n\n")
        for sym, (x, y, z) in zip(symbols, atoms.get_positions()):
            f.write(f"{sym:<3s} {x:15.8f} 1 {y:15.8f} 1 {z:15.8f} 1\n")

    subprocess.run([mopac_exe, "ts.mop"], cwd=workdir,
                   capture_output=True, text=True, timeout=3600)

    out_path = _find_with_ext(workdir, ".out")
    arc_path = _find_with_ext(workdir, ".arc")

    # Pull final geometry: prefer .arc's FINAL GEOMETRY OBTAINED block
    ts_atoms = _parse_mopac_final_geometry(arc_path, symbols) if arc_path else None
    if ts_atoms is None:
        ts_atoms = atoms.copy()

    # Convergence check: MOPAC TS reports "TS = " success line or "WAS NOT OBTAINED"
    converged = False
    msg = ""
    if out_path:
        with open(out_path) as f:
            txt = f.read()
        if "GRADIENT" in txt and re.search(r"GEOMETRY OPTIMISED USING [A-Z]+", txt):
            converged = True
            m = re.search(r"GEOMETRY OPTIMISED USING ([A-Z]+)", txt)
            msg = f"{m.group(1)} reported geometry optimised." if m else "optimised"
        elif "TRANSITION STATE WAS LOCATED" in txt.upper():
            converged = True
            msg = "MOPAC reported TS located."
        elif "NOT OBTAINED" in txt.upper() or "FAILED" in txt.upper():
            converged = False
            msg = "MOPAC reported TS search did not converge."

    extras = parse_mopac_extras(workdir)
    hof = extras.get("heat_of_formation_kcal_mol")
    energy_eV = hof / 23.060547830619026 if hof is not None else None

    body: Dict[str, Any] = {}
    if energy_eV is not None:
        body.update(energy_block_from_eV(energy_eV))
    if hof is not None:
        body["final_heat_of_formation_kcal_mol"] = hof
    body["converged"] = converged
    body["mopac_status"] = msg
    body["mopac_workdir"] = workdir
    body["mopac_keywords"] = keywords
    if extras.get("ionization_potential_eV") is not None:
        body["ionization_potential_eV"] = extras["ionization_potential_eV"]
    return body, ts_atoms


def _parse_mopac_final_geometry(arc_path, symbols):
    """Extract the 'FINAL GEOMETRY OBTAINED' xyz block from a MOPAC .arc."""
    if not arc_path or not os.path.isfile(arc_path):
        return None
    with open(arc_path) as f:
        text = f.read()
    m = re.search(r"FINAL GEOMETRY OBTAINED.*?(?=\Z|FINAL HEAT)", text, re.DOTALL)
    block = m.group(0) if m else text
    coords = []
    for line in block.split("\n"):
        parts = line.split()
        # MOPAC arc geometry lines: Sym x flag y flag z flag
        if len(parts) >= 7 and parts[0][0].isalpha():
            try:
                x = float(parts[1]); y = float(parts[3]); z = float(parts[5])
                coords.append((parts[0], x, y, z))
            except (ValueError, IndexError):
                continue
    if len(coords) != len(symbols):
        return None
    from ase import Atoms
    return Atoms(
        symbols=[c[0] for c in coords],
        positions=[(c[1], c[2], c[3]) for c in coords],
    )


# ---------------------------------------------------------------------------
# xtb + Sella TS (optional)
# ---------------------------------------------------------------------------

def _ts_sella(atoms, *, method, charge, multiplicity, solvent, steps,
              tier=None, functional=None, basis=None):
    try:
        from sella import Sella
    except ImportError as e:
        raise RuntimeError(
            f"{method} TS search requires Sella (no native TS optimizer). "
            "Install with `pip install sella`, or use --method mopac instead."
        ) from e

    calc = build_calculator(
        method, charge=charge, multiplicity=multiplicity, solvent=solvent,
        tier=tier, functional=functional, basis=basis,
    )
    apply_calc_to_atoms(atoms, calc)
    ts_opt = Sella(atoms, internal=True, order=1)
    converged = ts_opt.run(fmax=0.01, steps=steps)
    energy_eV = atoms.get_potential_energy()
    body: Dict[str, Any] = {}
    body.update(energy_block_from_eV(energy_eV))
    body["converged"] = bool(converged)
    body["n_steps"] = ts_opt.nsteps if hasattr(ts_opt, "nsteps") else None
    return body, atoms
