"""ASE Atoms -> pyscf.gto.Mole construction with chemkit defaults.

Responsibilities:
- Convert ASE Atoms into a pyscf.gto.Mole
- Handle charge / spin (PySCF wants nelec_alpha - nelec_beta, not multiplicity)
- Auto-promote basis sets for anions (diffuse functions matter a lot)
- Centralize the "pyscf not installed" error
"""
from __future__ import annotations
from typing import Tuple


# Basis sets that should be promoted to their diffuse variant for anions.
# Keep the mapping small and explicit; users can override with --basis.
_DIFFUSE_PROMOTION = {
    "def2-svp": "def2-svpd",
    "def2-tzvp": "def2-tzvpd",
    "def2-tzvpp": "def2-tzvppd",
    "def2-qzvp": "def2-qzvpd",
    "def2-qzvpp": "def2-qzvppd",
    "cc-pvdz": "aug-cc-pvdz",
    "cc-pvtz": "aug-cc-pvtz",
    "cc-pvqz": "aug-cc-pvqz",
}


def _require_pyscf():
    try:
        import pyscf  # noqa: F401
        from pyscf import gto  # noqa: F401
        return gto
    except ImportError as e:
        raise ImportError(
            "PySCF is not installed. Install it with:\n"
            "    pip install pyscf\n"
            "Optional dispersion add-on:\n"
            "    pip install pyscf-dispersion"
        ) from e


def promote_basis_for_anion(basis: str, charge: int) -> Tuple[str, bool]:
    """If `charge < 0`, swap to a diffuse-augmented basis when one is known.

    Returns (resolved_basis, was_promoted).
    """
    if charge >= 0:
        return basis, False
    promoted = _DIFFUSE_PROMOTION.get(basis.lower())
    if promoted is None:
        return basis, False
    return promoted, True


def build_mol(
    atoms,
    *,
    basis: str,
    charge: int = 0,
    multiplicity: int = 1,
    max_memory_mb: int = 8000,
    verbose: int = 0,
):
    """Build a pyscf.gto.Mole from an ASE Atoms object.

    PySCF uses `spin = 2S = (n_alpha - n_beta)`, not the chemistry-conventional
    multiplicity (2S+1). We translate here so the rest of chemkit stays
    consistent with xtb/MOPAC's `--mult` semantics.
    """
    gto = _require_pyscf()

    if multiplicity < 1:
        raise ValueError(f"multiplicity must be >= 1, got {multiplicity}")
    spin = multiplicity - 1  # PySCF convention

    atom_spec = [
        (sym, tuple(pos))
        for sym, pos in zip(atoms.get_chemical_symbols(), atoms.get_positions())
    ]

    mol = gto.M(
        atom=atom_spec,
        basis=basis,
        charge=int(charge),
        spin=int(spin),
        unit="Angstrom",
        max_memory=int(max_memory_mb),
        verbose=int(verbose),
    )
    return mol
