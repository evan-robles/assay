"""Redox potential via a simple thermodynamic cycle.

E°(red) = -(ΔG_redox + n*F*E_ref) / (n*F)

Where ΔG_redox = G(reduced) - G(oxidized) for the half-reaction
    Ox + n e⁻ → Red

Uses gas-phase or implicit-solvent (COSMO/ALPB) electronic energies as a stand-in
for Gibbs energies. For research-grade values, run `freq` on each oxidation
state and supply G's manually.
"""
from __future__ import annotations
import os
from typing import Any, Dict, Optional

from . import sp as sp_task
from ..io import read_geometry
from ..schema import base_result, EV_TO_KCAL

# Standard reference potentials (V vs absolute potential of electron at rest).
# E_abs(SHE) ≈ 4.281 V (Trasatti / IUPAC recommended).
REFERENCE_POTENTIALS_V = {
    "SHE": 4.281,
    "Ag/AgCl": 4.281 - 0.222,
    "Fc+/Fc": 4.281 + 0.40,   # approximate; depends on solvent
}


def run(
    input_path: str,
    *,
    method: str,
    oxidized_charge: int,
    reduced_charge: int,
    oxidized_multiplicity: int = 1,
    reduced_multiplicity: int = 2,
    solvent: Optional[str] = None,
    reference: str = "SHE",
    n_electrons: int = 1,
    cli: str = "",
) -> Dict[str, Any]:
    if reference not in REFERENCE_POTENTIALS_V:
        raise ValueError(
            f"Unknown reference {reference!r}. "
            f"Choose from: {list(REFERENCE_POTENTIALS_V)}"
        )

    ox_sp = sp_task.run(
        input_path, method=method, charge=oxidized_charge,
        multiplicity=oxidized_multiplicity, solvent=solvent, cli=cli,
    )
    red_sp = sp_task.run(
        input_path, method=method, charge=reduced_charge,
        multiplicity=reduced_multiplicity, solvent=solvent, cli=cli,
    )

    delta_E_eV = red_sp["total_energy_eV"] - ox_sp["total_energy_eV"]
    # E°(red/ox) = -(ΔG/nF) - E_ref(abs). With ΔG in eV and one electron, ΔG/F = ΔE in volts.
    E_redox_V = -(delta_E_eV / n_electrons) - REFERENCE_POTENTIALS_V[reference]

    atoms = read_geometry(input_path)
    result = base_result(
        task="redox_potential",
        method=ox_sp["method"],
        program=method,
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=atoms.get_chemical_symbols(),
        charge=oxidized_charge, multiplicity=oxidized_multiplicity,
        solvent=solvent, cli=cli,
    )
    result["redox_potential_V_vs_" + reference] = E_redox_V
    result["delta_E_redox_eV"] = delta_E_eV
    result["delta_E_redox_kcal_mol"] = delta_E_eV * EV_TO_KCAL
    result["n_electrons"] = n_electrons
    result["oxidized_state"] = {
        "charge": oxidized_charge, "multiplicity": oxidized_multiplicity,
        "energy_eV": ox_sp["total_energy_eV"],
    }
    result["reduced_state"] = {
        "charge": reduced_charge, "multiplicity": reduced_multiplicity,
        "energy_eV": red_sp["total_energy_eV"],
    }
    result["warnings"] = result.get("warnings", []) + [
        "Redox potential computed from single-point energies on the SAME geometry — "
        "does not include reorganization or solvation free energy contributions properly. "
        "Accuracy with semi-empirical methods is ±0.3–0.5 V at best. "
        "For publishable values: optimize each oxidation state, run freq for ΔG, "
        "include explicit solvation cycle.",
    ]
    return result
