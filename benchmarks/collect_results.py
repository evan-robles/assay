#!/usr/bin/env python3
"""Collect fidelity-run results into a summary CSV + console table.

Walks each case folder under a validation directory (default the committed
single-point validation set), reads the latest run's result.json + meta.json
(+ engine_reference.json / agent_run.json for the level of theory), and emits one
row per case: the spec name, mode/expect, model, the level of theory actually run
(method / solvent / functional / basis / tier), the overall verdict, and a
compact pass/fail breakdown of the individual checks. The LoT columns let the
fidelity table be sliced by method/level — empty where a field does not apply
(functional/basis on xtb/mopac, solvent in gas phase).

Usage:
    # Env: anl_env
    python benchmarks/collect_results.py                 # default dir, prints table + writes CSV
    python benchmarks/collect_results.py --dir <path>    # collect a different validation dir
    python benchmarks/collect_results.py --csv out.csv   # choose the CSV path

The finding keys differ by mode (compute: layer_B_invocation + layer_C_reporting;
refusal: refusal_fidelity; failure: failure_handling); all are handled.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _REPO / "benchmarks" / "fidelity" / "single-point-validation"

# Fixed-name engine-reference folder (sibling of agent-run folders under a
# molecule). The engine reference moved here from inside each run folder; keep
# the old in-run location as a fallback so pre-refactor runs still collect.
ENGINE_REF_DIRNAME = "engine-reference"


def _engine_reference_json(run: Path) -> Optional[Path]:
    """Locate engine_reference.json for an agent-run folder.

    New layout: a sibling engine-reference/ child of the molecule folder
    (run.parent). Old layout: inside the run folder itself. Returns the first
    that exists, else None.
    """
    sibling = run.parent / ENGINE_REF_DIRNAME / "engine_reference.json"
    if sibling.is_file():
        return sibling
    legacy = run / "engine_reference.json"
    return legacy if legacy.is_file() else None

# Reuse the driver's method-string parser so collection and scoring agree on how
# 'b3lyp/def2-tzvp' is split into functional/basis (frontier-orbitals & fukui
# report the level of theory only as that combined string, not separate fields).
sys.path.insert(0, str(_REPO / "benchmarks"))
try:
    from fidelity_driver import (_parse_method_lot, _result_field,  # type: ignore
                                 _coerce_float)
except Exception:  # pragma: no cover - fallback if import path differs
    def _parse_method_lot(method: Any) -> Dict[str, Optional[str]]:
        out: Dict[str, Optional[str]] = {"functional": None, "basis": None}
        if isinstance(method, str) and "/" in method:
            func, _, basis = method.partition("/")
            out["functional"] = func.strip() or None
            out["basis"] = basis.strip() or None
        return out

    def _result_field(result: Dict[str, Any], key: str) -> Any:  # minimal fallback
        return (result or {}).get(key)

    def _coerce_float(v: Any, field: Optional[str] = None, **_: Any):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

# Skills whose spec has report_value_field=None still have a natural scalar
# "headline" result worth tabulating. Map each to the engine-output field that
# best represents what the skill produced. (Everything else uses the spec's
# report_value_field directly — no per-skill config needed.)
_HEADLINE_FALLBACK = {
    "build-from-smiles": "n_atoms",
    "conformer-search": "n_conformers_kept",
    "visualize-orbitals": "n_orbitals",
    "fukui-reactivity": "n_atoms",
}

# result.json keys that hold lists of per-check findings, across all modes.
_FINDING_KEYS = ["layer_B_invocation", "layer_C_reporting",
                 "refusal_fidelity", "failure_handling"]


def _latest_run(case_dir: Path) -> Optional[Path]:
    """Return the newest timestamped agent-run subdir that has a result.json.

    The engine-reference/ folder is skipped explicitly (it holds the shared
    engine reference, not a scored agent run, and has no result.json anyway)."""
    runs = [d for d in case_dir.iterdir()
            if d.is_dir() and d.name != ENGINE_REF_DIRNAME
            and (d / "result.json").is_file()]
    if not runs:
        return None
    return sorted(runs, key=lambda d: d.name)[-1]


def _findings(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key in _FINDING_KEYS:
        for f in result.get(key) or []:
            out.append(f)
    return out


def _lot_fields(run: Path, result: Dict[str, Any]) -> Dict[str, str]:
    """Resolve the level of theory actually run for this case: method, solvent,
    functional, basis, tier.

    Source order (most authoritative first):
      1. engine_reference.json  — the engine's own reference run (ground truth).
      2. agent_run.json -> result_json — the agent's actual call parameters.
      3. result.json layer_B 'method'/'solvent' `got` values — last resort.
    functional/basis fall back to parsing the combined `method` string
    (e.g. 'b3lyp/def2-tzvp'), since frontier-orbitals & fukui report the level
    of theory only that way. Empty string for a field that does not apply
    (e.g. solvent/functional on a gas-phase xtb run)."""
    src: Dict[str, Any] = {}
    ref = _engine_reference_json(run)
    if ref is not None:
        try:
            src = json.loads(ref.read_text())
        except Exception:
            src = {}
    if not src:
        ar = run / "agent_run.json"
        if ar.is_file():
            try:
                src = (json.loads(ar.read_text()) or {}).get("result_json", {}) or {}
            except Exception:
                src = {}

    def _g(key: str) -> Optional[Any]:
        v = src.get(key)
        return v if v not in (None, "") else None

    method = _g("method")
    solvent = _g("solvent")
    functional = _g("functional")
    basis = _g("basis")
    tier = _g("tier")

    # Last-resort method/solvent from the scored layer_B 'got' values.
    if method is None or solvent is None:
        for f in result.get("layer_B_invocation") or []:
            if method is None and f.get("check") == "method":
                method = f.get("got")
            if solvent is None and f.get("check") == "solvent":
                solvent = f.get("got")

    # functional/basis from the combined method string when absent.
    if (functional is None or basis is None) and isinstance(method, str):
        parsed = _parse_method_lot(method)
        functional = functional or parsed.get("functional")
        basis = basis or parsed.get("basis")

    return {
        "method": str(method) if method is not None else "",
        "solvent": str(solvent) if solvent is not None else "",
        "functional": str(functional) if functional is not None else "",
        "basis": str(basis) if basis is not None else "",
        "tier": str(tier) if tier is not None else "",
    }


def _fmt(v: Any) -> str:
    """Compact string for a value cell: '' for None, trimmed float, else str."""
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _headline_fields(run: Path, spec: Dict[str, Any], result: Dict[str, Any],
                     agent: Dict[str, Any]) -> Dict[str, str]:
    """Extract the per-skill headline scientific result: the field name, the
    engine's (truth) value, and the agent's reported value, plus the
    vibrational-only extras (n_imaginary_modes, zpe_eV).

    The field is the spec's report_value_field; when that is null (build,
    conformer-search, fukui, visualize-orbitals) it falls back to a per-skill
    natural headline (_HEADLINE_FALLBACK). truth comes from engine_reference.json;
    reported comes from the agent's captured record (coerced like the scorer does)."""
    skill = spec.get("skill", "")
    field = spec.get("report_value_field") or _HEADLINE_FALLBACK.get(skill)

    # Engine reference (truth) — the authoritative computed value.
    ref: Dict[str, Any] = {}
    rp = _engine_reference_json(run)
    if rp is not None:
        try:
            ref = json.loads(rp.read_text())
        except Exception:
            ref = {}

    truth_val = _result_field(ref, field) if field else None
    rep_raw = (agent.get("reported", {}) or {}).get(field) if field else None
    # Coerce the agent's value the same way the scorer does (handles dicts /
    # strings-with-units), disambiguating against truth where possible.
    tnum = _coerce_float(truth_val, field) if truth_val is not None else None
    rep_val = _coerce_float(rep_raw, field, truth=tnum) if rep_raw is not None else None
    # Prefer the coerced number; fall back to the raw reported value as text.
    reported_out = rep_val if rep_val is not None else rep_raw

    out = {
        "value_field": field or "",
        "truth_value": _fmt(truth_val),
        "reported_value": _fmt(reported_out),
    }

    # Vibrational-only scientific extras (empty for every other skill).
    if skill == "vibrational-analysis":
        out["n_imaginary"] = _fmt(_result_field(ref, "n_imaginary_modes"))
        out["zpe_eV"] = _fmt(_result_field(ref, "zpe_eV"))
    else:
        out["n_imaginary"] = ""
        out["zpe_eV"] = ""
    return out


