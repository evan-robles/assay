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
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

_REPO = Path(__file__).resolve().parent.parent
_DRIVER = _REPO / "benchmarks" / "fidelity_driver.py"

# Reuse the driver's exact model->folder slug so the "already run?" check matches
# the folder names the driver actually writes (argo:o3 -> argo_o3). Also reuse
# its base-URL resolution (CHEMKIT_LLM_BASE_URL from .env.local) for --all-models.
sys.path.insert(0, str(_REPO / "benchmarks"))
from fidelity_driver import _fs_safe, _ARGO_BASE_URL  # noqa: E402


def _find_spec(case_dir: Path) -> Optional[Path]:
    specs = sorted(case_dir.glob("*.spec.json"))
    return specs[0] if specs else None


def _engine_ref_cached(case_dir: Path, spec: Optional[Path]) -> bool:
    """Conservative check: does this molecule already have an engine-reference
    on disk? Used ONLY to decide whether the parallel-mode serial warmup needs
    to compute it first (race-avoidance). We deliberately do a cheap existence
    check (engine-reference/engine_reference.json present) rather than the
    driver's full hash-validity check: if a cached reference is present but
    STALE, the driver recomputes it on the warmup call anyway — and if it's
    absent, we must warm it. Either way a present file means no two parallel
    workers will race to CREATE it. A missing spec is treated as 'needs warm'
    so the driver surfaces the error serially, not inside a race."""
    if spec is None:
        return True  # nothing to warm; let the worker surface the missing spec
    ref = case_dir / "engine-reference" / "engine_reference.json"
    return ref.is_file()


def _fetch_all_models() -> List[str]:
    """Fetch chat-capable model ids from the argo-proxy /v1/models endpoint.

    Keeps `argo:` models, drops the text-embedding models (they can't tool-call).
    Base URL is the driver's resolved _ARGO_BASE_URL (CHEMKIT_LLM_BASE_URL from
    .env.local). Raises SystemExit with a clear message if the proxy is
    unreachable (wrong port / not running)."""
    url = _ARGO_BASE_URL.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SystemExit(
            f"error: --all-models could not fetch {url} ({e}). "
            "Is argo-proxy running on that port? "
            "(set CHEMKIT_LLM_BASE_URL in benchmarks/fidelity/.env.local)"
        )
    ids = [m.get("id", "") for m in data.get("data", [])]
    models = sorted(
        i for i in ids
        if i.startswith("argo:") and "embed" not in i
    )
    if not models:
        raise SystemExit(f"error: /v1/models returned no usable chat models: {ids}")
    return models


def _model_already_run(case_dir: Path, model: Optional[str]) -> bool:
    """True if `case_dir` already has a completed run for `model` — i.e. the
    per-model subfolder `<case>/<fs_safe(model)>/` contains at least one
    timestamped run with a result.json. Lets a multi-model sweep be resumed
    without redoing work. A None model (driver default, untagged run) is never
    treated as already-run (we can't disambiguate it)."""
    if model is None:
        return False
    model_dir = case_dir / _fs_safe(model)
    if not model_dir.is_dir():
        return False
    for d in model_dir.iterdir():
        if d.is_dir() and (d / "result.json").is_file():
            return True
    return False


def _scored_run_count(case_dir: Path, model: Optional[str]) -> int:
    """How many SCORED (PASS/FAIL) runs already exist for this (case, model).

    Counts only result.json files whose `overall` is PASS or FAIL — an ERROR run
    (crash / transport corruption / dead node) is NOT a valid data point, so it
    does not count toward the repeat target and its slot is refilled on the next
    run. This is what lets a re-invocation TOP UP to the target `repeat` instead
    of either skipping wholesale (old binary resume) or duplicating everything
    (naive --repeat). A None model can't be disambiguated, so returns 0."""
    if model is None:
        return 0
    model_dir = case_dir / _fs_safe(model)
    if not model_dir.is_dir():
        return 0
    n = 0
    for d in model_dir.iterdir():
        rj = d / "result.json"
        if d.is_dir() and rj.is_file():
            try:
                if json.loads(rj.read_text()).get("overall") in ("PASS", "FAIL"):
                    n += 1
            except (OSError, ValueError):
                pass  # unreadable/partial result.json — not a scored run
    return n


