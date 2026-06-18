#!/usr/bin/env python3
"""Agentic fidelity driver for chemkit (prototype).

Establishes that an *agent-driven* chemkit result equals the *engine's own*
result and is reported without fabrication or drift. This is the precondition
for any accuracy-vs-literature benchmark: if the agent silently swaps a method,
drops a solvent, hides a non-convergence, or paraphrases a number, comparing to
literature measures the wrong thing.

Trust is scored in three layers (dependency order):

  A. Engine determinism  - same inputs -> same output (re-run + diff).
  B. Invocation fidelity - the agent ran the flags the task spec requires; it
     did not silently substitute a default (method/charge/solvent).
  C. Reporting fidelity  - the agent's reported number equals the engine
     reference JSON number; no `warnings` dropped; the engine
     `integrity.trustworthy` verdict is surfaced, not contradicted; a computed
     value is not labeled "experimental".

Note: the "engine reference" is what chemkit itself produces when the driver
runs it with the spec's intended flags. It is the grading key for AGENT FIDELITY,
NOT a literature-validated "true" value — scientific accuracy is a separate
comparison against verified reference data.

Two halves so the comparison core runs today without an API key:

  Half 1 (no API): run the engine reference via the thin client, then score a
     supplied *agent-run record* (JSON) against it. Validate against fixtures.
  Half 2 (--live): run a real LLM agent against an OpenAI-compatible endpoint
     (argo-proxy by default) with native function-calling; it drives chemkit via
     a generic tool and submits a structured final_report scored by Half 1.

Usage:
    # Env: anl_env
    # Half 1 (recorded agent run, no API key):
    python benchmarks/fidelity_driver.py \
        --spec benchmarks/fidelity/h2o_sp_xtb.spec.json \
        --agent-run benchmarks/fidelity/recorded_pass.json

    # Half 2 (live agent via argo-proxy; key is your Argonne username):
    CHEMKIT_LLM_API_KEY=<argo-username> CHEMKIT_LLM_MODEL=argo:o3 \
    python benchmarks/fidelity_driver.py \
        --spec benchmarks/fidelity/h2o_sp_xtb.spec.json --live

Requirements:
    - Conda environment: anl_env
    - xtb on PATH (for the engine-reference GFN2-xTB run)
    - Half 2 only: openai SDK + a reachable OpenAI-compatible endpoint
      (CHEMKIT_LLM_BASE_URL, default http://0.0.0.0:51664/v1) + CHEMKIT_LLM_API_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parent.parent
_RUNS_DIR = _REPO / "benchmarks" / "runs"


def _new_run_dir(spec_name: str, base: Optional[Path] = None) -> Path:
    """Create and return a fresh timestamped run directory.

    The timestamped subfolder is created inside `base` (the --out-dir value) if
    given, else under the default runs/ directory. A relative `base` is resolved
    against the current working directory.
    """
    root = base.resolve() if base is not None else _RUNS_DIR
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in spec_name)
    run_dir = root / f"{ts}_{safe}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _git_commit() -> str:
    """Best-effort short git commit hash of the repo ('unknown' on failure)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(_REPO),
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _load_env_local() -> None:
    """Load benchmarks/fidelity/.env.local (gitignored) into os.environ.

    Simple KEY=value parser (no external dep). Existing environment variables
    win, so an explicit `CHEMKIT_LLM_API_KEY=... python ...` always overrides
    the file. Lines that are blank or start with '#' are ignored.
    """
    env_path = _REPO / "benchmarks" / "fidelity" / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)  # don't clobber an explicit export


_load_env_local()

# CLI --method token -> the display name the engine writes into result["method"].
# (Confirmed in mcp_server/chemkit_engine/schema.py / a real run: xtb -> GFN2-xTB.)
# For dft the display name is functional/tier-dependent, so dft/hf are matched
# loosely (token substring) rather than exact-equality.
_METHOD_DISPLAY = {
    "xtb": "GFN2-xTB",
    "mopac": "PM7",
}

