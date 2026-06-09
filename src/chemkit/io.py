"""I/O helpers: read molecular geometry; write structured JSON result files."""
from __future__ import annotations
import json
import os
import sys
from typing import Any, Dict

from ase.io import read as ase_read


def read_geometry(path: str):
    """Read xyz/sdf/pdb (anything ASE recognizes) and return an Atoms object."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Geometry file not found: {path}")
    atoms = ase_read(path)
    return atoms


def write_result(result: Dict[str, Any], out_path: str) -> str:
    """Write result dict to JSON; create parent dir if missing. Returns abs path."""
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=_default_json)
    return out_path


def _default_json(o):
    try:
        import numpy as np
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
    except ImportError:
        pass
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"Not JSON-serializable: {type(o).__name__}")


def cli_invocation() -> str:
    """Reconstruct the command line that produced this run (for reproducibility)."""
    return " ".join(sys.argv)