def _run_one(case_dir: Path, spec: Path, *, live: bool,
             agent_run_name: Optional[str], out_dir: Optional[str],
             model: Optional[str], refresh_engine: bool) -> dict:
    """Run the driver on a single (case, model) and return its result record."""
    cmd = [sys.executable, str(_DRIVER), "--spec", str(spec)]
    if refresh_engine:
        cmd.append("--refresh-engine")
    if live:
        cmd.append("--live")
        if model:
            cmd += ["--model", model]
    elif agent_run_name:
        ar = case_dir / agent_run_name
        if not ar.is_file():
            print(f"[suite] {case_dir.name}: no {agent_run_name}, skipping")
            return {"case": case_dir.name, "model": model, "ran": False}
        cmd += ["--agent-run", str(ar)]
    # Default: write each case's run into its OWN molecule folder. An explicit
    # shared --out-dir is made PER-CASE by appending the case name, so distinct
    # specs never collapse onto one molecule_dir (which would make them share —
    # and thrash — a single unscoped engine-reference/; see the shared-out-dir
    # collision fix). This mirrors the default's per-case structure.
    case_out = str(Path(out_dir) / case_dir.name) if out_dir else str(case_dir)
    cmd += ["--out-dir", case_out]

    proc = subprocess.run(cmd, cwd=str(_REPO))
    # Driver exit codes: 0 = PASS, 1 = scored FAIL, 2 = CRASH (unhandled
    # exception before scoring). Surface a crash distinctly as "errored" so the
    # roll-up reports ERROR, not a misleading FAIL.
    errored = proc.returncode == 2
    return {"case": case_dir.name, "model": model, "ran": True,
            "exit_code": proc.returncode, "pass": proc.returncode == 0,
            "errored": errored}


def _run_one_model(model: Optional[str], cases: List[Path], *, live: bool,
                   agent_run_name: Optional[str], out_dir: Optional[str],
                   refresh_engine: bool, force: bool, repeat: int,
                   quiet: bool = False) -> List[dict]:
    """Run the whole case list for a SINGLE model, serially. This is one
    "worker" — the unit that --jobs runs concurrently (mirrors Aurora's
    one-model-per-node). Each model writes only into its own
    `<case>/<fs_safe(model)>/` subfolders, so distinct models never contend
    (safe to run in parallel). Returns this model's result records.

    `quiet` prefixes lines with the model label instead of a banner, so
    interleaved output from concurrent workers stays attributable."""
    label = model or "(default)"
    out: List[dict] = []
    if not quiet:
        print(f"\n########## MODEL: {label} ##########")
    for case_dir in cases:
        spec = _find_spec(case_dir)
        # TOP-UP resume (unless --force): run only the reps still MISSING to reach
        # `repeat` SCORED (PASS/FAIL) runs. Existing scored runs are kept; ERROR
        # runs don't count (their slots are refilled). This makes re-invoking the
        # command idempotent toward the target: a (case,model) already at `repeat`
        # runs nothing; one short by k runs exactly k. --force ignores existing
        # runs and executes the full `repeat` fresh (e.g. after a spec change).
        if live and not force:
            have = _scored_run_count(case_dir, model)
            need = max(0, repeat - have)
            if need == 0:
                print(f"[{label}] {case_dir.name}: {have}/{repeat} scored, skipping"
                      if quiet else
                      f"[suite] {case_dir.name} [{label}]: already {have}/{repeat} "
                      f"scored, skipping")
                out.append({"case": case_dir.name, "model": model,
                            "ran": False, "skipped": True})
                continue
            if have > 0:
                print(f"[{label}] {case_dir.name}: {have}/{repeat} scored, "
                      f"running {need} more"
                      if quiet else
                      f"[suite] {case_dir.name} [{label}]: resuming, {have}/{repeat} "
                      f"scored, running {need} more")
        else:
            need = repeat  # --force (or recorded mode): run the full count fresh
        for rep in range(need):
            tag = f" ({rep + 1}/{need})" if need > 1 else ""
            print(f"[{label}] {case_dir.name}{tag}: running"
                  if quiet else
                  f"\n===== {case_dir.name}  [{label}]{tag} =====")
            out.append(_run_one(
                case_dir, spec, live=live, agent_run_name=agent_run_name,
                out_dir=out_dir, model=model, refresh_engine=refresh_engine))
    return out


