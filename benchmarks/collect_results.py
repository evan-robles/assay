#!/usr/bin/env python3
"""Collect fidelity-run results into a summary CSV + console table.

Walks each case folder under a validation directory (default the committed
single-point validation set), reads the latest run's result.json + meta.json,
and emits one row per case: the spec name, mode/expect, model, overall verdict,
and a compact pass/fail breakdown of the individual checks.

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
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _REPO / "benchmarks" / "fidelity" / "single-point-validation"

# result.json keys that hold lists of per-check findings, across all modes.
_FINDING_KEYS = ["layer_B_invocation", "layer_C_reporting",
                 "refusal_fidelity", "failure_handling"]


def _latest_run(case_dir: Path) -> Optional[Path]:
    """Return the newest timestamped run subdir that has a result.json."""
    runs = [d for d in case_dir.iterdir()
            if d.is_dir() and (d / "result.json").is_file()]
    if not runs:
        return None
    return sorted(runs, key=lambda d: d.name)[-1]


def _findings(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key in _FINDING_KEYS:
        for f in result.get(key) or []:
            out.append(f)
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
        n_total = len(findings)
        n_pass = sum(1 for f in findings if f.get("ok"))
        failed = [f.get("check") for f in findings if not f.get("ok")]

        rows.append({
            "case": case_dir.name,
            "expect": result.get("expect", "compute"),
            "mode": result.get("mode", meta.get("mode", "")),
            "model": meta.get("model") or "",
            "determinism": "pass" if result.get("layer_A_determinism") else "FAIL",
            "overall": result.get("overall", "?"),
            "checks_passed": f"{n_pass}/{n_total}",
            "failed_checks": "; ".join(c for c in failed if c) or "",
        })
    return rows


def _print_table(rows: List[Dict[str, Any]]) -> None:
    cols = ["case", "expect", "overall", "checks_passed", "determinism", "model"]
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