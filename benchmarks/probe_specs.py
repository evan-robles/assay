#!/usr/bin/env python3
"""Engine-level probe of a suite's specs: run the engine on the SMALLEST molecule
in the suite and confirm the spec's `report_value_field` is actually present in
the engine output. This catches the field-name-mismatch class of bug (e.g.
spec says `delta_g_solv_kcal_mol` but the engine emits `delta_G_solv_kcal_mol`)
that silently kills the value gate — something static JSON validation cannot see.

For specs with `report_value_field: null` (conformer-search, fukui,
visualize-orbitals), there is no scalar to check; instead confirm the engine run
exits cleanly and returns a dict (the skill is scored on invocation + warnings).

This is a cheap pre-flight, NOT the full fidelity test. It runs the engine once
per suite (smallest molecule) so DFT suites stay tractable.

Usage:
    # Env: anl_env
    python benchmarks/probe_specs.py benchmarks/fidelity/solvation-validation
    python benchmarks/probe_specs.py --all          # every *-validation suite
"""
from __future__ import annotations
import argparse, json, os, sys, tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "benchmarks"))


def _natoms(xyzrel: str) -> int:
    p = Path(xyzrel) if os.path.isabs(xyzrel) else _REPO / xyzrel
    try:
        return int(p.read_text().splitlines()[0].strip())
    except Exception:
        return 9999


def _smallest_spec(suite: Path):
    """Return (spec_path, spec_dict) for the smallest-by-atom-count case, or the
    first case if no xyz (build skill)."""
    items = []
    for sp in sorted(suite.glob("*/*.spec.json")):
        d = json.load(open(sp))
        n = _natoms(d["xyz"]) if "xyz" in d else 9999
        items.append((n, sp, d))
    if not items:
        return None, None
    items.sort(key=lambda t: t[0])
    return items[0][1], items[0][2]


def probe_suite(suite: Path) -> dict:
    from fidelity_driver import (run_engine, _resolve_xyz, _result_field,
                                 _engine_flags)
    sp, d = _smallest_spec(suite)
    if sp is None:
        return {"suite": suite.name, "ok": False, "detail": "no specs"}

    skill = d["skill"]
    # Build the SAME reference invocation the driver uses: intended_flags plus
    # --charge/--mult/--solvent/tier/etc. derived from the `intended` block.
    # (Using bare intended_flags would drop required flags like --solvent.)
    flags = _engine_flags(d)
    rvf = d.get("report_value_field")

    # positional input: xyz path, build string, or NONE for positional-less
    # multi-input skills (reaction-energy / pka / reaction-profile) whose every
    # geometry arrives via the spec's `inputs` named flags (handled by _engine_flags).
    if "xyz" in d:
        positional = _resolve_xyz(d["xyz"])
    elif "input" in d:
        positional = d.get("input")
    else:
        positional = None

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "probe.json")
        try:
            res = run_engine(skill, flags, positional, out,
                             keep_dir=None, label="probe",
                             tolerate_failure=True)
        except Exception as e:
            return {"suite": suite.name, "skill": skill, "case": Path(sp).parts[-2],
                    "ok": False, "detail": f"engine raised: {type(e).__name__}: {e}"}

    if not isinstance(res, dict):
        return {"suite": suite.name, "skill": skill, "case": Path(sp).parts[-2],
                "ok": False, "detail": f"engine returned non-dict: {type(res)}"}

    # null rvf: just confirm it ran and returned a dict
    if rvf is None:
        return {"suite": suite.name, "skill": skill, "case": Path(sp).parts[-2],
                "ok": True, "rvf": None,
                "detail": "ran OK (no scalar value field to check)",
                "keys_sample": sorted(res.keys())[:8]}

    # non-null rvf: the field MUST be present (this is the gate-killer check)
    val = _result_field(res, rvf)
    present = val is not None
    return {"suite": suite.name, "skill": skill, "case": Path(sp).parts[-2],
            "ok": present, "rvf": rvf, "value": val,
            "detail": ("field present" if present
                       else f"report_value_field {rvf!r} NOT in engine output"),
            "numeric_keys": sorted(k for k in res
                                   if isinstance(res.get(k), (int, float)))[:14]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("suite", nargs="?", help="one *-validation folder")
    ap.add_argument("--all", action="store_true", help="probe every suite")
    args = ap.parse_args()

    if args.all:
        suites = sorted((_REPO / "benchmarks" / "fidelity").glob("*-validation"))
    elif args.suite:
        s = Path(args.suite)
        if not s.is_dir():
            s = _REPO / args.suite
        suites = [s]
    else:
        ap.error("give a suite folder or --all")

    rows = [probe_suite(s) for s in suites]
    fails = [r for r in rows if not r.get("ok")]
    for r in rows:
        mark = "PASS" if r.get("ok") else "FAIL"
        print(f"[{mark}] {r['suite']:32s} {r.get('skill','?'):20s} "
              f"rvf={r.get('rvf')!r:28s} {r['detail']}")
        if not r.get("ok") and r.get("numeric_keys"):
            print(f"       engine numeric keys: {r['numeric_keys']}")
    print(f"\n{len(rows)-len(fails)}/{len(rows)} suites probe-OK")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