def collect(base: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        run = _latest_run(case_dir)
        if run is None:
            continue
        result = json.loads((run / "result.json").read_text())
        meta = {}
        if (run / "meta.json").is_file():
            meta = json.loads((run / "meta.json").read_text())

        findings = _findings(result)
        failed = [f.get("check") for f in findings if not f.get("ok")]
        lot = _lot_fields(run, result)

        # Per-skill headline scientific result needs the spec (report_value_field,
        # skill). Read it from the case folder; tolerate its absence.
        spec: Dict[str, Any] = {}
        specs = sorted(case_dir.glob("*.spec.json"))
        agent_run: Dict[str, Any] = {}
        if specs:
            try:
                spec = json.loads(specs[0].read_text())
            except Exception:
                spec = {}
        if (run / "agent_run.json").is_file():
            try:
                agent_run = json.loads((run / "agent_run.json").read_text())
            except Exception:
                agent_run = {}
        head = _headline_fields(run, spec, result, agent_run)

        rows.append({
            "case": case_dir.name,
            "expect": result.get("expect", "compute"),
            "mode": result.get("mode", meta.get("mode", "")),
            "model": meta.get("model") or "",
            # Level of theory actually run (method / solvent / functional / basis
            # / tier). Empty where a field does not apply (e.g. functional on xtb,
            # solvent in gas phase). See _lot_fields for the source order.
            "method": lot["method"],
            "solvent": lot["solvent"],
            "functional": lot["functional"],
            "basis": lot["basis"],
            "tier": lot["tier"],
            # Per-skill headline scientific result: the field name, the engine's
            # (truth) value, and the agent's reported value, side by side. Plus
            # vibrational-only extras (n_imaginary, zpe_eV). See _headline_fields.
            "value_field": head["value_field"],
            "truth_value": head["truth_value"],
            "reported_value": head["reported_value"],
            "n_imaginary": head["n_imaginary"],
            "zpe_eV": head["zpe_eV"],
            "determinism": "pass" if result.get("layer_A_determinism") else "FAIL",
            "overall": result.get("overall", "?"),
            "failed_checks": "; ".join(c for c in failed if c) or "",
        })
    return rows


def _print_table(rows: List[Dict[str, Any]]) -> None:
    # method shown inline plus the headline value comparison (truth vs reported)
    # and the imaginary-mode count (populated for vibrational analysis; blank
    # elsewhere); functional/basis/tier/solvent/zpe stay in the CSV to keep the
    # console narrow.
    cols = ["case", "expect", "overall", "method",
            "value_field", "truth_value", "reported_value", "n_imaginary"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols} if rows else {}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))
    n_pass = sum(1 for r in rows if r["overall"] == "PASS")
    print(f"\n{n_pass}/{len(rows)} cases PASS")


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect fidelity results into a summary")
    ap.add_argument("--dir", default=str(_DEFAULT_DIR),
                    help="validation directory of case folders")
    ap.add_argument("--csv", default=None, help="CSV output path (default: <dir>/summary.csv)")
    args = ap.parse_args()

    base = Path(args.dir)
    if not base.is_dir():
        print(f"error: not a directory: {base}")
        return 2
    rows = collect(base)
    if not rows:
        print(f"No scored runs found under {base}")
        return 1

    _print_table(rows)

    csv_path = Path(args.csv) if args.csv else base / "summary.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV written: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())