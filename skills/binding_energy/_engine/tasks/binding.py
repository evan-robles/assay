"""Binding / interaction energy: E(complex) - sum(E(monomers))."""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional

from _engine.tasks import sp as sp_task
from _engine.calculators import program_label
from _engine.io import read_geometry
from _engine.schema import base_result, EV_TO_HARTREE, EV_TO_KCAL


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
) -> Dict[str, Any]:
    if len(monomer_paths) < 2:
        raise ValueError("Need at least two monomer geometries.")
    if monomer_charges is None:
        monomer_charges = [0] * len(monomer_paths)
    if monomer_multiplicities is None:
        monomer_multiplicities = [1] * len(monomer_paths)

    complex_sp = sp_task.run(
        complex_path, method=method, charge=charge,
        multiplicity=multiplicity, solvent=solvent, cli=cli,
        tier=tier, functional=functional, basis=basis,
    )
    monomer_results = []
    monomer_sum_eV = 0.0
    for path, q, m in zip(monomer_paths, monomer_charges, monomer_multiplicities):
        r = sp_task.run(path, method=method, charge=q, multiplicity=m,
                        solvent=solvent, cli=cli,
                        tier=tier, functional=functional, basis=basis)
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
    result["monomers"] = [
        {"input_file": r["input_file"], "total_energy_eV": r["total_energy_eV"]}
        for r in monomer_results
    ]
    result["warnings"] = result.get("warnings", []) + [
        "Binding energy uses single-point energies on the provided geometries. "
        "For thermodynamically meaningful values, optimize complex and monomers separately first, "
        "and consider counterpoise correction for basis-set superposition error (DFT only)."
    ]
    return result
