"""Conformer-search task — thin shim over the self-contained skill.

The workflow now lives in ``skills/conformer_search/scripts/run.py`` (DESIGN.md
inversion). This module re-exports its ``run`` plus the three dihedral helpers the
scan task reuses, so the engine CLI and sibling tasks keep working against a
single copy of the physics.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skills.conformer_search.scripts.run import (  # noqa: E402,F401
    run,
    _detect_rotatable_bonds,
    _component_excluding,
    _set_dihedral_about_bond,
)

__all__ = [
    "run",
    "_detect_rotatable_bonds",
    "_component_excluding",
    "_set_dihedral_about_bond",
]
