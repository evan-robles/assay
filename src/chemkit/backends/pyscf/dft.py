"""DFT entry point for the PySCF backend.

Exposes `run_sp(atoms, ...)` returning a chemkit-shape result dict.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from .molecule import build_mol, promote_basis_for_anion
from .scf import build_mean_field, pack_scf_result


# Tier table: (xc, basis, grid_level, auxbasis).
# Functional strings use libxc names (PySCF accepts both libxc and its own
# aliases — libxc is the safer bet for portability).
# wB97X-V / wB97M-V use VV10 nonlocal correlation, native in PySCF — no add-on.
# wB97X-D3BJ would be a hair better at the standard tier but requires
# pyscf-dispersion, which currently fails to load on Python 3.13.
TIERS = {
    "fast":     {"xc": "r2scan",  "basis": "def2-svp",   "grid": 3, "aux": "def2-universal-jfit"},
    "standard": {"xc": "wb97x_v", "basis": "def2-tzvp",  "grid": 4, "aux": "def2-universal-jfit"},
    "accurate": {"xc": "wb97m_v", "basis": "def2-qzvpp", "grid": 5, "aux": "def2-universal-jfit"},
}
DEFAULT_TIER = "standard"


def resolve_tier(
    tier: Optional[str],
    xc: Optional[str],
    basis: Optional[str],
) -> Dict[str, Any]:
    """Merge a tier preset with explicit overrides. Overrides win."""
    tier_name = (tier or DEFAULT_TIER).lower()
    if tier_name not in TIERS:
        raise ValueError(f"Unknown DFT tier {tier!r}. Choose from {sorted(TIERS)}.")
    cfg = dict(TIERS[tier_name])
    cfg["tier"] = tier_name
    if xc:
        cfg["xc"] = xc
    if basis:
        cfg["basis"] = basis
    return cfg


def run_sp(
    atoms,
    *,
    tier: Optional[str] = None,
    xc: Optional[str] = None,
    basis: Optional[str] = None,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    max_memory_mb: int = 8000,
) -> Dict[str, Any]:
    """Run a DFT single-point and return the per-method `code_specific` block
    plus the converged total energy in Hartree.

    The task layer (chemkit.tasks.sp) wraps this into the shared chemkit
    result schema; this function stays backend-shaped so it can be reused by
    `opt`, `freq`, `binding`, etc. without round-tripping JSON.
    """
    cfg = resolve_tier(tier, xc, basis)
    used_basis, promoted = promote_basis_for_anion(cfg["basis"], charge)
    cfg["basis"] = used_basis

    mol = build_mol(
        atoms,
        basis=cfg["basis"],
        charge=charge,
        multiplicity=multiplicity,
        max_memory_mb=max_memory_mb,
    )

    mf = build_mean_field(
        mol,
        method="dft",
        xc=cfg["xc"],
        grid_level=cfg["grid"],
        auxbasis=cfg["aux"],
        solvent=solvent,
    )

    energy_hartree = float(mf.kernel())

    extras = pack_scf_result(mf)
    extras.update({
        "tier": cfg["tier"],
        "functional": cfg["xc"],
        "basis": cfg["basis"],
        "grid_level": cfg["grid"],
        "auxbasis": cfg["aux"],
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
        warnings.append("DFT SCF did not converge — energy is from the last iteration.")

    return {
        "energy_hartree": energy_hartree,
        "extras": extras,
        "warnings": warnings,
    }
