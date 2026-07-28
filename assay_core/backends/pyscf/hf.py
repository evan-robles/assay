"""Hartree-Fock entry point for the PySCF backend.

HF has no functional or tier — just a basis. Shape mirrors `dft.run_sp` so the
task layer can dispatch uniformly.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from .molecule import build_mol, promote_basis_for_anion
from .scf import build_mean_field, pack_scf_result, _report_auxbasis


DEFAULT_BASIS = "def2-tzvp"

# HF has no functional, but convergence still varies with how tight you want
# the answer. Same scf_tol/max_cycle ladder as DFT so chemkit's --tier flag
# means the same thing across methods.
HF_TIERS = {
    "fast":     {"scf_tol": 1e-7,  "max_cycle": 80},
    "standard": {"scf_tol": 1e-8,  "max_cycle": 150},
    "accurate": {"scf_tol": 1e-10, "max_cycle": 300},
}
DEFAULT_TIER = "standard"


def run_sp(
    atoms,
    *,
    tier: Optional[str] = None,
    basis: Optional[str] = None,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    max_memory_mb: int = 8000,
) -> Dict[str, Any]:
    """Run an HF single-point and return {energy_hartree, extras, warnings}."""
    chosen_basis = basis or DEFAULT_BASIS
    used_basis, promoted = promote_basis_for_anion(chosen_basis, charge)
    tier_name = (tier or DEFAULT_TIER).lower()
    if tier_name not in HF_TIERS:
        raise ValueError(f"Unknown HF tier {tier!r}. Choose from {sorted(HF_TIERS)}.")
    tcfg = HF_TIERS[tier_name]

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
        scf_tol=tcfg["scf_tol"],
        max_cycle=tcfg["max_cycle"],
        solvent=solvent,
    )

    energy_hartree = float(mf.kernel())

    extras = pack_scf_result(mf)
    extras.update({
        "tier": tier_name,
        "basis": used_basis,
        "scf_tol": tcfg["scf_tol"],
        "scf_max_cycle": tcfg["max_cycle"],
        "density_fit": False,
        "auxbasis": _report_auxbasis(mf),
        "integral_treatment": "exact (no density fitting)",
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
