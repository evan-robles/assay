"""logP (octanol/water partition coefficient) via implicit-solvent SPs.

logP from a three-leg thermodynamic cycle on the **same input geometry**:

    logP = (ΔG_solv(water) − ΔG_solv(octanol)) / (RT ln 10)

Each ΔG_solv is the difference of two single-point energies (gas vs implicit
solvent) at the same level of theory. The result is the "electronic" logP —
no cavitation, dispersion-repulsion, or thermal corrections. Screening-grade
(±1 log unit typical at semi-empirical level).

Sign convention: positive logP → prefers octanol (lipophilic).

Was previously co-located in `tasks/solvation.py`; split out so each skill
in `skills/` maps to a single module under `tasks/`.
"""
from __future__ import annotations
import os
from typing import Any, Dict, Optional

from _engine.tasks import sp as sp_task
from _engine.calculators import program_label
from _engine.io import read_geometry
from _engine.schema import base_result, EV_TO_KCAL, element_warnings

# RT * ln(10) at 298.15 K, in kcal/mol.
#   R = 1.987204e-3 kcal/(mol·K); RT = 0.5925 kcal/mol; * ln10 = 1.3643.
RT_LN10_KCAL_MOL_298K = 1.3643


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
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
                      multiplicity=multiplicity, solvent=None, cli=cli,
                      tier=tier, functional=functional, basis=basis)
    water = sp_task.run(input_path, method=method, charge=charge,
                        multiplicity=multiplicity, solvent="water", cli=cli,
                        tier=tier, functional=functional, basis=basis)
    octanol = sp_task.run(input_path, method=method, charge=charge,
                          multiplicity=multiplicity, solvent="octanol", cli=cli,
                          tier=tier, functional=functional, basis=basis)

    dG_w_kcal = (water["total_energy_eV"]   - gas["total_energy_eV"]) * EV_TO_KCAL
    dG_o_kcal = (octanol["total_energy_eV"] - gas["total_energy_eV"]) * EV_TO_KCAL
    ddG = dG_w_kcal - dG_o_kcal
    logp = ddG / RT_LN10_KCAL_MOL_298K

    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()
    result = base_result(
        task="logp",
        method=gas["method"], program=program_label(method),
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
