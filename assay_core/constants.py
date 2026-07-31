"""Physical constants and unit conversions for chemkit — single source.

Every unit conversion the engine uses is defined here once; tasks and backends
import from this module rather than redefining constants locally.

Provenance
----------
Fundamental constants are CODATA 2022 recommended values:
  Mohr, P. J.; Tiesinga, E.; Newell, D. B.; Taylor, B. N. CODATA Recommended
  Values of the Fundamental Physical Constants: 2022. National Institute of
  Standards and Technology. https://physics.nist.gov/cuu/Constants/
  (accessed 2026-06-15).

The thermochemical calorie is exact by definition: 1 cal = 4.184 J.
Each constant below cites the CODATA quantity it derives from.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------
# Hartree energy: 27.211 386 245 981(30) eV
HARTREE_TO_EV = 27.211386245981
# Hartree energy: 2625.499 639 5(40) kJ/mol -> /4.184 = 627.5094740629 kcal/mol
HARTREE_TO_KCAL = 627.5094740629
EV_TO_HARTREE = 1.0 / HARTREE_TO_EV
EV_TO_KCAL = HARTREE_TO_KCAL / HARTREE_TO_EV   # ≈ 23.0605478... kcal/mol per eV
KCAL_TO_EV = 1.0 / EV_TO_KCAL                  # eV per kcal/mol
CAL_TO_EV = KCAL_TO_EV / 1000.0                # eV per cal/mol

# Spectroscopy: photon energy in eV per wavenumber (cm^-1).
# 1 eV = 8065.543937... cm^-1; the inverse 1.239841984e-4 eV/cm^-1 is the
# CODATA-derived hc factor used for vibrational-frequency conversions.
EV_PER_CM = 1.239841984e-4
CM_PER_EV = 1.0 / EV_PER_CM

# ---------------------------------------------------------------------------
# Length
# ---------------------------------------------------------------------------
# Bohr radius a0 = 0.529 177 210 544(82) Å.
BOHR_TO_ANGSTROM = 0.529177210544
ANGSTROM_TO_BOHR = 1.0 / BOHR_TO_ANGSTROM      # = 1.8897261259077822

# ---------------------------------------------------------------------------
# Gradient / force
# ---------------------------------------------------------------------------
# Hartree/Bohr -> eV/Å (for converting QM gradients to ASE's force units).
HARTREE_PER_BOHR_TO_EV_PER_ANGSTROM = HARTREE_TO_EV / BOHR_TO_ANGSTROM

# ---------------------------------------------------------------------------
# Dipole
# ---------------------------------------------------------------------------
# Atomic unit of electric dipole moment ea0 = 8.478 353 6198(13)e-30 C·m;
# 1 D = 1e-21/c C·m (c = 299 792 458 m/s exact) -> ea0/D = 2.541746471.
AU_TO_DEBYE = 2.541746471


# ===========================================================================
# Reference data (element coverage). Solvent dielectric tables live in
# schema.py for now (they are consumed by the calculator factory and carry
# their own Gaussian-SCRF / MOPAC provenance there).
# ===========================================================================
# Elements PM7 parametrizes only marginally — flagged in result warnings.
# GFN2-xTB covers Z=1..86 with no comparable gaps, so only the MOPAC/PM7 set
# is needed.
PM7_WEAK_ELEMENTS = {"Fe", "Ru", "Os", "Co", "Rh", "Ir", "Mn", "Tc", "Re",
                     "Cr", "Mo", "W", "V", "Nb", "Ta", "Sc", "Y"}
