#!/usr/bin/env python3
"""solvation — self-contained assay skill.

ΔG_solv = E(solvated) − E(gas) at fixed geometry (electronic solvation free energy).

Owns its workflow; depends only on the shared `assay_core` physics
library (composites use sibling skills in-process via their task shims).
Runnable stand-alone via scripts/run.py.
"""
from __future__ import annotations

import os as _os, sys as _sys
# Ensure the repo root is importable so `from skills.<sibling>...` resolves when
# this file is run directly as a script (python skills/<pkg>/scripts/run.py),
# where only the script's own dir is on sys.path.
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

# Skill discovery manifest (read by assay_core.discovery / the MCP server).
SKILL_NAME = "solvation"      # kebab display name (matches SKILL.md frontmatter)
SUBCOMMAND = "solvation"      # engine subcommand this skill implements
import os
from typing import Any, Dict, Optional

from skills.single_point_energy.scripts import run as sp_task
from assay_core.calculators import program_label
from assay_core.io import read_geometry
from assay_core.integrity import finalize
from assay_core.schema import (
    base_result, EV_TO_HARTREE, EV_TO_KCAL, element_warnings,
    SINGLE_CONFORMER_WARNING,
)

_SCREENING_WARNINGS = [
    "Electronic ΔG_solv only — no cavitation, dispersion-repulsion, or thermal "
    "correction. This is an electronic-energy difference E(solv) − E(gas), not a "
    "thermodynamic free energy (no ZPE/entropy).",
    "Standard-state caveat: the value is NOT corrected to the conventional "
    "ΔG*_solv 1 M (gas) → 1 M (solution) state; the experimental tables you may "
    "compare against include a ~1.9 kcal/mol (RT ln 24.46) term this number "
    "omits. Do not compare directly to tabulated ΔG*_solv without adding it.",
    "Semi-empirical implicit solvation is screening-grade; ±2–3 kcal/mol typical.",
]


def run(
    input_path: str,
    *,
    method: str,
    solvent: str,
    charge: int = 0,
    multiplicity: int = 1,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Solvation free energy ΔG_solv = E(solvated) − E(gas) on the same geometry."""
    if not solvent:
        raise ValueError("solvation requires --solvent")

    gas = sp_task.run(input_path, method=method, charge=charge,
                      multiplicity=multiplicity, solvent=None, cli=cli,
                      tier=tier, functional=functional, basis=basis,
                      density_fit=density_fit,
                      solvent_model=solvent_model,
                      gate_integrity=False)
    solv = sp_task.run(input_path, method=method, charge=charge,
                       multiplicity=multiplicity, solvent=solvent, cli=cli,
                       tier=tier, functional=functional, basis=basis,
                       density_fit=density_fit,
                       solvent_model=solvent_model,
                       gate_integrity=False)

    delta_eV = solv["total_energy_eV"] - gas["total_energy_eV"]
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    result = base_result(
        task="solvation",
        method=gas["method"], program=program_label(method),
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms), atoms=symbols,
        charge=charge, multiplicity=multiplicity, solvent=solvent, cli=cli,
    )
    result["delta_G_solv_eV"] = delta_eV
    result["delta_G_solv_hartree"] = delta_eV * EV_TO_HARTREE
    result["delta_G_solv_kcal_mol"] = delta_eV * EV_TO_KCAL
    result["energy_gas_eV"] = gas["total_energy_eV"]
    result["energy_solv_eV"] = solv["total_energy_eV"]
    result["geometry_note"] = (
        "single-point on the supplied geometry; no separate gas/solvent opt"
    )

    warns = []
    if abs(delta_eV) < 1e-6:
        warns.append(
            "|ΔG_solv| ≈ 0 — implicit solvent may have been silently dropped "
            "by the calculator. Check that xtb-python / MOPAC accepted the "
            f"solvent={solvent!r} request."
        )
    warns += _SCREENING_WARNINGS
    warns.append(SINGLE_CONFORMER_WARNING)
    warns += element_warnings(symbols, method)
    result["warnings"] = warns

    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def build_parser():
    import argparse
    from assay_core import argkit
    p = argparse.ArgumentParser(prog="solvation",
        description="Electronic solvation free energy in an implicit solvent.")
    argkit._add_chem_options(p)
    return p


def _call_run(args, cli, pyscf_kwargs):
    if not args.solvent:
        raise ValueError("solvation requires --solvent (e.g. --solvent water)")
    return run(args.input, method=args.method, solvent=args.solvent,
               charge=args.charge, multiplicity=args.multiplicity, cli=cli,
               **pyscf_kwargs)


if __name__ == "__main__":
    from assay_core import argkit
    raise SystemExit(argkit.run_cli(build_parser(), run, task="solvation",
                                    call_run=_call_run))
