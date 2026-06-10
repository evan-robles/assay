"""Hartree-Fock entry point for the PySCF backend.

HF has no functional or tier — just a basis. Shape mirrors `dft.run_sp` so the
task layer can dispatch uniformly.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from .molecule import build_mol, promote_basis_for_anion
from .scf import build_mean_field, pack_scf_result


DEFAULT_BASIS = "def2-tzvp"


def run_sp(
    atoms,
    *,
    basis: Optional[str] = None,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    max_memory_mb: int = 8000,
) -> Dict[str, Any]:
    """Run an HF single-point and return {energy_hartree, extras, warnings}."""
    chosen_basis = basis or DEFAULT_BASIS
    used_basis, promoted = promote_basis_for_anion(chosen_basis, charge)

    mol = build_mol(
        atoms,
        basis=used_basis,
        charge=charge,
        multiplicity=multiplicity,
        max_memory_mb=max_memory_mb,
    )

    mf = build_mean_field(
        mol,
        method="hf",
        solvent=solvent,
    )

    energy_hartree = float(mf.kernel())

    extras = pack_scf_result(mf)
    extras.update({
        "basis": used_basis,
        "density_fit": True,
        "solvent_model": ("ddCOSMO" if solvent else None),
    })

    warnings = []
    if promoted:
        warnings.append(
            f"Anion detected (charge={charge}); basis promoted to {used_basis} "
            f"to add diffuse functions."
        )
    if not extras.get("scf_converged", False):
        warnings.append("HF SCF did not converge — energy is from the last iteration.")

    return {
        "energy_hartree": energy_hartree,
        "extras": extras,
        "warnings": warnings,
    }
