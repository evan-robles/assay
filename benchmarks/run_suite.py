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
    """True if `case_dir` already has a completed run for `model` — i.e. a
    timestamped subfolder ending in `__<fs_safe(model)>` that contains a
    result.json. Lets a multi-model sweep be resumed without redoing work.
    A None model (driver default, untagged folder) is never treated as
    already-run (we can't disambiguate it)."""
    if model is None:
        return False
    suffix = f"__{_fs_safe(model)}"
    for d in case_dir.iterdir():
        if d.is_dir() and d.name.endswith(suffix) and (d / "result.json").is_file():
            return True
    return False


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
    # --out-dir overrides this (e.g. a shared per-model batch dir).
    cmd += ["--out-dir", out_dir if out_dir else str(case_dir)]

    proc = subprocess.run(cmd, cwd=str(_REPO))
    return {"case": case_dir.name, "model": model, "ran": True,
            "exit_code": proc.returncode, "pass": proc.returncode == 0}


def run_suite(folder: Path, *, live: bool, agent_run_name: Optional[str],
              out_dir: Optional[str], models: Optional[List[Optional[str]]] = None,
              refresh_engine: bool = False, force: bool = False) -> List[dict]:
    """Run the driver over every case in `folder`, once per model in `models`.

    `models` is a list of agent model ids (live mode); a single-element [None]
    means "use the driver's default model" / recorded mode. For each (model,
    case) in live mode, a case already run for that model (a `__<slug>` run
    folder with result.json) is SKIPPED unless `force` — making a multi-model
    sweep resumable. Different models write disjoint `__<model>` folders, so this
    is safe to shard across nodes (one model subset per node)."""
    results: List[dict] = []
    cases = [p for p in sorted(folder.iterdir())
             if p.is_dir() and _find_spec(p) is not None]
    models = models or [None]

    for model in models:
        label = model or "(default)"
        print(f"\n########## MODEL: {label} ##########")
        for case_dir in cases:
            spec = _find_spec(case_dir)
            if live and not force and _model_already_run(case_dir, model):
                print(f"[suite] {case_dir.name}: model {label} already run, skipping")
                results.append({"case": case_dir.name, "model": model,
                                "ran": False, "skipped": True})
                continue
            print(f"\n===== {case_dir.name}  [{label}] =====")
            results.append(_run_one(
                case_dir, spec, live=live, agent_run_name=agent_run_name,
                out_dir=out_dir, model=model, refresh_engine=refresh_engine))
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
                         "suite is run once per model; each run folder is tagged "
                         "__<model>. Already-run (case,model) pairs are skipped.")
    ap.add_argument("--all-models", action="store_true",
                    help="run EVERY chat model the argo-proxy /v1/models lists "
                         "(skips text-embedding models). Large/expensive on a DFT "
                         "suite. Combine with --shard to split across nodes.")
    ap.add_argument("--shard", default=None, metavar="i/N",
                    help="run only this node's slice of the model list: --shard 1/4 "
                         "runs models[0::4]-style shard 1 of 4. Shard by model so "
                         "nodes write disjoint __<model> folders (no contention).")
    ap.add_argument("--force", action="store_true",
                    help="re-run even if a (case,model) already has a result "
                         "(default: skip already-run pairs, making sweeps resumable).")
    ap.add_argument("--refresh-engine", action="store_true",
                    help="pass-through to the driver's --refresh-engine: force a "
                         "fresh per-molecule engine-reference/ even if cached.")
    ap.add_argument("--collect", action="store_true",
                    help="after running, collect results into a summary table + CSV")
    args = ap.parse_args()

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
    # (1-based i). Shard by model so nodes write disjoint __<model> folders.
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
                        force=args.force)

    ran = [r for r in results if r.get("ran")]
    passed = [r for r in ran if r.get("pass")]
    skipped = [r for r in results if r.get("skipped")]
    # Per-model roll-up.
    print(f"\n===== suite roll-up: {len(passed)}/{len(ran)} ran-PASS"
          f"{f', {len(skipped)} skipped' if skipped else ''} =====")
    by_model: dict = {}
    for r in ran:
        m = r.get("model") or "(default)"
        by_model.setdefault(m, [0, 0])
        by_model[m][1] += 1
        if r.get("pass"):
            by_model[m][0] += 1
    for m, (p, t) in sorted(by_model.items()):
        print(f"  {m}: {p}/{t} PASS")
    for r in ran:
        if not r.get("pass"):
            print(f"  FAIL  {r['case']} [{r.get('model') or '(default)'}] "
                  f"(exit {r.get('exit_code')})")

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