def run_suite(folder: Path, *, live: bool, agent_run_name: Optional[str],
              out_dir: Optional[str], models: Optional[List[Optional[str]]] = None,
              refresh_engine: bool = False, force: bool = False,
              repeat: int = 1, jobs: int = 1) -> List[dict]:
    """Run the driver over every case in `folder`, once per model in `models`.

    `models` is a list of agent model ids (live mode); a single-element [None]
    means "use the driver's default model" / recorded mode. For each (model,
    case) in live mode, a case already run for that model (a run under
    `<case>/<fs_safe(model)>/` with result.json) is SKIPPED unless `force` —
    making a multi-model sweep resumable. Each model writes into its own
    per-model subfolder, so this is safe to shard across nodes (one model
    subset per node) with no cross-model contention.

    `repeat` (N >= 1): run each (case, model) N FRESH times this invocation to
    measure a flaky model's pass RATE rather than a single coin-flip verdict.

    `jobs` (N >= 1): run N MODELS concurrently, each as its own worker serial
    over the cases (mirrors Aurora's one-model-per-node parallelism). Because
    every model writes only into disjoint `<case>/<model>/` folders there is no
    cross-model contention, so this is safe. jobs=1 is the original fully-serial
    behavior. Concurrency here helps a lot even on one machine because agent
    runs are dominated by the LLM round-trip (argo) wait, not local CPU."""
    cases = [p for p in sorted(folder.iterdir())
             if p.is_dir() and _find_spec(p) is not None]
    models = models or [None]

    if jobs <= 1 or len(models) <= 1:
        results: List[dict] = []
        for model in models:
            results.extend(_run_one_model(
                model, cases, live=live, agent_run_name=agent_run_name,
                out_dir=out_dir, refresh_engine=refresh_engine, force=force,
                repeat=repeat, quiet=False))
        return results

    # WARMUP (serial, before any parallel worker): each molecule's
    # engine-reference/ is created on first touch. If two model-workers hit a
    # molecule whose reference is not yet cached AT THE SAME TIME, they race to
    # create it (corrupt/duplicate reference). Aurora avoided this with a serial
    # STEP-1 warmup; we do the same. Run one NON-live driver call per molecule
    # that still lacks a valid cached reference, serially, so by the time the
    # parallel workers start every reference exists and workers only READ it.
    # Molecules that already have a cached reference cost ~nothing (the driver
    # reuses it), so this is cheap for the already-scaffolded suites.
    if live:
        need_warm = [c for c in cases
                     if not _engine_ref_cached(c, _find_spec(c))]
        if need_warm:
            print(f"[suite] warmup: computing engine-reference for "
                  f"{len(need_warm)} molecule(s) serially (race-avoidance) ...")
            for c in need_warm:
                spec = _find_spec(c)
                print(f"[warmup] {c.name}")
                # non-live driver call: builds engine-reference/, no agent, no model
                subprocess.run([sys.executable, str(_DRIVER), "--spec", str(spec),
                                "--out-dir", str(c)], cwd=str(_REPO))

    # Parallel: one worker per model, up to `jobs` at a time. Threads suffice —
    # each worker only blocks on subprocess.run (the driver runs in its own
    # process), so the GIL is not a bottleneck. Output is prefixed per model.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    n = min(jobs, len(models))
    print(f"[suite] parallel: {n} concurrent model-workers over "
          f"{len(models)} model(s)")
    results = []
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(
            _run_one_model, m, cases, live=live, agent_run_name=agent_run_name,
            out_dir=out_dir, refresh_engine=refresh_engine, force=force,
            repeat=repeat, quiet=True): m for m in models}
        for fut in as_completed(futs):
            m = futs[fut]
            try:
                results.extend(fut.result())
            except Exception as e:  # a worker crashing must not lose the rest
                print(f"[suite] worker for model {m or '(default)'} raised: {e}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the fidelity driver over a suite folder")
    ap.add_argument("folder", help="suite folder of case subfolders")
    ap.add_argument("--live", action="store_true", help="run each case with a live agent")
    ap.add_argument("--agent-run-name", default=None,
                    help="recorded agent-run record filename inside each case folder")
    ap.add_argument("--out-dir", default=None, help="pass-through --out-dir for runs")
    ap.add_argument("--model", nargs="*", default=None,
                    help="one OR MORE agent models for --live runs, called via "
                         "argo-proxy (e.g. --model argo:o3 argo:gpt-4o). The whole "
                         "suite is run once per model; each model's runs go in its "
                         "own <case>/<model>/ subfolder. Already-run (case,model) "
                         "pairs are skipped.")
    ap.add_argument("--all-models", action="store_true",
                    help="run EVERY chat model the argo-proxy /v1/models lists "
                         "(skips text-embedding models). Large/expensive on a DFT "
                         "suite. Combine with --shard to split across nodes.")
    ap.add_argument("--shard", default=None, metavar="i/N",
                    help="run only this node's slice of the model list: --shard 1/4 "
                         "runs models[0::4]-style shard 1 of 4. Shard by model so "
                         "nodes write disjoint <case>/<model>/ subfolders (no "
                         "contention).")
    ap.add_argument("--force", action="store_true",
                    help="ignore existing runs and execute the full --repeat count "
                         "fresh for every (case,model). Default is top-up resume: "
                         "run only the reps missing to reach --repeat scored runs.")
    ap.add_argument("--refresh-engine", action="store_true",
                    help="pass-through to the driver's --refresh-engine: force a "
                         "fresh per-molecule engine-reference/ even if cached.")
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="TARGET number of SCORED (PASS/FAIL) runs per (case,model) "
                         "to measure a flaky model's pass RATE (default 1). Resume is "
                         "TOP-UP: a re-invocation runs only the reps still missing to "
                         "reach N (ERROR runs don't count and are refilled), so the "
                         "command is idempotent toward the target and never "
                         "duplicates completed reps. Use --force to ignore existing "
                         "runs and execute N fresh. N>1 AUTO-COLLECTS at the end "
                         "(aggregates the N newest runs into pass_rate + modal "
                         "verdict + failed-check tally).")
    ap.add_argument("--jobs", "-j", type=int, default=1, metavar="N",
                    help="run N MODELS concurrently, each a worker serial over the "
                         "cases (mirrors Aurora's one-model-per-node parallelism). "
                         "Default 1 (serial). Safe because each model writes only "
                         "into its own <case>/<model>/ folders. Big speedup even on "
                         "one machine since agent runs are argo-round-trip bound. "
                         "For DFT-heavy suites, lower --jobs and/or set "
                         "CHEMKIT_PYSCF_THREADS to avoid CPU oversubscription.")
    ap.add_argument("--collect", action="store_true",
                    help="after running, collect results into a summary table + CSV")
    args = ap.parse_args()
    if args.repeat < 1:
        print(f"error: --repeat must be >= 1 (got {args.repeat})")
        return 2
    if args.jobs < 1:
        print(f"error: --jobs must be >= 1 (got {args.jobs})")
        return 2

    # When running models concurrently, cap each engine's thread pool so N
    # parallel runs don't oversubscribe the cores (N workers x many threads ->
    # thrash / pyscf "NUM_THREADS exceeded" segfaults). Only set a default if the
    # user hasn't already chosen one, and only in parallel mode. xtb-heavy suites
    # barely use it; DFT suites benefit from the user tuning this explicitly.
    if args.jobs > 1 and "CHEMKIT_PYSCF_THREADS" not in os.environ:
        cores = os.cpu_count() or 4
        per = max(1, cores // args.jobs)
        os.environ["CHEMKIT_PYSCF_THREADS"] = str(per)
        print(f"[suite] parallel engine-thread cap: CHEMKIT_PYSCF_THREADS={per} "
              f"({cores} cores / {args.jobs} jobs)")

    # Resolve the suite folder robustly: as given, or relative to the repo root,
    # so it works whether you run from the repo root or from inside benchmarks/.
    folder = Path(args.folder)
    if not folder.is_dir():
        alt = _REPO / args.folder
        if alt.is_dir():
            folder = alt
        else:
            print(f"error: not a directory: {args.folder} "
                  f"(also tried {alt})")
            return 2
    if not args.live and not args.agent_run_name:
        print("error: choose --live or --agent-run-name <file>")
        return 2

    # Resolve the model list. Models only apply to live mode.
    explicit_models = list(args.model or [])
    if (args.all_models or explicit_models) and not args.live:
        print("error: --model / --all-models only apply to --live runs")
        return 2
    if args.all_models:
        models: List[Optional[str]] = _fetch_all_models()
    elif explicit_models:
        models = list(explicit_models)
    else:
        models = [None]  # single default-model (live) or recorded mode

    # Optional per-node sharding: --shard i/N keeps this node's slice of `models`
    # (1-based i). Shard by model so nodes write disjoint <case>/<model>/ subfolders.
    if args.shard:
        try:
            i_str, n_str = args.shard.split("/")
            i, n = int(i_str), int(n_str)
            assert 1 <= i <= n
        except (ValueError, AssertionError):
            print(f"error: --shard must be 'i/N' with 1<=i<=N (got {args.shard!r})")
            return 2
        models = [m for idx, m in enumerate(models) if idx % n == (i - 1)]
        if not models:
            print(f"[suite] shard {i}/{n}: no models in this slice; nothing to do")
            return 0

    if args.all_models or len(models) > 1 or args.shard:
        shown = [m or "(default)" for m in models]
        print(f"[suite] running {len(models)} model(s): {', '.join(shown)}")

    results = run_suite(folder, live=args.live,
                        agent_run_name=args.agent_run_name, out_dir=args.out_dir,
                        models=models, refresh_engine=args.refresh_engine,
                        force=args.force, repeat=args.repeat, jobs=args.jobs)

    ran = [r for r in results if r.get("ran")]
    passed = [r for r in ran if r.get("pass")]
    errored = [r for r in ran if r.get("errored")]
    skipped = [r for r in results if r.get("skipped")]
    # Per-model roll-up. ERRORED runs (driver crashed before scoring, exit 2) are
    # reported distinctly from scored FAILs — never hidden, never mislabeled.
    print(f"\n===== suite roll-up: {len(passed)}/{len(ran)} ran-PASS"
          f"{f', {len(errored)} ERRORED' if errored else ''}"
          f"{f', {len(skipped)} skipped' if skipped else ''} =====")
    by_model: dict = {}
    for r in ran:
        m = r.get("model") or "(default)"
        by_model.setdefault(m, [0, 0, 0])   # [pass, total, error]
        by_model[m][1] += 1
        if r.get("pass"):
            by_model[m][0] += 1
        if r.get("errored"):
            by_model[m][2] += 1
    for m, (p, t, e) in sorted(by_model.items()):
        print(f"  {m}: {p}/{t} PASS" + (f"  ({e} ERRORED)" if e else ""))
    for r in ran:
        if not r.get("pass"):
            tag = "ERROR" if r.get("errored") else "FAIL "
            print(f"  {tag} {r['case']} [{r.get('model') or '(default)'}] "
                  f"(exit {r.get('exit_code')})")

    # --collect regenerates the model-grouped summary table + summary.csv. When
    # asked to collect (explicit --collect, or --repeat N which implies it), we
    # ALWAYS regenerate from the current on-disk results — even if nothing new
    # ran this invocation. Re-collecting a fully-resumed suite is exactly how you
    # refresh a STALE summary.csv (e.g. after manually deleting ERROR runs, or
    # after an earlier run left the CSV out of date): the whole point of asking
    # to collect is to get a summary that matches disk. (An earlier version
    # skipped regeneration on "nothing new ran"; with top-up resume that is a
    # common, expected state for a complete suite, so skipping left stale
    # summaries in place.) Collection reads ALL models fresh from their run
    # folders, so models untouched this invocation are still reflected correctly.
    #
    # --repeat N IMPLIES collection: a repeat sweep's whole purpose is the
    # aggregated pass-rate table.
    should_collect = args.collect or args.repeat > 1
    if should_collect:
        # With --repeat N, aggregate the N newest runs per (case,model) into a
        # pass rate (collect_repeats). With a single run, one row per (case,model)
        # (collect_all). Both group by model and use the same grouped-CSV writer.
        from collect_results import (collect_all, collect_repeats,
                                     _print_table, _print_repeat_table,
                                     write_grouped_csv)
        if args.repeat > 1:
            rows = collect_repeats(folder, n=args.repeat)
            printer = _print_repeat_table
            _cap = args.repeat
        else:
            rows = collect_all(folder)
            printer = _print_table
            _cap = None
        if rows:
            print()
            printer(rows)
            csv_path = folder / "summary.csv"
            write_grouped_csv(rows, csv_path, base=folder, n=_cap)
            print(f"\nCSV written: {csv_path}")
        else:
            print("\n[suite] no scored results on disk yet; no summary written.")

    # Suite exit code: nonzero if any case failed to pass.
    return 0 if len(passed) == len(ran) and ran else 1


if __name__ == "__main__":
    sys.path.insert(0, str(_REPO / "benchmarks"))
    raise SystemExit(main())
