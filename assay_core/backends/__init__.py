"""Backend dispatch layer for ASSAY.

xtb and MOPAC live in `assay_core.calculators` (single-method ASE calculators).
PySCF hosts many methods (HF, DFT, MP2, CCSD(T), CASSCF, TDDFT) and gets its
own subpackage here.
"""
