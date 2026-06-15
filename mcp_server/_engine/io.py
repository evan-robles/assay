"""I/O helpers: read molecular geometry; write structured JSON result files."""
from __future__ import annotations
import json
import math
import os
import pathlib
import sys
from typing import Any, Dict

from ase.io import read as ase_read


def read_geometry(path: str):
    """Read xyz/sdf/pdb (anything ASE recognizes) and return an Atoms object."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Geometry file not found: {path}")
    atoms = ase_read(path)
    return atoms


# Significant figures retained when serializing floats. Eight sig figs is more
# than any chemkit quantity supports (a DFT total energy is meaningful to
# ~µHartree on hundreds of Hartree ≈ 8 sig figs; xtb/PM7 far less), so this only
# trims the meaningless float-repr tail (e.g. -137.96738451827179 ->
# -137.967385) — cutting tokens and false precision without losing any real
# information. Differences are still computed from the FULL-precision in-memory
# result before this write, so chained calculations are unaffected.
_SERIALIZE_SIG_FIGS = 8


def write_result(result: Dict[str, Any], out_path: str) -> str:
    """Write result dict to JSON; create parent dir if missing. Returns abs path.

    NaN and ±Infinity are coerced to None so the output is strict-JSON valid
    (browsers, Go, Rust will choke on `NaN` literals). A failed calculation
    that propagates NaN into a result field is still readable by consumers
    rather than silently producing malformed JSON. Floats are rounded to
    _SERIALIZE_SIG_FIGS significant figures to avoid emitting false precision.
    """
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(_scrub(result, round_sig=True), f, indent=2,
                  default=_default_json, allow_nan=False)
    return out_path


def _round_sig(x: float, sig: int = _SERIALIZE_SIG_FIGS) -> float:
    """Round x to `sig` significant figures. Leaves 0.0 and non-finite alone."""
    if x == 0 or not math.isfinite(x):
        return x
    from math import floor, log10
    return round(x, -int(floor(log10(abs(x)))) + (sig - 1))


def _default_json(o):
    # numpy scalars and arrays — must come before generic .tolist() since
    # np.bool_ defines neither tolist() (it returns a Python bool) nor a
    # numpy.integer/floating MRO link.
    try:
        import numpy as np
        if isinstance(o, np.ndarray):
            return _scrub(o.tolist(), round_sig=True)
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.floating):
            v = float(o)
            if not math.isfinite(v):
                return None
            return _round_sig(v)
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.complexfloating):
            return {"real": float(o.real), "imag": float(o.imag)}
    except ImportError:
        pass
    if isinstance(o, (pathlib.Path, os.PathLike)):
        return os.fspath(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, complex):
        return {"real": o.real, "imag": o.imag}
    if hasattr(o, "tolist"):
        return _scrub(o.tolist())
    raise TypeError(f"Not JSON-serializable: {type(o).__name__}")


def _scrub(value, round_sig: bool = False):
    """Recursively replace non-finite floats with None (the JSON-strict
    representation of NaN/Inf), optionally rounding finite floats to
    _SERIALIZE_SIG_FIGS significant figures. Walks lists/tuples/dicts only —
    leaves everything else alone."""
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return _round_sig(value) if round_sig else value
    if isinstance(value, list):
        return [_scrub(v, round_sig) for v in value]
    if isinstance(value, tuple):
        return [_scrub(v, round_sig) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v, round_sig) for k, v in value.items()}
    return value


def cli_invocation() -> str:
    """Reconstruct the command line that produced this run (for reproducibility)."""
    return " ".join(sys.argv)


