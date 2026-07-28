#!/usr/bin/env python3
"""single-point-energy — self-contained assay skill.

Computes the total electronic energy at a fixed geometry (plus frontier-orbital
data) with no relaxation. This is the FIRST skill converted to the inverted
architecture (DESIGN.md): it owns its whole workflow here and depends only on
the shared `assay_core` physics library. It is runnable stand-alone —

    python skills/single_point_energy/scripts/run.py --method xtb mol.xyz

— with the same level-of-theory gate, integrity gate, live `.out` log, and
input_configs.yaml persistence the MCP server path provides, because the
`__main__` below routes through the shared `assay_core.argkit.run_cli` spine.

The engine's `assay_core.tasks.sp` now re-exports `run` from here (a thin shim),
so the CLI / composite skills that still reference the task keep working with a
single copy of the physics.
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict, Optional

from assay_core import argkit
from assay_core.calculators import (
    build_calculator, apply_calc_to_atoms,
    method_label, program_label, collect_calc_extras,
)
from assay_core.io import read_geometry
from assay_core.integrity import finalize
from assay_core.schema import (
    base_result, energy_block_from_eV, element_warnings,
    scf_convergence_warnings,
)
from assay_core.constants import HARTREE_TO_EV, ANGSTROM_TO_BOHR

SKILL = "single-point-energy"   # kebab display name (matches SKILL.md frontmatter)
TASK = "sp"                      # engine subcommand / default-out label


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Single-point energy workflow (was assay_core/tasks/sp.py::run)."""
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()
    calc = build_calculator(
        method, charge=charge, multiplicity=multiplicity, solvent=solvent,
        tier=tier, functional=functional, basis=basis, density_fit=density_fit,
        solvent_model=solvent_model,
    )
    apply_calc_to_atoms(atoms, calc)

    energy_eV = atoms.get_potential_energy()

    # ASE's MOPAC calculator already returns the heat of formation (the canonical
    # PM7 observable), which is what chemists usually mean by "the energy" of a
    # semi-empirical calculation. Keep `total_energy_eV` aligned with that so
    # `sp` matches `opt`/`freq`. The absolute electronic energy (ETOT from
    # ENPART) is still available in code_specific.electronic_total_energy_eV.
    result = base_result(
        task="single_point",
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
    result.update(energy_block_from_eV(energy_eV))
    if method == "xtb":
        result["energy_zero"] = "isolated atoms at infinity (xtb)"
    elif method == "mopac":
        result["energy_zero"] = "elements in their standard states (PM7 heat of formation)"
    else:
        result["energy_zero"] = "electronic energy (bare nuclei + electrons)"

    # Pull code-specific extras (HOMO/LUMO, dipole, heat of formation, etc.).
    extras = collect_calc_extras(method, atoms, calc)
    if method == "mopac" and "heat_of_formation_kcal_mol" in extras:
        # Promote HoF to top level so the schema matches `opt` / `freq`.
        result["final_heat_of_formation_kcal_mol"] = extras["heat_of_formation_kcal_mol"]
    if extras:
        result["code_specific"] = extras

    warns = element_warnings(symbols, method)
    warns += scf_convergence_warnings(method, extras)
    if warns:
        result["warnings"] = warns

    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def build_parser() -> argparse.ArgumentParser:
    """Own argparse, composing the shared assay_core.argkit option builders so
    the choices=/normalizers/gate/stdout flags are IDENTICAL to the engine CLI's
    `sp` subparser (which is likewise just `_add_chem_options`)."""
    p = argparse.ArgumentParser(
        prog="single-point-energy",
        description="Total electronic energy at a fixed geometry (no relaxation), "
                    "plus frontier-orbital data (HOMO/LUMO/gap).",
    )
    argkit._add_chem_options(p)
    return p


def _xtb_homo_lumo(atoms, calc) -> Dict[str, Any]:
    """Run a low-level xtb singlepoint to recover orbital eigenvalues.

    The ASE-side XTB calculator only returns energy/forces/dipole; orbital
    energies live on the xtb-python Calculator's Result object.

    Kept here (rather than in calculators.py) to avoid importing xtb at
    module-load time on systems without xtb-python installed.
    """
    import numpy as np
    from assay_core.calculators import import_xtb_python
    try:
        Calculator, Param, VERBOSITY_MUTED = import_xtb_python()
    except ImportError:
        return {}  # soft-fail: skip xtb HOMO/LUMO extraction if xtb-python absent

    numbers = np.array(atoms.get_atomic_numbers(), dtype=np.int32)
    positions_bohr = np.asarray(atoms.get_positions()) * ANGSTROM_TO_BOHR

    charge = float(getattr(calc, "_chemkit_charge", 0))
    uhf = int(getattr(calc, "_chemkit_uhf", 0))

    try:
        xcalc = Calculator(Param.GFN2xTB, numbers, positions_bohr,
                           charge=charge, uhf=uhf)
        xcalc.set_verbosity(VERBOSITY_MUTED)
        # ALPB solvent if configured on the ASE calc
        solvent = getattr(calc, "parameters", {}).get("solvent")
        if solvent:
            try:
                from xtb.utils import get_solvent, Solvent
                sol = get_solvent(solvent)
                if sol != Solvent.none:
                    xcalc.set_solvent(sol)
            except Exception:
                pass
        res = xcalc.singlepoint()
        eigs = np.asarray(res.get_orbital_eigenvalues())   # Hartree
        occs = np.asarray(res.get_orbital_occupations())
    except Exception:
        return {}

    occupied = np.where(occs > 1e-6)[0]
    virtual = np.where(occs < 1e-6)[0]
    if occupied.size == 0 or virtual.size == 0:
        return {}

    homo_idx = int(occupied[-1])
    lumo_idx = int(virtual[0])
    homo_eV = float(eigs[homo_idx]) * HARTREE_TO_EV
    lumo_eV = float(eigs[lumo_idx]) * HARTREE_TO_EV
    return {
        "homo_eV": homo_eV,
        "lumo_eV": lumo_eV,
        "homo_lumo_gap_eV": lumo_eV - homo_eV,
    }


if __name__ == "__main__":
    raise SystemExit(argkit.run_cli(build_parser(), run, task=TASK))
