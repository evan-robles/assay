#!/usr/bin/env python3
"""Make fidelity spec prompts explicit about charge and multiplicity.

Most *.spec.json prompts omit charge/multiplicity, yet the scorer's
invocation-fidelity check requires the intended charge/mult. A standards-
compliant agent that STOPS to ask for an unspecified charge/mult (per
calculation-reporting-standards #10: never assume charge/mult) is then
penalized as a non-completion — a benchmark-prompt defect, not an agent error.

This tool appends an explicit "Use charge <c> and multiplicity <m>." clause to
each prompt that (a) has intended.charge and/or intended.multiplicity set, and
(b) does not already mention charge/multiplicity. Idempotent: re-running does
nothing to already-clarified prompts. Tasks with no charge/mult concept
(name-to-smiles) are left untouched.

Usage:
    python tools/clarify_spec_prompts.py [--suite <dir>] [--dry-run]
    (default: all benchmarks/fidelity/*-validation suites)
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_FIDELITY = _REPO / "benchmarks" / "fidelity"


def _needs_clarification(prompt: str) -> bool:
    p = prompt.lower()
    return "charge" not in p and "multiplic" not in p and "spin" not in p


def _clause(charge, mult) -> str:
    parts = []
    if charge is not None:
        parts.append(f"charge {charge}")
    if mult is not None:
        parts.append(f"multiplicity {mult}")
    if not parts:
        return ""
    return "Use " + " and ".join(parts) + "."


def patch_spec(path: Path, dry_run: bool) -> str:
    try:
        spec = json.loads(path.read_text())
    except Exception as e:
        return f"SKIP (unreadable: {e})"
    prompt = spec.get("prompt")
    intended = spec.get("intended") or {}
    if not isinstance(prompt, str):
        return "SKIP (no prompt)"
    charge = intended.get("charge")
    mult = intended.get("multiplicity")
    if charge is None and mult is None:
        return "SKIP (no intended charge/mult — e.g. name-to-smiles)"
    if not _needs_clarification(prompt):
        return "SKIP (already explicit)"
    clause = _clause(charge, mult)
    if not clause:
        return "SKIP (nothing to add)"
    # Insert the clause right before the trailing "Use the chemkit tools…" style
    # sentence if present, else append at the end. Keep it one clean sentence.
    new_prompt = prompt.rstrip()
    if not new_prompt.endswith("."):
        new_prompt += "."
    new_prompt += " " + clause
    spec["prompt"] = new_prompt
    if not dry_run:
        path.write_text(json.dumps(spec, indent=2) + "\n")
    return f"PATCHED (+ '{clause}')"


def main() -> int:
    ap = argparse.ArgumentParser(description="Clarify charge/mult in fidelity spec prompts")
    ap.add_argument("--suite", default=None, help="single suite dir (default: all)")
    ap.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = ap.parse_args()

    if args.suite:
        suites = [Path(args.suite)]
    else:
        suites = sorted(p for p in _FIDELITY.glob("*-validation") if p.is_dir())

    n_patched = 0
    n_total = 0
    for suite in suites:
        specs = sorted(suite.glob("*/*.spec.json"))
        if not specs:
            continue
        print(f"\n=== {suite.name} ===")
        for s in specs:
            n_total += 1
            res = patch_spec(s, args.dry_run)
            if res.startswith("PATCHED"):
                n_patched += 1
            print(f"  {s.parent.name:24} {res}")
    verb = "would patch" if args.dry_run else "patched"
    print(f"\n{verb} {n_patched}/{n_total} specs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
