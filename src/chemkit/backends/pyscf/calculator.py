"""ASE-compatible Calculator backed by PySCF.

Lets every chemkit task that already speaks ASE (opt, freq, binding, scan,
electrostatics, ...) pick up DFT and HF without per-task plumbing. The
calculator caches its converged SCF object on the most recent geometry, so
chained property requests (energy then forces; energy then dipole) avoid
re-running the SCF.
"""
from __future__ import annotations
from typing import Optional

import numpy as np
from ase.calculators.calculator import Calculator, all_changes

from .molecule import build_mol, promote_basis_for_anion
from .scf import build_mean_field


HARTREE_TO_EV = 27.211386245988
HARTREE_PER_BOHR_TO_EV_PER_ANG = 27.211386245988 / 0.529177210903


class PySCFCalculator(Calculator):
    """ASE Calculator delegating to PySCF for DFT (RKS/UKS) or HF (RHF/UHF).

    Parameters mirror the chemkit CLI knobs. `method` selects the theory layer
    ('dft' or 'hf'); `xc` is required when `method == 'dft'`.
    """

    implemented_properties = ["energy", "forces", "dipole"]
    name = "pyscf"

    def __init__(
        self,
        *,
        method: str = "dft",
        xc: Optional[str] = None,
        basis: str = "def2-tzvp",
        charge: int = 0,
        multiplicity: int = 1,
        solvent: Optional[str] = None,
        grid_level: int = 4,
        scf_tol: float = 1e-8,
        density_fit: bool = True,
        auxbasis: str = "def2-universal-jfit",
        max_memory_mb: int = 8000,
        verbose: int = 0,
    ):
        super().__init__()
        method = method.lower()
        if method not in ("dft", "hf"):
            raise ValueError(f"PySCFCalculator: unknown method {method!r}")
        if method == "dft" and not xc:
            raise ValueError("PySCFCalculator: DFT requires an xc functional.")
        self._method = method
        self._xc = xc
        self._basis = basis
        self._charge = int(charge)
        self._multiplicity = int(multiplicity)
        self._solvent = solvent
        self._grid_level = int(grid_level)
        self._scf_tol = float(scf_tol)
        self._density_fit = bool(density_fit)
        self._auxbasis = auxbasis
        self._max_memory_mb = int(max_memory_mb)
        self._verbose = int(verbose)

        # Auto-promote diffuse basis for anions; record what we actually used.
        self._basis, self._basis_promoted = promote_basis_for_anion(self._basis, self._charge)

        # Cached converged mean-field; invalidated by ASE when atoms change.
        self._mol = None
        self._mf = None
        self._cached_positions = None

    # ---- chemkit-side accessors (used by sp.py / electrostatics / frontier) --

    @property
    def method(self) -> str:
        return self._method

    @property
    def functional(self) -> Optional[str]:
        return self._xc

    @property
    def basis(self) -> str:
        return self._basis

    @property
    def basis_promoted(self) -> bool:
        return self._basis_promoted

    @property
    def mean_field(self):
        """Last converged SCF object (or None if no calculation yet)."""
        return self._mf

    # ---- ASE plumbing ------------------------------------------------------

    def calculate(self, atoms=None, properties=("energy",), system_changes=all_changes):
        super().calculate(atoms, properties, system_changes)

        positions = np.asarray(self.atoms.get_positions(), dtype=float)
        need_scf = (
            self._mf is None
            or self._cached_positions is None
            or not np.array_equal(positions, self._cached_positions)
        )
        if need_scf:
            self._mol = build_mol(
                self.atoms,
                basis=self._basis,
                charge=self._charge,
                multiplicity=self._multiplicity,
                max_memory_mb=self._max_memory_mb,
                verbose=self._verbose,
            )
            self._mf = build_mean_field(
                self._mol,
                method=self._method,
                xc=self._xc,
                grid_level=self._grid_level,
                scf_tol=self._scf_tol,
                density_fit=self._density_fit,
                auxbasis=self._auxbasis,
                solvent=self._solvent,
            )
            energy_hartree = float(self._mf.kernel())
            self._cached_energy_eV = energy_hartree * HARTREE_TO_EV
            self._cached_positions = positions.copy()
            self._cached_forces = None
            self._cached_dipole = None

        self.results["energy"] = self._cached_energy_eV

        if "forces" in properties:
            if self._cached_forces is None:
                grad = self._mf.nuc_grad_method().kernel()  # Hartree / Bohr
                self._cached_forces = -np.asarray(grad) * HARTREE_PER_BOHR_TO_EV_PER_ANG
            self.results["forces"] = self._cached_forces

        if "dipole" in properties:
            if self._cached_dipole is None:
                try:
                    self._cached_dipole = np.asarray(
                        self._mf.dip_moment(unit="Debye", verbose=0), dtype=float
                    )
                except Exception:
                    self._cached_dipole = np.zeros(3)
            self.results["dipole"] = self._cached_dipole
