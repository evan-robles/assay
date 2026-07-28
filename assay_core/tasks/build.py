"""Build-from-SMILES task — thin shim over the self-contained skill.

The workflow now lives in ``skills/build_from_smiles/scripts/run.py`` (DESIGN.md
inversion). This module re-exports its ``run`` so the engine CLI keeps working
against a single copy of the logic.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skills.build_from_smiles.scripts.run import run  # noqa: E402,F401

__all__ = ["run"]