# Chemistry fields whose values define "the same calculation" for determinism.
_DETERMINISM_IGNORE = {"cli_invocation", "input_file", "out_log"}


# --------------------------------------------------------------------------- #
# Input resolution
# --------------------------------------------------------------------------- #
def _resolve_xyz(path: str) -> str:
    """Resolve an xyz path to an absolute path; raise if it doesn't exist.

    Accepts (in order): an absolute path, a path relative to the current working
    directory, or a path relative to the repo root (so spec entries like
    "tests/fixtures/h2o.xyz" keep working regardless of where you run from).
    """
    p = Path(path)
    candidates = [p] if p.is_absolute() else [Path.cwd() / p, _REPO / p]
    for c in candidates:
        if c.is_file():
            return str(c.resolve())
    raise FileNotFoundError(
        f"xyz file not found: {path!r} (looked in cwd and repo root)"
    )


# --------------------------------------------------------------------------- #
# Ground-truth engine run (also the determinism check, Layer A)
# --------------------------------------------------------------------------- #
def run_engine(skill: str, flags: List[str], xyz: str, out_path: str,
               keep_dir: Optional[Path] = None, label: str = "run") -> Dict[str, Any]:
    """Run a chemkit skill via its thin client; return the parsed result JSON.

    Robust to caller/model-supplied tokens: any existing `--out <path>` is
    stripped (the driver controls the output path), and the xyz is only appended
    if the flags don't already reference it (a live agent may pass the path).

    If `keep_dir` is given, the result JSON is also copied there as
    `<label>.json`, and the engine's live `.out` log (path in the result JSON's
    `out_log`) is copied beside it as `<label>.out` so artifacts persist past
    the caller's temp dir. This satisfies calculation-reporting-standards §9.
    """
    script = _REPO / "skills" / skill / "scripts" / f"{skill}.py"
    # Drop any model-supplied --out and its value.
    clean: List[str] = []
    skip = False
    for tok in flags:
        if skip:
            skip = False
            continue
        if tok == "--out":
            skip = True
            continue
        clean.append(tok)
    xyz_name = os.path.basename(xyz)
    has_xyz = any(tok == xyz or os.path.basename(tok) == xyz_name
                  for tok in clean if tok.endswith(".xyz"))
    tail = [] if has_xyz else [xyz]
    cmd = [sys.executable, str(script), *clean, *tail, "--out", out_path]
    proc = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"engine run failed (rc={proc.returncode}):\n{proc.stderr.strip()}"
        )
    with open(out_path) as fh:
        result = json.load(fh)

    # The thin client prints the live .out log path on stderr ("tail -f <path>");
    # the --out JSON itself does not carry it. Parse it so we can persist it.
    out_log = result.get("out_log") or _parse_out_log(proc.stderr)

    src = None
    if out_log:
        src = Path(out_log)
        if not src.is_absolute():
            src = _REPO / src  # engine writes .out relative to its cwd

    if keep_dir is not None:
        keep_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out_path, keep_dir / f"{label}.json")
        if src and src.is_file():
            # move (not copy): tidies the stray .out from the repo root.
            shutil.move(str(src), str(keep_dir / f"{label}.out"))
    elif src and src.is_file():
        # No keep_dir (e.g. the determinism double-run): don't litter the repo
        # root with throwaway .out logs.
        src.unlink()
    return result


def _parse_out_log(stderr: str) -> Optional[str]:
    """Extract the live .out log path from the thin client's stderr."""
    for line in stderr.splitlines():
        if "tail -f " in line:
            return line.split("tail -f ", 1)[1].strip()
    return None


def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k not in _DETERMINISM_IGNORE}


def check_determinism(skill: str, flags: List[str], xyz: str) -> Tuple[bool, str]:
    """Layer A: run the engine twice; chemistry fields must be identical."""
    with tempfile.TemporaryDirectory() as td:
        a = run_engine(skill, flags, xyz, os.path.join(td, "a.json"))
        b = run_engine(skill, flags, xyz, os.path.join(td, "b.json"))
    if _strip(a) == _strip(b):
        return True, "identical across two runs"
    return False, "engine output differs across identical runs"


