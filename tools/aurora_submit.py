#!/usr/bin/env python3
"""Submit ASSAY jobs to the Aurora supercomputer (PBS Pro).

This is a standalone ORCHESTRATION tool, not an ASSAY chemistry skill / MCP tool.
A PBS job is asynchronous (submit now, results in minutes-to-hours), which does
not fit ASSAY's synchronous MCP-tool pattern -- so this lives in tools/ and drives
running ASSAY on a compute node rather than computing chemistry itself.

Three subcommands:

    submit   generate a PBS script from your saved defaults + CLI overrides,
             qsub it, and record the job id.
    status   check a submitted job's state via qstat.
    collect  gather a finished job's stdout/stderr + any result files.

Usage:
    # one-time: copy the template and fill in your project + env
    cp tools/aurora.example.yaml ~/.assay/aurora.yaml && $EDITOR ~/.assay/aurora.yaml

    # submit the fidelity suite (engine-only; see the --live warning below):
    python tools/aurora_submit.py submit \
        --suite benchmarks/fidelity/logp-partition-validation \
        --queue debug --walltime 01:00:00

    # or submit an arbitrary command:
    python tools/aurora_submit.py submit --cmd "python -m chemkit_engine.cli sp --method xtb mol.xyz"

    python tools/aurora_submit.py status  <jobid>
    python tools/aurora_submit.py collect <jobid-or-rundir>

Requirements:
    - Conda environment: chemkit  (PyYAML must be importable)
    - Runs on an Aurora LOGIN NODE (needs the `qsub` binary). Not testable from
      macOS except for the pure `build_pbs_script()` core.

IMPORTANT (compute-node internet): Aurora compute nodes have no direct outbound
internet. A `--live` suite run needs the argo-proxy / model endpoint and will
silently skip agent scoring on a compute node unless you enable `proxy: true` in
the config (which injects the ALCF proxy exports). Engine-only runs are fine.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency is declared in pyproject
    sys.stderr.write(
        "aurora_submit needs PyYAML: pip install pyyaml (or use the chemkit conda env)\n"
    )
    raise

# tools/aurora_submit.py -> repo root is the parent of tools/.
_REPO = Path(__file__).resolve().parent.parent

# Config defaults. Every knob has a documented default EXCEPT `project`, which has
# no safe default (guessing an allocation is wrong) -- it must be supplied.
_DEFAULTS: Dict[str, Any] = {
    "project": None,                 # REQUIRED: your ALCF allocation (qsub -A)
    "queue": "debug",                # debug=1-2 nodes/1h; debug-scaling=2-256/1h
    "walltime": "01:00:00",          # HH:MM:SS
    "nodes": 1,                      # qsub -l select=<nodes>
    "filesystems": "flare",          # MUST be declared or the job won't run
    "place": "scatter",              # one chunk per node
    "env": "chemkit",                # conda env to activate on the node
    "module_use": "/soft/modulefiles",
    "module_load": "frameworks",     # ALCF Python/conda stack
    "project_root": None,            # /lus/flare/projects/<project>; default derived
    "repo_path": str(_REPO),         # abs path to the ASSAY checkout on Aurora
    "proxy": False,                  # inject ALCF proxy exports (compute-node internet)
}

# ALCF proxy (compute nodes only) -- confirmed this session: proxy.alcf.anl.gov:3128.
_PROXY_HOST = "http://proxy.alcf.anl.gov:3128"

# Marker echoed at the end of the job so `collect` can confirm completion + rc.
_DONE_MARKER = "ASSAY_JOB_DONE"


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def default_config_path() -> Path:
    """Preferred config location (~/.assay/aurora.yaml)."""
    return Path(os.path.expanduser("~/.assay/aurora.yaml"))


def load_config(path: Optional[str], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Merge documented defaults <- config file <- CLI overrides.

    Returns the EFFECTIVE config with every value populated (defaults included),
    so the caller can persist it and surface it -- no hidden defaults
    (calculation-reporting-standards #3). `project` has no default and must be
    provided by the file or an override, else this exits (#10: never guess a
    scientifically/operationally consequential value).
    """
    cfg: Dict[str, Any] = dict(_DEFAULTS)

    cfg_path = Path(path) if path else default_config_path()
    if cfg_path.is_file():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            from_file = yaml.safe_load(fh) or {}
        if not isinstance(from_file, dict):
            sys.stderr.write(f"error: config {cfg_path} is not a YAML mapping\n")
            sys.exit(2)
        for k, v in from_file.items():
            if k not in _DEFAULTS:
                sys.stderr.write(f"warning: unknown config key '{k}' in {cfg_path} (ignored)\n")
                continue
            cfg[k] = v
    elif path:
        # An explicit --config that doesn't exist is a user error, not a fallback.
        sys.stderr.write(f"error: config file not found: {cfg_path}\n")
        sys.exit(2)

    # CLI overrides win (only apply keys the user actually set -> not None).
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v

    # Derive project_root from project if not given.
    if not cfg.get("project_root") and cfg.get("project"):
        cfg["project_root"] = f"/lus/flare/projects/{cfg['project']}"

    if not cfg.get("project"):
        sys.stderr.write(
            "error: no `project` (ALCF allocation) set. Add it to your config "
            f"({cfg_path}) or pass --project. I will not guess an allocation.\n"
        )
        sys.exit(2)

    return cfg


