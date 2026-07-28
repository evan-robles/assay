"""pka-acidity task — thin shim over the self-contained skill.

The workflow now lives in ``skills/pka_acidity/scripts/run.py``
(DESIGN.md inversion). This module re-exports its ``run`` so the engine
CLI and composite callers keep working against a single copy.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skills.pka_acidity.scripts.run import run  # noqa: E402,F401

__all__ = ['run']
