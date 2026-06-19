#!/usr/bin/env python3
"""Run the fidelity driver over every case in a suite folder.

A suite folder contains one subfolder per test case, each with a single
`*.spec.json` (and its input geometry). This runs the driver on each case, then
optionally collects a summary. One command validates a whole skill's test set
(e.g. all single-point molecules).

Usage:
    # Env: anl_env
    # Live agent on every case, then print + write the summary:
    python benchmarks/run_suite.py benchmarks/fidelity/single-point-validation --live --collect

    # Recorded mode: each case folder must hold an agent-run record named
    # <case>.agent.json (or pass a glob via --agent-run-name):
    python benchmarks/run_suite.py <folder> --agent-run-name agent_run.json --collect

    # Choose where run artifacts go (default: each spec's own folder via --out-dir):
    python benchmarks/run_suite.py <folder> --live --out-dir runs_o3

Behavior:
    - Continue-on-error: a case whose driver exits nonzero is recorded and the
      suite keeps going; the roll-up reports N pass / M total.
    - --collect re-reads the case folders with collect_results.collect() to print
      the table and write summary.csv (so the roll-up reflects scored results).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

_REPO = Path(__file__).resolve().parent.parent
_DRIVER = _REPO / "benchmarks" / "fidelity_driver.py"


def _find_spec(case_dir: Path) -> Optional[Path]:
    specs = sorted(case_dir.glob("*.spec.json"))
    return specs[0] if specs else None


def run_suite(folder: Path, *, live: bool, agent_run_name: Optional[str],
              out_dir: Optional[str]) -> List[dict]:
    results = []
    cases = sorted(p for p in folder.iterdir() if p.is_dir())
    for case_dir in cases:
        spec = _find_spec(case_dir)
        if spec is None:
            continue  # not a case folder
        cmd = [sys.executable, str(_DRIVER), "--spec", str(spec)]
        if live:
            cmd.append("--live")
        elif agent_run_name:
            ar = case_dir / agent_run_name
            if not ar.is_file():
                print(f"[suite] {case_dir.name}: no {agent_run_name}, skipping")
                results.append({"case": case_dir.name, "ran": False})
                continue
            cmd += ["--agent-run", str(ar)]
        if out_dir:
            cmd += ["--out-dir", out_dir]

        print(f"\n===== {case_dir.name} =====")
        proc = subprocess.run(cmd, cwd=str(_REPO))
        results.append({"case": case_dir.name, "ran": True,
                        "exit_code": proc.returncode,
                        "pass": proc.returncode == 0})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the fidelity driver over a suite folder")
    ap.add_argument("folder", help="suite folder of case subfolders")
    ap.add_argument("--live", action="store_true", help="run each case with a live agent")
    ap.add_argument("--agent-run-name", default=None,
                    help="recorded agent-run record filename inside each case folder")
    ap.add_argument("--out-dir", default=None, help="pass-through --out-dir for runs")
    ap.add_argument("--collect", action="store_true",
                    help="after running, collect results into a summary table + CSV")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"error: not a directory: {folder}")
        return 2
    if not args.live and not args.agent_run_name:
        print("error: choose --live or --agent-run-name <file>")
        return 2

    results = run_suite(folder, live=args.live,
                        agent_run_name=args.agent_run_name, out_dir=args.out_dir)

    ran = [r for r in results if r.get("ran")]
    passed = [r for r in ran if r.get("pass")]
    print(f"\n===== suite roll-up: {len(passed)}/{len(ran)} cases exited PASS =====")
    for r in ran:
        if not r.get("pass"):
            print(f"  FAIL  {r['case']} (exit {r.get('exit_code')})")

    if args.collect:
        from collect_results import collect, _print_table  # local import
        import csv
        rows = collect(folder)
        if rows:
            print()
            _print_table(rows)
            csv_path = folder / "summary.csv"
            with open(csv_path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
            print(f"\nCSV written: {csv_path}")

    # Suite exit code: nonzero if any case failed to pass.
    return 0 if len(passed) == len(ran) and ran else 1


if __name__ == "__main__":
    sys.path.insert(0, str(_REPO / "benchmarks"))
    raise SystemExit(main())
