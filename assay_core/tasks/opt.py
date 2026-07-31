"""Re-exports the geometry-optimize skill's ``run`` (and the `_run_mopac` helper reused by confsearch/scan) for the engine CLI."""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skills.geometry_optimize.scripts.run import run, _run_mopac  # noqa: E402,F401

__all__ = ["run", "_run_mopac"]
