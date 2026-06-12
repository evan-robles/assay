"""PySCF backend — multi-method (HF, DFT, ...) ab initio entry points.

Public surface:
    PySCFCalculator              # ASE Calculator for use by every chemkit task
    run_sp_dft, run_sp_hf        # standalone single-point helpers
    resolve_dft_tier, DFT_TIERS  # tier presets used by chemkit.calculators
"""
from .calculator import PySCFCalculator
from .dft import (
    run_sp as run_sp_dft,
    resolve_tier as resolve_dft_tier,
    TIERS as DFT_TIERS,
    DEFAULT_TIER as DFT_DEFAULT_TIER,
)
from .hf import (
    run_sp as run_sp_hf,
    DEFAULT_BASIS as HF_DEFAULT_BASIS,
)

__all__ = [
    "PySCFCalculator",
    "run_sp_dft",
    "run_sp_hf",
    "resolve_dft_tier",
    "DFT_TIERS",
    "DFT_DEFAULT_TIER",
    "HF_DEFAULT_BASIS",
]
