"""I/O helpers: read molecular geometry; write structured JSON result files."""
from __future__ import annotations
import hashlib
import json
import math
import os
import pathlib
import sys
from typing import Any, Dict, List

from ase.io import read as ase_read


# ── Structured warnings (additive; non-breaking) ──────────────────────────────
# Every task emits `warnings` as a list of free-text strings. That list is left
# UNCHANGED (all existing consumers — the scorer's `warnings preserved` check,
# the CLI compact pointer, the MCP server — keep reading it verbatim). In
# ADDITION we derive `warnings_structured`: the same warnings, each wrapped as
# {"code", "text"} with a short STABLE code. The code lets a downstream interface
# (e.g. the benchmark's final_report) let an agent surface warnings BY REFERENCE
# (short code) instead of retyping long paragraphs verbatim — reducing the
# transcription burden that weak models (observed: gpt-4.1-nano on logp) fail by
# dropping warnings entirely. This is purely additive: it changes NO existing
# field and NO scoring; it only exposes a machine-stable handle on each warning.
#
# The code = "<category>_<hash8>" where category is inferred from keywords (so a
# reader gets a human hint) and hash8 is the first 8 hex of sha256(text) (so the
# code is deterministic and stable for identical warning text across runs, with
# no hand-maintained per-string table across the 19 emitting tasks).
_WARN_CATEGORY_KEYWORDS = (
    ("screening_grade", ("screening-grade", "screening grade", "± ", "±1", "±2", "±3", "typical")),
    ("single_conformer", ("single-conformer", "single conformer", "conformational", "boltzmann")),
    ("thermo_electronic_only", ("electronic-energy", "electronic only", "zpe", "thermal", "entropy", "standard-state", "standard state")),
    ("convergence", ("did not converge", "unconverged", "convergence", "scf")),
    ("imaginary_mode", ("imaginary mode", "imaginary frequenc", "not a true minimum")),
    ("method_suggestion", ("consider", "rdkit", "crippen", "xlogp", "group-contribution")),
    ("element_param", ("parameter", "parametriz", "element", "not parametrized")),
)


def _warn_category(text: str) -> str:
    low = text.lower()
    for cat, kws in _WARN_CATEGORY_KEYWORDS:
        if any(k in low for k in kws):
            return cat
    return "general"


def _warn_code(text: str) -> str:
    """Deterministic, stable short code for a warning string."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{_warn_category(text)}_{h}"


def _structure_warnings(warnings: Any) -> List[Dict[str, str]]:
    """Map a list of warning strings to [{code, text}]. Non-string / non-list
    inputs yield []. Idempotent-safe: if an entry is already a {code,text} dict
    (e.g. a result re-written by confsearch), it is passed through unchanged."""
    if not isinstance(warnings, (list, tuple)):
        return []
    out: List[Dict[str, str]] = []
    for w in warnings:
        if isinstance(w, dict) and "text" in w:
            out.append({"code": str(w.get("code") or _warn_code(str(w["text"]))),
                        "text": str(w["text"])})
        elif isinstance(w, str):
            out.append({"code": _warn_code(w), "text": w})
    return out


# Header line prepended to the copy-ready warnings block. Kept short and
# imperative so an agent relaying the block to a user carries an explicit
# instruction with it.
_WARNINGS_BLOCK_HEADER = (
    "⚠️ Tool warnings — surface ALL of these to the user verbatim "
    "(do not drop, summarize, or paraphrase any):"
)


def _warnings_block(warnings: Any) -> str:
    """Build a single copy-ready markdown string listing every warning verbatim.

    The agent can relay THIS ONE FIELD to its user in a single paste, instead of
    reconstructing a list from the `warnings[]` array — which is where weak
    models drop warnings (observed gpt-4.1-nano). The block is derived from the
    exact warning text (verbatim), so relaying it satisfies the
    'echo every warning verbatim' requirement (calculation-reporting-standards
    §7). Returns '' when there are no warnings (caller then omits the field)."""
    if not isinstance(warnings, (list, tuple)):
        return ""
    texts = []
    for w in warnings:
        if isinstance(w, dict) and "text" in w:
            texts.append(str(w["text"]))
        elif isinstance(w, str):
            texts.append(w)
    if not texts:
        return ""
    return _WARNINGS_BLOCK_HEADER + "\n" + "\n".join(f"- {t}" for t in texts)


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
    # Additive: alongside the untouched free-text `warnings`, emit two derived
    # convenience fields so a real MCP agent can surface warnings faithfully with
    # minimal effort (the original `warnings` field is never modified, so every
    # existing consumer is unaffected):
    #   * `warnings_structured` = [{code, text}] — a machine-stable handle per
    #     warning (reference by short code instead of retyping).
    #   * `warnings_block` = ONE copy-ready markdown string of all warnings
    #     verbatim, headed by a "surface these to the user" instruction. An agent
    #     relays this single field in one paste instead of reconstructing the
    #     list — the step where weak models drop warnings (observed gpt-4.1-nano).
    # Both are added only when there are warnings.
    if isinstance(result, dict) and result.get("warnings"):
        _w = result.get("warnings")
        _add: Dict[str, Any] = {}
        if "warnings_structured" not in result:
            structured = _structure_warnings(_w)
            if structured:
                _add["warnings_structured"] = structured
        if "warnings_block" not in result:
            block = _warnings_block(_w)
            if block:
                _add["warnings_block"] = block
        if _add:
            result = {**result, **_add}
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