# ---------------------------------------------------------------------------
# PBS script generation (pure -- the testable core)
# ---------------------------------------------------------------------------
def build_pbs_script(cfg: Dict[str, Any], run_cmd: str, run_dir: str, job_name: str) -> str:
    """Return the full PBS Pro batch script text. No side effects.

    Bakes in the Aurora requirements: -A/-q/-l select/walltime/filesystems/place,
    -k doe, cd into a project-space run dir (never $HOME), env activation, and an
    optional ALCF proxy block. Ends by echoing the DONE marker with the rc.
    """
    lines: List[str] = ["#!/bin/bash -l"]
    lines += [
        f"#PBS -A {cfg['project']}",
        f"#PBS -N {job_name}",
        f"#PBS -q {cfg['queue']}",
        f"#PBS -l select={int(cfg['nodes'])}",
        f"#PBS -l walltime={cfg['walltime']}",
        f"#PBS -l filesystems={cfg['filesystems']}",
        f"#PBS -l place={cfg['place']}",
        "#PBS -k doe",
        "",
        "set -euo pipefail",
        "",
        "# --- environment ------------------------------------------------------",
    ]
    if cfg.get("module_use"):
        lines.append(f"module use {cfg['module_use']}")
    if cfg.get("module_load"):
        lines.append(f"module load {cfg['module_load']}")
    lines.append(f"conda activate {cfg['env']}")
    lines.append("")

    if cfg.get("proxy"):
        lines += [
            "# --- ALCF proxy (compute nodes have no direct internet) --------------",
            f'export http_proxy="{_PROXY_HOST}"',
            f'export https_proxy="{_PROXY_HOST}"',
            f'export HTTP_PROXY="{_PROXY_HOST}"',
            f'export HTTPS_PROXY="{_PROXY_HOST}"',
            "",
        ]

    lines += [
        "# --- run --------------------------------------------------------------",
        "# Submit/run from project space, never $HOME (a job launched from $HOME",
        "# on Aurora dies abruptly).",
        f"cd {run_dir}",
        "",
        "# Capture the command's rc without `set -e` aborting before the marker is",
        "# echoed, so `collect` always sees ASSAY_JOB_DONE rc=<n> even on failure.",
        "rc=0",
        f"{run_cmd} || rc=$?",
        f'echo "{_DONE_MARKER} rc=$rc"',
        "exit $rc",
        "",
    ]
    return "\n".join(lines)


