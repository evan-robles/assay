"""Binding / interaction energy: E(complex) - sum(E(monomers))."""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional

from . import sp as sp_task
from ..calculators import program_label
from ..io import read_geometry
from ..schema import (
    base_result, EV_TO_HARTREE, EV_TO_KCAL, SINGLE_CONFORMER_WARNING,
)


def run(
    complex_path: str,
    monomer_paths: List[str],
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    monomer_charges: Optional[List[int]] = None,
    monomer_multiplicities: Optional[List[int]] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    if len(monomer_paths) < 2:
        raise ValueError("Need at least two monomer geometries.")
    if monomer_charges is None:
        monomer_charges = [0] * len(monomer_paths)
    if monomer_multiplicities is None:
        monomer_multiplicities = [1] * len(monomer_paths)
    if len(monomer_charges) != len(monomer_paths):
        raise ValueError(
            f"monomer_charges has {len(monomer_charges)} entries but there are "
            f"{len(monomer_paths)} monomers."
        )
    if len(monomer_multiplicities) != len(monomer_paths):
        raise ValueError(
            f"monomer_multiplicities has {len(monomer_multiplicities)} entries "
            f"but there are {len(monomer_paths)} monomers."
        )

    # Charge conservation: the fragments must sum to the complex's charge, or the
    # binding energy mixes different electron counts and is physically
    # meaningless. (Mirrors the redox/pka charge-consistency checks.)
    monomer_charge_sum = sum(int(q) for q in monomer_charges)
    if monomer_charge_sum != int(charge):
        raise ValueError(
            f"binding: monomer charges sum to {monomer_charge_sum} but the "
            f"complex charge is {charge}. Charge must be conserved when the "
            "complex is split into fragments (otherwise E(complex) and "
            "Σ E(monomer) have different electron counts and their difference is "
            "not a binding energy). Set --monomer-charges so they sum to the "
            "complex charge."
        )

    # Sub-calls stamp their own integrity but never raise mid-composite; the
    # binding result is gated as a whole at the end.
    complex_sp = sp_task.run(
        complex_path, method=method, charge=charge,
        multiplicity=multiplicity, solvent=solvent, cli=cli,
        tier=tier, functional=functional, basis=basis,
        density_fit=density_fit,
        gate_integrity=False,
    )
    monomer_results = []
    monomer_sum_eV = 0.0
    for path, q, m in zip(monomer_paths, monomer_charges, monomer_multiplicities):
        r = sp_task.run(path, method=method, charge=q, multiplicity=m,
                        solvent=solvent, cli=cli,
                        tier=tier, functional=functional, basis=basis,
                        density_fit=density_fit,
                        gate_integrity=False)
        monomer_results.append(r)
        monomer_sum_eV += r["total_energy_eV"]

    binding_eV = complex_sp["total_energy_eV"] - monomer_sum_eV
    atoms_complex = read_geometry(complex_path)
    symbols = atoms_complex.get_chemical_symbols()

    result = base_result(
        task="binding_energy",
        method=complex_sp["method"],
        program=program_label(method),
        input_path=os.path.abspath(complex_path),
        n_atoms=len(atoms_complex),
        atoms=symbols,
        charge=charge, multiplicity=multiplicity, solvent=solvent, cli=cli,
    )
    result["binding_energy_eV"] = binding_eV
    result["binding_energy_hartree"] = binding_eV * EV_TO_HARTREE
    result["binding_energy_kcal_mol"] = binding_eV * EV_TO_KCAL
    result["complex_total_energy_eV"] = complex_sp["total_energy_eV"]
    result["sum_of_monomer_energies_eV"] = monomer_sum_eV
    # This task evaluates single points on the geometries AS SUPPLIED — it never
    # relaxes the fragments. So the quantity is an INTERACTION energy (fragments
    # frozen at their in-complex geometry), NOT a binding energy (which would add
    # the fragment deformation/relaxation energy). Label it for what it is so the
    # two physically distinct quantities are not conflated.
    result["quantity"] = "interaction energy (single points, fragments not relaxed)"
    result["monomer_charge_sum"] = monomer_charge_sum
    result["monomers"] = [
        {"input_file": r["input_file"], "total_energy_eV": r["total_energy_eV"],
         "charge": int(q), "multiplicity": int(m)}
        for r, q, m in zip(monomer_results, monomer_charges, monomer_multiplicities)
    ]
    result["warnings"] = result.get("warnings", []) + [
        "Reported value is an INTERACTION energy: E(complex) − Σ E(monomer) with "
        "every fragment evaluated AS SUPPLIED (no relaxation). If your monomer "
        "geometries were extracted from the complex this is the interaction "
        "energy at the complex geometry; if they were separately relaxed it "
        "approaches a true binding energy. The two differ by the fragment "
        "deformation energy, which is not computed here.",
        "For a thermodynamic binding energy, optimize the complex and each "
        "monomer separately and add the deformation energy; for DFT also apply "
        "a counterpoise (BSSE) correction.",
        SINGLE_CONFORMER_WARNING,
    ]

    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)
