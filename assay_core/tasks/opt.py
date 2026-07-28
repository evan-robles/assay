"""Geometry-optimization task — thin shim over the self-contained skill.

The workflow now lives in ``skills/geometry_optimize/scripts/run.py`` (DESIGN.md
inversion). This module re-exports its ``run`` (and the ``_run_mopac`` helper,
which the confsearch and scan tasks reuse) so the engine CLI and sibling tasks
keep working against a single copy of the physics.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skills.geometry_optimize.scripts.run import run, _run_mopac  # noqa: E402,F401

__all__ = ["run", "_run_mopac"]