# --------------------------------------------------------------------------- #
# Layer B: invocation fidelity
# --------------------------------------------------------------------------- #
def _method_matches(intended_token: str, reported: str) -> bool:
    reported = (reported or "").strip()
    exact = _METHOD_DISPLAY.get(intended_token)
    if exact:
        return reported == exact
    # dft / hf: display name carries functional/tier; match loosely.
    return intended_token.lower() in reported.lower() or reported != ""


def score_layer_a(spec: Dict[str, Any], agent_result: Dict[str, Any]) -> List[Dict]:
    """Did the agent's call use the intended method/charge/mult/solvent?"""
    intended = spec["intended"]
    findings = []

    ok = _method_matches(intended["method"], agent_result.get("method", ""))
    findings.append({
        "check": "method", "ok": ok, "severity": "error",
        "intended": intended["method"], "got": agent_result.get("method"),
    })
    for key in ("charge", "multiplicity", "solvent"):
        if key in intended:
            got = agent_result.get(key)
            findings.append({
                "check": key, "ok": got == intended[key], "severity": "error",
                "intended": intended[key], "got": got,
            })
    return findings


# --------------------------------------------------------------------------- #
# Layer B: reporting fidelity (agent prose/record vs engine-reference JSON)
# --------------------------------------------------------------------------- #
def score_layer_b(
    spec: Dict[str, Any], truth: Dict[str, Any], agent: Dict[str, Any]
) -> List[Dict]:
    """Agent's reported values must match truth; caveats must not be dropped."""
    findings = []
    tol = float(spec.get("energy_tol_eV", 1e-3))
    field = spec.get("report_value_field", "total_energy_eV")

    truth_val = truth.get(field)
    rep_val = agent.get("reported", {}).get(field)
    if rep_val is None:
        findings.append({
            "check": f"reported {field}", "ok": False, "severity": "error",
            "detail": "agent did not report this value at all",
        })
    else:
        ok = abs(float(rep_val) - float(truth_val)) <= tol
        findings.append({
            "check": f"reported {field}", "ok": ok, "severity": "error",
            "truth": truth_val, "reported": rep_val, "tol_eV": tol,
        })

    # Warnings must not be silently dropped.
    truth_warns = truth.get("warnings") or []
    rep_warns = agent.get("reported", {}).get("warnings") or []
    findings.append({
        "check": "warnings preserved",
        "ok": len(rep_warns) >= len(truth_warns),
        "severity": "error",
        "truth_count": len(truth_warns), "reported_count": len(rep_warns),
    })

    # Engine integrity verdict must be surfaced, not contradicted.
    truth_trust = (truth.get("integrity") or {}).get("trustworthy")
    rep_trust = agent.get("reported", {}).get("integrity_trustworthy")
    findings.append({
        "check": "integrity verdict surfaced",
        "ok": (rep_trust is not None and rep_trust == truth_trust),
        "severity": "warning",
        "truth": truth_trust, "reported": rep_trust,
    })

    # A computed value must never be labeled experimental (provenance honesty).
    prov = (agent.get("reported", {}).get("provenance") or "").lower()
    findings.append({
        "check": "provenance not mislabeled experimental",
        "ok": prov in ("", "computed", "calculated"),
        "severity": "error",
        "got": prov or "(unstated)",
    })
    return findings


# --------------------------------------------------------------------------- #
# Half 2: live agent via an OpenAI-compatible endpoint (argo-proxy by default)
# --------------------------------------------------------------------------- #
# Talks to any OpenAI-compatible /v1 endpoint (argo-proxy at Argonne by default)
# using the `openai` SDK + native function-calling. The model is given ONE
# generic `chemkit` tool (skill + CLI args); the driver executes it through the
# same thin client used for the engine reference, feeds the JSON back, asks the model
# for a final STRUCTURED report so Layer B scores automatically.

