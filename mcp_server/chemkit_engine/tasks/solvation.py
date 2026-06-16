"""ΔG_solv (single solvent) via implicit-solvent single-point energies.

Computes ΔG_solv = E(solvated) − E(gas) on the **same input geometry**,
comparing a gas-phase calculation to an implicit-solvent calculation with the
same method. This is the "electronic" ΔG_solv — no cavitation/dispersion/
thermal terms. Adequate for screening at semi-empirical accuracy; flagged as
such in every result.

Energy-reference soundness (per src/chemkit/tasks/sp.py): both backends report
`total_energy_eV` referenced to a state that is identical between gas and
implicit-solvent calls (xtb: isolated atoms at infinity; PM7: elements in
standard states). Solvent contributions therefore subtract cleanly within a
single method. The CLI already enforces one --method per invocation.

Split note: the previous `run_logp` (logP via water/octanol thermodynamic
cycle) was moved to `tasks/logp.py` so each skill in `skills/` maps to a
single module under `tasks/`. The logp computation still uses `sp.run`
directly — it does not call back into this module.
"""
from __future__ import annotations
import os
from typing import Any, Dict, Optional

from . import sp as sp_task
from ..calculators import program_label
from ..io import read_geometry
from ..schema import (
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
                      gate_integrity=False)
    solv = sp_task.run(input_path, method=method, charge=charge,
                       multiplicity=multiplicity, solvent=solvent, cli=cli,
                       tier=tier, functional=functional, basis=basis,
                       density_fit=density_fit,
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

    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)
