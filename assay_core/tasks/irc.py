"""Re-exports the intrinsic-reaction-coordinate skill's ``run`` for the engine CLI."""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skills.intrinsic_reaction_coordinate.scripts.run import run  # noqa: E402,F401

__all__ = ['run']
