"""ΔG_solv (single solvent) and logP (water/octanol) via implicit-solvent SPs.

Both tasks reduce to differences of single-point energies on the **same input
geometry**, comparing a gas-phase calculation to one or more implicit-solvent
calculations with the same method. This is the "electronic" ΔG_solv — no
cavitation/dispersion/thermal terms. Adequate for screening at semi-empirical
accuracy; flagged as such in every result.

Energy-reference soundness (per src/chemkit/tasks/sp.py): both backends report
`total_energy_eV` referenced to a state that is identical between gas and
implicit-solvent calls (xtb: isolated atoms at infinity; PM7: elements in
standard states). Solvent contributions therefore subtract cleanly within a
single method. The CLI already enforces one --method per invocation.
"""
from __future__ import annotations
import os
from typing import Any, Dict, Optional

from . import sp as sp_task
from ..io import read_geometry
from ..schema import base_result, EV_TO_HARTREE, EV_TO_KCAL, element_warnings

# RT * ln(10) at 298.15 K, in kcal/mol.
#   R = 1.987204e-3 kcal/(mol·K); RT = 0.5925 kcal/mol; * ln10 = 1.3643.
RT_LN10_KCAL_MOL_298K = 1.3643

_SCREENING_WARNINGS = [
    "Electronic ΔG_solv only — no cavitation, dispersion-repulsion, or thermal "
    "correction.",
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
) -> Dict[str, Any]:
    """Solvation free energy ΔG_solv = E(solvated) − E(gas) on the same geometry."""
    if not solvent:
        raise ValueError("solvation requires --solvent")

    gas = sp_task.run(input_path, method=method, charge=charge,
                      multiplicity=multiplicity, solvent=None, cli=cli)
    solv = sp_task.run(input_path, method=method, charge=charge,
                       multiplicity=multiplicity, solvent=solvent, cli=cli)

    delta_eV = solv["total_energy_eV"] - gas["total_energy_eV"]
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    result = base_result(
        task="solvation",
        method=gas["method"], program=method,
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
    warns += element_warnings(symbols, method)
    result["warnings"] = warns
    return result


def run_logp(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    cli: str = "",
) -> Dict[str, Any]:
    """logP from ΔG_solv(water) − ΔG_solv(octanol). Pinned to 298.15 K.

    logP > 0 → prefers octanol (lipophilic). For ionizable molecules use logD,
    which is pH-dependent and out of scope here.
    """
    if charge != 0:
        raise ValueError(
            "logP is defined for the neutral species (charge=0). For ionizable "
            "molecules, consider logD (pH-dependent), which is out of scope for "
            "chemkit."
        )

    gas = sp_task.run(input_path, method=method, charge=charge,
                      multiplicity=multiplicity, solvent=None, cli=cli)
    water = sp_task.run(input_path, method=method, charge=charge,
                        multiplicity=multiplicity, solvent="water", cli=cli)
    octanol = sp_task.run(input_path, method=method, charge=charge,
                          multiplicity=multiplicity, solvent="octanol", cli=cli)

    dG_w_kcal = (water["total_energy_eV"]   - gas["total_energy_eV"]) * EV_TO_KCAL
    dG_o_kcal = (octanol["total_energy_eV"] - gas["total_energy_eV"]) * EV_TO_KCAL
    ddG = dG_w_kcal - dG_o_kcal
    logp = ddG / RT_LN10_KCAL_MOL_298K

    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()
    result = base_result(
        task="logp",
        method=gas["method"], program=method,
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms), atoms=symbols,
        charge=charge, multiplicity=multiplicity, solvent=None, cli=cli,
    )
    result.update({
        "logp": logp,
        "delta_G_solv_water_kcal_mol": dG_w_kcal,
        "delta_G_solv_octanol_kcal_mol": dG_o_kcal,
        "delta_delta_G_kcal_mol": ddG,
        "RT_ln10_kcal_mol_at_298K": RT_LN10_KCAL_MOL_298K,
        "water_solvent_key": "water",
        "octanol_solvent_key": "octanol",
        "energy_gas_eV": gas["total_energy_eV"],
        "energy_water_eV": water["total_energy_eV"],
        "energy_octanol_eV": octanol["total_energy_eV"],
    })

    warns = []
    if abs(dG_w_kcal) < 1e-4 or abs(dG_o_kcal) < 1e-4:
        warns.append(
            "ΔG_solv ≈ 0 in water or octanol — implicit solvent may have been "
            "silently dropped. Check xtb-python / MOPAC version."
        )
    warns += [
        "logP from semi-empirical ΔG_solv differences is screening-grade; "
        "±1 log unit typical.",
        "For chemoinformatic logP, also consider RDKit's Crippen/XLogP "
        "(group-contribution).",
    ]
    warns += element_warnings(symbols, method)
    result["warnings"] = warns
    return result
