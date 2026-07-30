"""Re-exports the single-point-energy skill's ``run`` (and the xtb HOMO/LUMO helper) for the engine CLI."""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skills.single_point_energy.scripts.run import run, _xtb_homo_lumo  # noqa: E402,F401

__all__ = ["run", "_xtb_homo_lumo"]