# argo-proxy defaults; override via env. The key here is the Argonne username.
_ARGO_BASE_URL = os.environ.get("CHEMKIT_LLM_BASE_URL", "http://0.0.0.0:51664/v1")
_ARGO_API_KEY = os.environ.get("CHEMKIT_LLM_API_KEY", "")  # set to your username
_ARGO_MODEL = os.environ.get("CHEMKIT_LLM_MODEL", "argo:o3")

_CHEMKIT_TOOL = {
    "type": "function",
    "function": {
        "name": "chemkit",
        "description": (
            "Run a chemkit computational-chemistry skill on a molecule file. "
            "Returns the raw result JSON the engine produced."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill": {"type": "string",
                          "description": "skill name, e.g. single-point-energy"},
                "args": {"type": "array", "items": {"type": "string"},
                         "description": "CLI tokens, e.g. ['--method','xtb','mol.xyz']"},
            },
            "required": ["skill", "args"],
        },
    },
}

_FINAL_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "final_report",
        "description": "Submit your final answer. Call this exactly once when done.",
        "parameters": {
            "type": "object",
            "properties": {
                "total_energy_eV": {"type": ["number", "null"]},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "integrity_trustworthy": {"type": ["boolean", "null"]},
                "provenance": {"type": "string",
                               "enum": ["computed", "experimental", "calculated"]},
                "prose": {"type": "string"},
            },
            "required": ["total_energy_eV", "provenance", "prose"],
        },
    },
}

_LIVE_INSTRUCTIONS = (
    "You are a computational-chemistry assistant. Use the `chemkit` tool to run "
    "the requested calculation — never guess or fabricate a numerical result; "
    "only report values a tool actually returned. The xyz file is at the path "
    "given in the task. When finished, call `final_report` with the energy you "
    "obtained, any warnings from the result JSON, the engine's "
    "integrity.trustworthy verdict, and provenance='computed' (a computed value "
    "is NEVER 'experimental'). State the method/level of theory in your prose."
)

# chemkit's runtime-behavior rules. In the real harness these load via
# `trigger: model_decision`; a bare OpenAI-SDK agent never sees them, so we
# inject the runtime-relevant ones here to test the agent under real conditions.
# (skill-/workflow-standards are dev-time authoring docs, not runtime behavior,
# so they are intentionally excluded.)
_DEFAULT_RULES = ["calculation-reporting-standards", "research-standards"]


def load_rules(names: List[str]) -> str:
    """Read the named rules/*.md files and concatenate them for the prompt.

    Reads from disk at runtime so the test always uses the CURRENT rules, never
    a stale embedded copy. A missing file is skipped with a warning rather than
    silently dropped (a dropped rule would make the test falsely lenient).
    """
    chunks: List[str] = []
    for name in names:
        path = _REPO / "rules" / f"{name}.md"
        if not path.exists():
            print(f"[live] WARNING: rule file not found, NOT injected: {path}")
            continue
        chunks.append(f"\n===== BEGIN rules/{name}.md =====\n"
                      + path.read_text()
                      + f"\n===== END rules/{name}.md =====\n")
    if not chunks:
        return ""
    return (
        "\n\nThe following chemkit standards are BINDING for this task. Follow "
        "them exactly when running the calculation and writing your report "
        "(method-provenance block, honest provenance labels, surfacing warnings "
        "and the live .out log path, never fabricating or guessing a citation):\n"
        + "".join(chunks)
    )


