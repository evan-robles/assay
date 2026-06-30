"""Single-point energy task."""
from __future__ import annotations
import os
from typing import Any, Dict, Optional

from ..calculators import (
    build_calculator, apply_calc_to_atoms,
    method_label, program_label, collect_calc_extras,
)
from ..io import read_geometry
from ..integrity import finalize
from ..schema import (
    base_result, energy_block_from_eV, element_warnings,
    scf_convergence_warnings,
)
from ..constants import HARTREE_TO_EV, ANGSTROM_TO_BOHR


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


def _xtb_homo_lumo(atoms, calc) -> Dict[str, Any]:
    """Run a low-level xtb singlepoint to recover orbital eigenvalues.

    The ASE-side XTB calculator only returns energy/forces/dipole; orbital
    energies live on the xtb-python Calculator's Result object.

    Kept here (rather than in calculators.py) to avoid importing xtb at
    module-load time on systems without xtb-python installed.
    """
    try:
        import numpy as np
        from xtb.interface import Calculator, Param
        from xtb.libxtb import VERBOSITY_MUTED
    except ImportError:
        return {}

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
