"""DFT entry point for the PySCF backend.

Exposes `run_sp(atoms, ...)` returning a chemkit-shape result dict.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from .molecule import build_mol, promote_basis_for_anion
from .scf import build_mean_field, pack_scf_result, _report_auxbasis


# Tier table: (xc, basis, grid_level, scf_tol, max_cycle).
# Functional strings use libxc names (PySCF accepts both libxc and its own
# aliases — libxc is the safer bet for portability).
# wB97X-V / wB97M-V use VV10 nonlocal correlation, native in PySCF — no add-on.
# wB97X-D3BJ would be a hair better at the standard tier but requires
# pyscf-dispersion, which currently fails to load on Python 3.13.
#
# NOTE on density fitting: the auxiliary basis is NOT pinned here. It is chosen
# in build_mean_field() to match the functional — a JK-fit auxbasis for hybrids
# (standard = B3LYP, accurate = wB97M-V, both carry exact exchange) and a
# J-only auxbasis for pure functionals (fast = r2scan). Hard-coding a J-only
# auxbasis previously mis-fit the exchange (K) matrix of the hybrid tiers.
#
# `density_fit` is each tier's DF profile, surfaced to the user when offering
# tier choices and on the command line: the screening-grade fast/standard tiers
# carry RI ON (≈3–10× faster SCF, ~0.1–0.8 mEh error), accurate keeps EXACT
# integrals. The explicit --density-fit flag, when given, OVERRIDES this tier
# value. build_mean_field() picks the matching auxbasis (JK-fit for hybrids/HF,
# J-fit for pure functionals) when DF is on.
TIERS = {
    "fast":     {"xc": "r2scan",  "basis": "def2-svp",   "grid": 3,
                 "scf_tol": 1e-7,  "max_cycle": 80,  "density_fit": True},
    "standard": {"xc": "b3lyp",   "basis": "def2-tzvp",  "grid": 4,
                 "scf_tol": 1e-8,  "max_cycle": 150, "density_fit": True},
    "accurate": {"xc": "wb97m_v", "basis": "def2-qzvpp", "grid": 5,
                 "scf_tol": 1e-10, "max_cycle": 300, "density_fit": False},
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
        scf_tol=cfg["scf_tol"],
        max_cycle=cfg["max_cycle"],
        solvent=solvent,  # exact integrals (no density fitting) by default
    )

    energy_hartree = float(mf.kernel())

    extras = pack_scf_result(mf)
    extras.update({
        "tier": cfg["tier"],
        "functional": cfg["xc"],
        "basis": cfg["basis"],
        "grid_level": cfg["grid"],
        "scf_tol": cfg["scf_tol"],
        "scf_max_cycle": cfg["max_cycle"],
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
        warnings.append("DFT SCF did not converge — energy is from the last iteration.")

    return {
        "energy_hartree": energy_hartree,
        "extras": extras,
        "warnings": warnings,
    }