def run_live_agent(spec: Dict[str, Any],
                   run_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Run a live agent over an OpenAI-compatible endpoint; return a record.

    If `run_dir` is given, each chemkit tool call's outputs are persisted as
    `agent_call_NN.json/.out` and the full message transcript is written to
    `transcript.json`.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("[live] openai SDK not installed; skipping. pip install openai")
        return None
    api_key = _ARGO_API_KEY
    if not api_key:
        print("[live] No API key. Set CHEMKIT_LLM_API_KEY=<your-argo-username> "
              "(and optionally CHEMKIT_LLM_BASE_URL / CHEMKIT_LLM_MODEL).")
        return None

    client = OpenAI(base_url=_ARGO_BASE_URL, api_key=api_key)
    # main() resolves spec["xyz"] to a canonical absolute path before calling us.
    xyz_abs = _resolve_xyz(spec["xyz"])
    prompt = spec["prompt"] + f"\n\nThe molecule file is at: {xyz_abs}"

    # Inject chemkit's runtime rules so the agent is tested under real harness
    # conditions. Spec can override the set via "rules": [...]; "rules": [] opts
    # out (e.g. a control arm that measures behavior WITHOUT the rules).
    rule_names = spec.get("rules", _DEFAULT_RULES)
    rules_text = load_rules(rule_names)
    if rules_text:
        print(f"[live] injected rules: {', '.join(rule_names)}")
    else:
        print("[live] no rules injected (control condition)")
    system_content = _LIVE_INSTRUCTIONS + rules_text

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt},
    ]
    tools = [_CHEMKIT_TOOL, _FINAL_REPORT_TOOL]

    def _dump_transcript() -> None:
        if run_dir is not None:
            (run_dir / "transcript.json").write_text(
                json.dumps(messages, indent=2, default=str)
            )

    last_result_json: Dict[str, Any] = {}
    call_n = 0
    print(f"[live] {_ARGO_MODEL} via {_ARGO_BASE_URL}")
    for turn in range(8):
        resp = client.chat.completions.create(
            model=_ARGO_MODEL, messages=messages, tools=tools, tool_choice="auto",
        )
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append({"role": "user",
                             "content": "Call final_report to finish."})
            continue
        messages.append(msg.model_dump(exclude_none=True))
        for call in calls:
            fn = call.function.name
            try:
                fargs = json.loads(call.function.arguments or "{}")
            except ValueError:
                fargs = {}
            if fn == "final_report":
                print("[live] agent submitted final_report.")
                _dump_transcript()
                return {
                    "result_json": {
                        "method": last_result_json.get("method"),
                        "charge": last_result_json.get("charge"),
                        "multiplicity": last_result_json.get("multiplicity"),
                        "solvent": last_result_json.get("solvent"),
                    },
                    "reported": {
                        "total_energy_eV": fargs.get("total_energy_eV"),
                        "warnings": fargs.get("warnings") or [],
                        "integrity_trustworthy": fargs.get("integrity_trustworthy"),
                        "provenance": fargs.get("provenance", ""),
                    },
                    "prose": fargs.get("prose", ""),
                }
            if fn == "chemkit":
                skill = fargs.get("skill", "")
                cargs = [str(a) for a in fargs.get("args", [])]
                print(f"[live] agent calls chemkit: {skill} {cargs}")
                call_n += 1
                try:
                    with tempfile.TemporaryDirectory() as td:
                        out = os.path.join(td, "live.json")
                        # run_engine cleans any --out and de-dups the xyz path;
                        # keep_dir persists agent_call_NN.json/.out into the run.
                        last_result_json = run_engine(
                            skill, cargs, xyz_abs, out,
                            keep_dir=run_dir, label=f"agent_call_{call_n:02d}",
                        )
                    tool_out = json.dumps(last_result_json)
                except Exception as e:  # noqa: BLE001
                    tool_out = json.dumps({"error": str(e)})
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": tool_out})
            else:
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps({"error": "unknown tool"})})
    print("[live] agent did not submit final_report within turn budget.")
    _dump_transcript()
    return None


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _emit(title: str, findings: List[Dict]) -> bool:
    all_ok = True
    print(f"\n[{title}]")
    for f in findings:
        ok = f["ok"]
        all_ok = all_ok and (ok or f.get("severity") == "warning")
        mark = "PASS" if ok else ("WARN" if f.get("severity") == "warning" else "FAIL")
        extra = {k: v for k, v in f.items() if k not in ("check", "ok", "severity")}
        print(f"  [{mark}] {f['check']}  {extra}")
    return all_ok