def _resolve_run_cmd(args: argparse.Namespace) -> str:
    """Turn --cmd / --suite (+ suite flags) into the literal shell command."""
    if args.cmd:
        return args.cmd
    # --suite: expand into a run_suite.py invocation. repo_path is used at runtime
    # via the script's cd + env; reference run_suite.py by its repo-relative path.
    suite_cmd = [
        "python",
        "benchmarks/run_suite.py",
        args.suite,
    ]
    if args.live:
        suite_cmd.append("--live")
    if args.model:
        suite_cmd += ["--model", *args.model]
    if args.collect:
        suite_cmd.append("--collect")
    if args.suite_args:
        # Everything after `--` is passed through verbatim.
        suite_cmd += args.suite_args
    return " ".join(suite_cmd)


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------
def cmd_submit(args: argparse.Namespace) -> int:
    overrides = {
        "project": args.project,
        "queue": args.queue,
        "walltime": args.walltime,
        "nodes": args.nodes,
        "filesystems": args.filesystems,
        "env": args.env,
        "proxy": True if args.proxy else None,
    }
    cfg = load_config(args.config, overrides)

    if not args.cmd and not args.suite:
        sys.stderr.write("error: pass exactly one of --cmd or --suite\n")
        return 2
    if args.cmd and args.suite:
        sys.stderr.write("error: pass only one of --cmd or --suite\n")
        return 2

    run_cmd = _resolve_run_cmd(args)

    # Loud warning: a --live suite run needs the model endpoint, unreachable on a
    # compute node without the proxy (the earlier openai/network failure).
    if args.suite and args.live and not cfg.get("proxy"):
        sys.stderr.write(
            "\n"
            "  !!  WARNING: --live needs the argo-proxy / model endpoint, which is\n"
            "  !!  NOT reachable from an Aurora compute node by default. Agent\n"
            "  !!  scoring will SILENTLY SKIP. Set `proxy: true` in your config or\n"
            "  !!  pass --proxy to inject the ALCF proxy, or run engine-only.\n\n"
        )

    # Run dir under project space. The repo checkout is expected to live there.
    stamp = _stamp()
    job_name = args.name or (f"assay_{Path(args.suite).name}" if args.suite else "assay_cmd")
    base = args.run_dir or cfg.get("repo_path") or cfg["project_root"]
    run_dir = str(Path(base).resolve()) if args.run_dir else base
    script_path = Path(cfg["repo_path"]) / f"{job_name}_{stamp}.pbs"
    cfg_path = Path(cfg["repo_path"]) / f"input_configs_{stamp}.yaml"

    script_text = build_pbs_script(cfg, run_cmd, run_dir, job_name)

    # Write the script + effective config FIRST, so both are inspectable even if
    # qsub is missing or rejects the job.
    script_path.write_text(script_text, encoding="utf-8")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            {"effective_config": cfg, "run_cmd": run_cmd, "run_dir": run_dir,
             "job_name": job_name, "generated": stamp},
            fh, sort_keys=False,
        )

    print(f"[aurora] wrote PBS script : {script_path}")
    print(f"[aurora] wrote config     : {cfg_path}")
    print(f"[aurora] run command      : {run_cmd}")

    # qsub it.
    try:
        proc = subprocess.run(
            ["qsub", str(script_path)],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        sys.stderr.write(
            "error: `qsub` not found. This tool submits jobs on an Aurora login "
            "node. The script + config were still written above for inspection.\n"
        )
        return 1

    if proc.returncode != 0:
        sys.stderr.write(f"error: qsub rejected the job:\n{proc.stderr.strip()}\n")
        return 1

    job_id = proc.stdout.strip()
    record = {
        "job_id": job_id,
        "job_name": job_name,
        "script_path": str(script_path),
        "input_configs": str(cfg_path),
        "run_cmd": run_cmd,
        "run_dir": run_dir,
        "queue": cfg["queue"],
        "nodes": cfg["nodes"],
        "walltime": cfg["walltime"],
        "submitted": stamp,
    }
    rec_path = Path(cfg["repo_path"]) / f"submission_{stamp}.json"
    rec_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(f"[aurora] submitted job    : {job_id}")
    print(f"[aurora] submission record: {rec_path}")
    print(f"\nNext: python tools/aurora_submit.py status {job_id}")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def cmd_status(args: argparse.Namespace) -> int:
    try:
        proc = subprocess.run(
            ["qstat", "-x", "-f", args.job_id],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        sys.stderr.write("error: `qstat` not found -- run this on an Aurora login node.\n")
        return 1

    if proc.returncode != 0:
        sys.stderr.write(
            f"qstat could not find {args.job_id} (it may have finished and aged out).\n"
            f"Try: python tools/aurora_submit.py collect {args.job_id}\n"
        )
        return 1

    out = proc.stdout
    # Pull the job_state line for a concise summary; print full -f too.
    state = "?"
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("job_state"):
            state = s.split("=", 1)[1].strip()
            break
    human = {"Q": "queued", "R": "running", "F": "finished",
             "H": "held", "E": "exiting", "B": "begun"}.get(state, state)
    print(f"[aurora] {args.job_id}: {state} ({human})")
    print(out.strip())
    return 0


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------
def _find_submission_record(target: str) -> Optional[Dict[str, Any]]:
    """If `target` is a job id, find its submission_<stamp>.json in the repo."""
    for rec in sorted(_REPO.glob("submission_*.json"), reverse=True):
        try:
            data = json.loads(rec.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("job_id") == target or target in data.get("job_id", ""):
            return data
    return None


def cmd_collect(args: argparse.Namespace) -> int:
    target = args.target
    record = None
    run_dir = None

    if os.path.isdir(target):
        run_dir = target
    else:
        record = _find_submission_record(target)
        if record:
            run_dir = record.get("run_dir")
            print(f"[aurora] matched submission record for {record.get('job_id')}")
        else:
            sys.stderr.write(
                f"error: '{target}' is neither a directory nor a known job id "
                f"(no submission_*.json in {_REPO}).\n"
            )
            return 1

    # PBS writes <jobname>.o<id> / .e<id>. Look in the repo (where qsub ran) and
    # the run dir.
    search_dirs = [_REPO]
    if run_dir and os.path.isdir(run_dir):
        search_dirs.append(Path(run_dir))

    job_name = record.get("job_name") if record else None
    found_out: List[Path] = []
    for d in search_dirs:
        pats = [f"{job_name}.o*", f"{job_name}.e*"] if job_name else ["*.o*", "*.e*"]
        for pat in pats:
            found_out += sorted(d.glob(pat))

    if not found_out:
        print(f"[aurora] no PBS .o/.e output files found yet in {', '.join(map(str, search_dirs))}")
        print("[aurora] the job may still be queued/running -- check `status`.")
        return 0

    print(f"[aurora] output files:")
    done = False
    for f in found_out:
        print(f"   {f}")
        if f.suffix.startswith(".o"):
            txt = f.read_text(encoding="utf-8", errors="replace")
            for line in txt.splitlines():
                if _DONE_MARKER in line:
                    done = True
                    print(f"   -> {line.strip()}")

    # Point at suite result artifacts if present.
    if run_dir and os.path.isdir(run_dir):
        summaries = sorted(Path(run_dir).rglob("summary.csv"))
        if summaries:
            print("[aurora] suite summaries:")
            for s in summaries:
                print(f"   {s}")

    print(f"[aurora] completed: {'yes' if done else 'not detected (still running or crashed before marker)'}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Submit/monitor/collect ASSAY jobs on Aurora (PBS Pro).",
    )
    sub = ap.add_subparsers(dest="action", required=True)

    # submit
    sp = sub.add_parser("submit", help="generate a PBS script and qsub it")
    sp.add_argument("--config", default=None, help="path to aurora.yaml (default ~/.assay/aurora.yaml)")
    src = sp.add_argument_group("what to run (choose one)")
    src.add_argument("--cmd", default=None, help="literal shell command to run on the node")
    src.add_argument("--suite", default=None, help="suite folder -> runs benchmarks/run_suite.py <folder>")
    sp.add_argument("--live", action="store_true", help="(suite) pass --live (see compute-node internet warning)")
    sp.add_argument("--model", nargs="*", default=None, help="(suite) one or more --model values")
    sp.add_argument("--collect", action="store_true", help="(suite) pass --collect")
    sp.add_argument("suite_args", nargs="*", help="extra args passed verbatim to run_suite.py (after --)")
    # PBS overrides (win over the config file)
    sp.add_argument("--project", default=None, help="ALCF allocation (qsub -A) -- required if not in config")
    sp.add_argument("--queue", default=None, help="PBS queue (default debug)")
    sp.add_argument("--walltime", default=None, help="HH:MM:SS (default 01:00:00)")
    sp.add_argument("--nodes", type=int, default=None, help="node count (default 1)")
    sp.add_argument("--filesystems", default=None, help="declared filesystems (default flare)")
    sp.add_argument("--env", default=None, help="conda env to activate (default chemkit)")
    sp.add_argument("--proxy", action="store_true", help="inject ALCF proxy exports (compute-node internet)")
    sp.add_argument("--name", default=None, help="PBS job name")
    sp.add_argument("--run-dir", default=None, help="dir to cd into on the node (default repo_path)")
    sp.set_defaults(func=cmd_submit)

    # status
    st = sub.add_parser("status", help="check a job via qstat")
    st.add_argument("job_id", help="PBS job id")
    st.set_defaults(func=cmd_status)

    # collect
    co = sub.add_parser("collect", help="gather a finished job's output")
    co.add_argument("target", help="job id OR run directory")
    co.set_defaults(func=cmd_collect)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
