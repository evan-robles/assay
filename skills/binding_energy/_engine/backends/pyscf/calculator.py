"""ASE-compatible Calculator backed by PySCF.

Lets every chemkit task that already speaks ASE (opt, freq, binding, scan,
electrostatics, ...) pick up DFT and HF without per-task plumbing. The
calculator caches its converged SCF object on the most recent geometry, so
chained property requests (energy then forces; energy then dipole) avoid
re-running the SCF.

Warm-start: the converged density matrix from the most recent geometry is
cached and passed as `dm0` to the next `kernel()` call. ASE driver loops
(BFGS opt, Vibrations finite-difference Hessian) feed back small-displacement
geometries, so the prior DM is an excellent initial guess and typically cuts
the iteration count by 2-3×. If the warm-start fails to converge (rare; can
happen at large displacements that drag the molecule through a near-
degeneracy), we automatically fall back to a cold SCF.
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
        max_cycle: Optional[int] = None,
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
        self._max_cycle = max_cycle if max_cycle is None else int(max_cycle)
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

        # Warm-start density matrix from the previous converged SCF.
        # Keyed on the full (symbols, charge, mult, basis, xc, method, solvent)
        # tuple: any change invalidates the cached DM. The symbols guard catches
        # different fragments (shape mismatch); the rest catch the case where
        # something mutates the calculator's parameters between calls (current
        # chemkit code doesn't, but the guard is cheap insurance against a
        # silently-stale DM leaking through if that invariant ever breaks).
        self._cached_dm = None
        self._cached_dm_key: Optional[tuple] = None

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
                max_cycle=self._max_cycle,
                density_fit=self._density_fit,
                auxbasis=self._auxbasis,
                solvent=self._solvent,
            )

            # Warm-start: pass the previous converged density as initial guess
            # iff every cache-key field matches. Symbols guard against shape
            # mismatch; the rest guard against a stale DM if any calculator
            # parameter mutated since the cache was written.
            current_key = (
                tuple(self.atoms.get_chemical_symbols()),
                self._charge,
                self._multiplicity,
                self._basis,
                self._xc,
                self._method,
                self._solvent,
            )
            dm0 = self._cached_dm if self._cached_dm_key == current_key else None

            energy_hartree = _run_scf_with_warm_start(self._mf, dm0)

            self._cached_energy_eV = energy_hartree * HARTREE_TO_EV
            self._cached_positions = positions.copy()
            self._cached_forces = None
            self._cached_dipole = None

            # Cache the converged density for the next geometry's warm start.
            # Only when convergence succeeded — a non-converged DM would
            # poison the next step.
            if getattr(self._mf, "converged", False):
                try:
                    self._cached_dm = self._mf.make_rdm1()
                    self._cached_dm_key = current_key
                except Exception:
                    self._cached_dm = None
                    self._cached_dm_key = None
            else:
                self._cached_dm = None
                self._cached_dm_key = None

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


def _run_scf_with_warm_start(mf, dm0):
    """Run mf.kernel() with the previous converged DM as initial guess; if it
    fails to converge (or raises), fall back to a cold SCF.

    Two failure modes are handled:
      1. dm0 has wrong shape (e.g. spin-restricted vs unrestricted mismatch
         after a cached-from-different-method run leaked through). kernel()
         raises ValueError on shape mismatch — caught, retried cold.
      2. dm0 leads to non-convergence (rare; usually only at large
         displacements through near-degeneracies). `mf.converged` is False;
         we redo from atomic guess. Net cost: the wasted partial SCF.
    """
    if dm0 is None:
        return float(mf.kernel())
    try:
        energy = float(mf.kernel(dm0=dm0))
    except Exception:
        # Shape mismatch or some other dm0-induced failure — drop it and
        # let PySCF build the default atomic-density guess.
        return float(mf.kernel())
    if getattr(mf, "converged", False):
        return energy
    # Warm start converged to non-convergence; retry cold.
    return float(mf.kernel())