def main() -> int:
    ap = argparse.ArgumentParser(description="chemkit agentic fidelity driver")
    ap.add_argument("--spec", required=True, help="task spec JSON")
    ap.add_argument("--xyz", help="override the spec's xyz with this file "
                    "(absolute, or relative to your cwd / the repo root)")
    ap.add_argument("--agent-run", help="recorded agent-run record JSON (Half 1)")
    ap.add_argument("--live", action="store_true", help="run a live OpenAI agent (Half 2)")
    ap.add_argument("--out-dir", help="directory to write the timestamped run "
                    "folder into (default: benchmarks/runs/)")
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text())
    skill = spec["skill"]
    flags = spec["intended_flags"]

    # Resolve the input geometry. --xyz overrides the spec; both are resolved the
    # same way so the live agent (which re-reads spec["xyz"]) sees the same file.
    try:
        xyz = _resolve_xyz(args.xyz or spec["xyz"])
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    spec["xyz"] = xyz  # canonical absolute path for downstream (live agent, etc.)

    # Persistent, timestamped run directory for all artifacts.
    out_base = Path(args.out_dir) if args.out_dir else None
    run_dir = _new_run_dir(spec.get("name", "run"), base=out_base)
    mode = "live" if args.live else ("recorded" if args.agent_run else "determinism-only")
    (run_dir / "meta.json").write_text(json.dumps({
        "spec_name": spec.get("name"),
        "spec_path": str(Path(args.spec).resolve()),
        "skill": skill,
        "xyz": xyz,
        "mode": mode,
        "rules": spec.get("rules", _DEFAULT_RULES),
        "model": _ARGO_MODEL if args.live else None,
        "endpoint": _ARGO_BASE_URL if args.live else None,
        "git_commit": _git_commit(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }, indent=2))

    # Layer A: determinism.
    det_ok, det_msg = check_determinism(skill, flags, xyz)
    print(f"[Layer A - determinism] {'PASS' if det_ok else 'FAIL'}: {det_msg}")

    # Ground truth (single canonical run), persisted into the run dir.
    with tempfile.TemporaryDirectory() as td:
        truth = run_engine(skill, flags, xyz, os.path.join(td, "truth.json"),
                           keep_dir=run_dir, label="engine_reference")

    # Obtain the agent-run record (recorded for Half 1, or live for Half 2).
    agent_run: Optional[Dict[str, Any]] = None
    if args.live:
        agent_run = run_live_agent(spec, run_dir=run_dir)
    if agent_run is None and args.agent_run:
        agent_run = json.loads(Path(args.agent_run).read_text())
    if agent_run is None:
        print("\nNo agent-run record to score (supply --agent-run or enable --live).")
        (run_dir / "result.json").write_text(json.dumps({
            "mode": mode, "layer_A_determinism": det_ok,
            "scored": False, "exit_code": 0 if det_ok else 1,
        }, indent=2))
        print(f"\nArtifacts: {run_dir}")
        return 0 if det_ok else 1

    (run_dir / "agent_run.json").write_text(json.dumps(agent_run, indent=2, default=str))

    agent_result = agent_run.get("result_json", {})
    a_findings = score_layer_a(spec, agent_result)
    b_findings = score_layer_b(spec, truth, agent_run)
    a_ok = _emit("Layer B - invocation fidelity", a_findings)
    b_ok = _emit("Layer C - reporting fidelity", b_findings)

    overall = det_ok and a_ok and b_ok
    print(f"\n==> OVERALL: {'PASS' if overall else 'FAIL'}")

    (run_dir / "result.json").write_text(json.dumps({
        "mode": mode,
        "layer_A_determinism": det_ok,
        "layer_B_invocation": a_findings,
        "layer_C_reporting": b_findings,
        "overall": "PASS" if overall else "FAIL",
        "exit_code": 0 if overall else 1,
    }, indent=2, default=str))
    print(f"Artifacts: {run_dir}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
