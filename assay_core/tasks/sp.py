"""Single-point energy task — thin shim over the self-contained skill.

The skill-inversion (DESIGN.md) moved the single-point workflow OUT of the engine
and INTO its self-contained skill at
``skills/single_point_energy/scripts/run.py``. This module now re-exports that
skill's ``run`` so the engine CLI and composite skills that still do
``from .tasks import sp`` keep working against a SINGLE copy of the physics
(no fork).

``skills/`` is a workflow tree kept on PYTHONPATH rather than pip-installed, so
ensure the repo root (the parent of the installed ``assay_core`` package) is
importable before pulling the skill in.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skills.single_point_energy.scripts.run import run, _xtb_homo_lumo  # noqa: E402,F401

__all__ = ["run", "_xtb_homo_lumo"]
