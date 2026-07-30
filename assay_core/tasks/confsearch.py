"""Re-exports the conformer-search skill's ``run`` (and the dihedral helpers reused by scan) for the engine CLI."""
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
