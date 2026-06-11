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
from ..calculators import program_label
from ..io import read_geometry
from ..schema import base_result, EV_TO_KCAL

# Standard reference potentials (V vs absolute potential of electron at rest).
# E_abs(SHE) ≈ 4.281 V (Trasatti / IUPAC recommended).
REFERENCE_POTENTIALS_V = {
    "SHE": 4.281,
    "Ag/AgCl": 4.281 + 0.222,
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
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    if reference not in REFERENCE_POTENTIALS_V:
        raise ValueError(
            f"Unknown reference {reference!r}. "
            f"Choose from: {list(REFERENCE_POTENTIALS_V)}"
        )

    # The reduction Ox + n e⁻ → Red implies reduced_charge − oxidized_charge = −n.
    # Reject obvious mismatches: passing n_electrons=2 with Δcharge=−1 (or any
    # combination where they disagree) produces a meaningless E°.
    expected_dq = -int(n_electrons)
    actual_dq = int(reduced_charge) - int(oxidized_charge)
    if actual_dq != expected_dq:
        raise ValueError(
            f"redox: n_electrons={n_electrons} but reduced_charge - oxidized_charge "
            f"= {actual_dq} (expected {expected_dq}). The reduced form must have "
            f"exactly n more electrons than the oxidized form (one less unit of charge "
            "per electron added)."
        )
    # Spin parity: each unpaired-electron count changes by ±1 per added electron,
    # so the multiplicities must differ by exactly n_electrons modulo 2.
    expected_parity = n_electrons % 2
    actual_parity = abs(int(reduced_multiplicity) - int(oxidized_multiplicity)) % 2
    if actual_parity != expected_parity:
        raise ValueError(
            f"redox: |reduced_mult - oxidized_mult| has parity {actual_parity}, "
            f"expected {expected_parity} for {n_electrons}-electron transfer. "
            "Adding n electrons flips spin parity n times; check that your "
            "ox/red multiplicities are consistent (e.g. neutral singlet ↔ "
            "anion-radical doublet for n=1)."
        )

    ox_sp = sp_task.run(
        input_path, method=method, charge=oxidized_charge,
        multiplicity=oxidized_multiplicity, solvent=solvent, cli=cli,
        tier=tier, functional=functional, basis=basis,
    )
    red_sp = sp_task.run(
        input_path, method=method, charge=reduced_charge,
        multiplicity=reduced_multiplicity, solvent=solvent, cli=cli,
        tier=tier, functional=functional, basis=basis,
    )

    delta_E_eV = red_sp["total_energy_eV"] - ox_sp["total_energy_eV"]
    # E°(red/ox) = -(ΔG/nF) - E_ref(abs). With ΔG in eV and one electron, ΔG/F = ΔE in volts.
    E_redox_V = -(delta_E_eV / n_electrons) - REFERENCE_POTENTIALS_V[reference]

    atoms = read_geometry(input_path)
    result = base_result(
        task="redox_potential",
        method=ox_sp["method"],
        program=program_label(method),
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
