#!/usr/bin/env python3
"""Remove ERRORED agent runs from a fidelity benchmark folder.

An "errored" run is one that did NOT produce a scored PASS/FAIL verdict, using the
SAME definitions the collector uses (see benchmarks/collect_results.py):

  * CRASHED  — the run started (has meta.json) but never wrote a result.json, i.e.
               the driver died before scoring; and
  * SCORED-ERROR — the run wrote a result.json whose ``overall == "ERROR"``
               (exit_code 2): data-integrity / transport-corruption / identity-
               mismatch runs that are deliberately excluded from fidelity scoring.

Valid PASS/FAIL runs, the ``engine-reference/`` directory, specs, and geometry
files are NEVER touched. Each removed run directory's sibling ``<ts>_<case>.out``
live log is removed with it.

DRY-RUN BY DEFAULT: prints what it would delete and deletes nothing. Pass
``--apply`` to actually remove. Pass ``--all-suites`` to scan every
``*-validation`` folder instead of one.

Usage:
    # Preview errored runs in one suite (default: dry-run):
    python benchmarks/prune_errored_runs.py fukui-reactivity-validation

    # Actually delete them:
    python benchmarks/prune_errored_runs.py fukui-reactivity-validation --apply

    # A path also works, and so does the bare suite name:
    python benchmarks/prune_errored_runs.py benchmarks/fidelity/redox-potential-validation --apply

    # Every suite at once:
    python benchmarks/prune_errored_runs.py --all-suites --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

_REPO = Path(__file__).resolve().parent.parent
_FIDELITY = _REPO / "benchmarks" / "fidelity"
ENGINE_REF_DIRNAME = "engine-reference"


def _resolve_suite(arg: str) -> Path:
    """Accept a bare suite name, a repo-relative path, or an absolute path."""
    p = Path(arg)
    for cand in (p, _FIDELITY / arg, _REPO / arg):
        if cand.is_dir():
            return cand.resolve()
    print(f"error: suite not found: {arg!r} (looked under {_FIDELITY})",
          file=sys.stderr)
    raise SystemExit(2)


def _is_run_dir(d: Path) -> bool:
    """A run folder has a meta.json (written at run start). Distinguishes a real
    run from a per-model grouping dir or engine-reference/."""
    return d.is_dir() and (d / "meta.json").is_file()


def _run_dirs(case_dir: Path) -> List[Path]:
    """Every run folder of a case — flat (<case>/<ts>/) and nested
    (<case>/<model>/<ts>/) alike. engine-reference/ is excluded."""
    runs: List[Path] = []
    for child in case_dir.iterdir():
        if not child.is_dir() or child.name == ENGINE_REF_DIRNAME:
            continue
        if _is_run_dir(child):
            runs.append(child)
        else:  # a per-model subfolder: its children are the runs
            for grand in child.iterdir():
                if _is_run_dir(grand):
                    runs.append(grand)
    return runs


def _errored_reason(run: Path) -> str | None:
    """Return why a run is errored ('crashed' / 'scored-error'), else None."""
    rj = run / "result.json"
    if not rj.is_file():
        return "crashed (no result.json)"
    try:
        overall = json.loads(rj.read_text()).get("overall")
    except (OSError, ValueError):
        # A present-but-unparseable result.json means the run did not complete a
        # valid scoring — treat as crashed.
        return "crashed (unreadable result.json)"
    if overall == "ERROR":
        return "scored ERROR (overall==ERROR)"
    return None


def _case_dirs(suite: Path) -> List[Path]:
    """Molecule/case dirs in a suite (any subdir that isn't the suite's own
    engine-reference and contains run material)."""
    return [d for d in suite.iterdir()
            if d.is_dir() and d.name != ENGINE_REF_DIRNAME]


def find_errored(suite: Path) -> List[Tuple[Path, str]]:
    """All (errored_run_dir, reason) in a suite."""
    out: List[Tuple[Path, str]] = []
    for case in _case_dirs(suite):
        for run in _run_dirs(case):
            reason = _errored_reason(run)
            if reason:
                out.append((run, reason))
    return out


def _sibling_out(run: Path) -> Path:
    """The per-run live log written next to the run dir: <ts>_<case>.out."""
    return run.with_name(run.name + ".out")


def prune_suite(suite: Path, apply: bool) -> Tuple[int, int]:
    """Preview or remove errored runs in one suite. Returns (n_runs, n_out_logs)."""
    errored = find_errored(suite)
    if not errored:
        print(f"[{suite.name}] no errored runs found.")
        return 0, 0

    print(f"[{suite.name}] {len(errored)} errored run(s)"
          + ("" if apply else " — DRY RUN, nothing deleted"))
    n_out = 0
    for run, reason in errored:
        rel = run.relative_to(_REPO)
        out_log = _sibling_out(run)
        has_out = out_log.is_file()
        tag = "DELETE" if apply else "would delete"
        print(f"  {tag}: {rel}  [{reason}]"
              + (f"  (+ {out_log.name})" if has_out else ""))
        if apply:
            shutil.rmtree(run, ignore_errors=True)
            if has_out:
                try:
                    out_log.unlink()
                    n_out += 1
                except OSError:
                    pass
        elif has_out:
            n_out += 1
    return len(errored), n_out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Remove errored (crashed / scored-ERROR) runs from a fidelity "
                    "benchmark suite. Dry-run by default.")
    ap.add_argument("suite", nargs="?",
                    help="suite name (e.g. fukui-reactivity-validation) or path; "
                         "omit with --all-suites")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default: dry-run preview only)")
    ap.add_argument("--all-suites", action="store_true",
                    help="scan every *-validation folder under benchmarks/fidelity")
    args = ap.parse_args()

    if args.all_suites:
        suites = sorted(p for p in _FIDELITY.glob("*-validation") if p.is_dir())
    elif args.suite:
        suites = [_resolve_suite(args.suite)]
    else:
        ap.error("give a suite name/path, or use --all-suites")

    total_runs = total_out = 0
    for suite in suites:
        n_runs, n_out = prune_suite(suite, args.apply)
        total_runs += n_runs
        total_out += n_out

    verb = "removed" if args.apply else "would remove"
    print(f"\n{verb} {total_runs} errored run dir(s) and {total_out} .out log(s)"
          f" across {len(suites)} suite(s).")
    if not args.apply and total_runs:
        print("Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
