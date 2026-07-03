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
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _REPO / "benchmarks" / "fidelity" / "single-point-validation"


# ── timing helpers ────────────────────────────────────────────────────────────
# Per-run wall-clock timing is recorded by fidelity_driver in each run's
# result.json (and agent_run.json) under a "timing" block:
#   {total_s, llm_s, engine_s, turns, tool_calls}
# These helpers pull those values and compute mean / sample-std / standard-error
# so the summary can report how long each MODEL takes per task.
_TIMING_KEYS = ("total_s", "llm_s", "engine_s")


def _run_timing_full(run: Path) -> Dict[str, Any]:
    """The full timing block for one run (total_s/llm_s/engine_s/turns/tool_calls),
    from result.json then agent_run.json. {} if neither has one."""
    for name in ("result.json", "agent_run.json"):
        f = run / name
        if not f.is_file():
            continue
        try:
            t = (json.loads(f.read_text()) or {}).get("timing")
        except (OSError, ValueError):
            continue
        if isinstance(t, dict):
            return t
    return {}


def _run_timing(run: Path) -> Dict[str, float]:
    """Just the float timing metrics (total_s/llm_s/engine_s) for one run."""
    t = _run_timing_full(run)
    return {k: float(t[k]) for k in _TIMING_KEYS if isinstance(t.get(k), (int, float))}


def _stats(vals: Sequence[float]) -> Dict[str, Any]:
    """mean / sample-std (ddof=1) / standard-error / n for a list of numbers.
    std and se are None for n<2 (undefined); all None for n==0."""
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "se": None}
    mean = sum(vals) / n
    if n < 2:
        return {"n": n, "mean": round(mean, 2), "std": None, "se": None}
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)   # sample variance (ddof=1)
    std = math.sqrt(var)
    se = std / math.sqrt(n)                                # standard error of the mean
    return {"n": n, "mean": round(mean, 2), "std": round(std, 2), "se": round(se, 2)}

# Fixed-name engine-reference folder (sibling of agent-run folders under a
# molecule). The engine reference moved here from inside each run folder; keep
# the old in-run location as a fallback so pre-refactor runs still collect.
ENGINE_REF_DIRNAME = "engine-reference"


def _engine_reference_json(run: Path) -> Optional[Path]:
    """Locate engine_reference.json for an agent-run folder.

    The engine-reference/ lives directly under the CASE folder. A run may be:
      * nested (agent runs): <case>/<model>/<ts>/  -> case is run.parent.parent
      * flat (recorded/determinism): <case>/<ts>/ -> case is run.parent
    so we look for engine-reference/ under BOTH candidate case dirs. Oldest
    layout kept the reference inside the run folder itself — checked last.
    """
    for case_dir in (run.parent, run.parent.parent):
        cand = case_dir / ENGINE_REF_DIRNAME / "engine_reference.json"
        if cand.is_file():
            return cand
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


def _is_run_dir(d: Path) -> bool:
    """A run folder is any dir that has a meta.json (written at run start, before
    scoring). This is what distinguishes a real run — completed OR crashed — from
    a per-model grouping dir or the engine-reference/ folder."""
    return d.is_dir() and (d / "meta.json").is_file()


def _run_crashed(run: Path) -> bool:
    """True if a run started (has meta.json) but never produced a result.json —
    i.e. it crashed/errored before scoring. Such runs MUST be surfaced, not
    silently skipped: 'no result' is a real, reportable outcome, not absence."""
    return not (run / "result.json").is_file()


def _all_runs(case_dir: Path) -> List[Path]:
    """Every run folder of a case — COMPLETED and CRASHED alike — identified by
    meta.json. Includes both flat runs (``<case>/<ts>/``, model=None) and nested
    per-model runs (``<case>/<model>/<ts>/``). engine-reference/ is excluded."""
    runs: List[Path] = []
    for child in case_dir.iterdir():
        if not child.is_dir() or child.name == ENGINE_REF_DIRNAME:
            continue
        if _is_run_dir(child):
            runs.append(child)                     # flat run (completed or crashed)
        else:
            # a per-model subfolder: its children are the runs
            for grandchild in child.iterdir():
                if _is_run_dir(grandchild):
                    runs.append(grandchild)
    return runs


def _scored_runs(case_dir: Path) -> List[Path]:
    """Run folders that completed scoring (have a result.json). This is
    _all_runs minus the crashed ones. Used by the single-run collectors; the
    repeat collector uses _all_runs so crashes are counted, not dropped."""
    return [r for r in _all_runs(case_dir) if not _run_crashed(r)]


def _latest_run(case_dir: Path) -> Optional[Path]:
    """Return the newest timestamped agent-run subdir that has a result.json."""
    runs = _scored_runs(case_dir)
    if not runs:
        return None
    return sorted(runs, key=lambda d: d.name)[-1]


def _run_model(run: Path) -> str:
    """The model a run folder was produced with, from its meta.json 'model'
    field (authoritative — the driver writes it there). Falls back to '(default)'
    for runs with no model (the driver default / recorded mode, model=None)."""
    mj = run / "meta.json"
    if mj.is_file():
        try:
            m = (json.loads(mj.read_text()) or {}).get("model")
            if m:
                return str(m)
        except Exception:
            pass
    return "(default)"


def _latest_run_per_model(case_dir: Path) -> Dict[str, Path]:
    """Map each model -> its NEWEST scored run folder for this case.

    A multi-model sweep nests runs under a per-model subfolder
    (<case>/<fs_safe(model)>/<ts>/); the old _latest_run() kept only the single
    newest across ALL models, silently
    dropping every other model. This keeps the latest run for EACH model so all
    models appear in the summary. Runs are grouped by meta.json['model']."""
    latest: Dict[str, Path] = {}
    for run in sorted(_scored_runs(case_dir), key=lambda d: d.name):
        # sorted ascending by timestamped name -> later iterations overwrite,
        # so each model ends up mapped to its newest run.
        latest[_run_model(run)] = run
    return latest


def _runs_per_model(case_dir: Path, n: Optional[int] = None) -> Dict[str, List[Path]]:
    """Map each model -> ALL its run folders for this case, newest first,
    INCLUDING crashed runs (meta.json but no result.json). Crashes are counted as
    errored outcomes by the repeat aggregator, never silently dropped.

    If `n` is given, keep only the n NEWEST runs per model (the fresh batch a
    --repeat N invocation just produced). Runs are grouped by meta.json['model']
    and sorted by their timestamped folder name (descending = newest first)."""
    by_model: Dict[str, List[Path]] = {}
    for run in sorted(_all_runs(case_dir), key=lambda d: d.name, reverse=True):
        by_model.setdefault(_run_model(run), []).append(run)
    if n is not None:
        by_model = {m: runs[:n] for m, runs in by_model.items()}
    return by_model


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


def _row_for_run(case_dir: Path, run: Path) -> Dict[str, Any]:
    """Build one summary row from a single scored run folder."""
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

    return {
        "case": case_dir.name,
        "expect": result.get("expect", "compute"),
        "mode": result.get("mode", meta.get("mode", "")),
        "model": meta.get("model") or "(default)",
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
        # Per-run wall-clock timing (seconds); None where not recorded.
        **{k: _run_timing(run).get(k) for k in _TIMING_KEYS},
    }


def collect(base: Path) -> List[Dict[str, Any]]:
    """Legacy single-row-per-case collect (newest run across all models).

    Retained for callers that want one representative row per case. For a
    model-aware, complete view (one row per (case, model)), use collect_all()."""
    rows: List[Dict[str, Any]] = []
    for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        run = _latest_run(case_dir)
        if run is None:
            continue
        rows.append(_row_for_run(case_dir, run))
    return rows


def collect_all(base: Path) -> List[Dict[str, Any]]:
    """One row per (case, model): for every case, the newest run of EACH model.

    Fixes the silent-drop bug where a multi-model sweep only surfaced the single
    newest model per case. Rows are returned grouped by model (models sorted,
    then cases within each model) so the combined summary reads as per-model
    sections."""
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for model, run in _latest_run_per_model(case_dir).items():
            by_model.setdefault(model, []).append(_row_for_run(case_dir, run))
    rows: List[Dict[str, Any]] = []
    for model in sorted(by_model):
        rows.extend(sorted(by_model[model], key=lambda r: r["case"]))
    return rows


def _aggregate_repeat(case_dir: Path, model: str,
                      runs: List[Path]) -> Dict[str, Any]:
    """Aggregate N runs of one (case, model) into a single pass-rate row.

    CRASHED runs (started but no result.json — the driver errored before scoring)
    are counted, NOT dropped: they add to n_runs, count as non-pass (lowering the
    rate), contribute 'ERRORED (crash, no result.json) xN' to the failed-check
    tally, and register an 'ERROR' verdict in the modal tally. This keeps the
    summary honest — an errored run is a real, reportable outcome, and a
    (case,model) whose runs ALL crashed still appears (n_pass=0, modal=ERROR)
    instead of silently vanishing.

    Reports: n_pass / n_runs / n_error / pass_rate; the MODAL overall verdict
    (PASS/FAIL/ERROR, most common across the N runs); and a failed-check TALLY,
    most-frequent first (e.g. 'warnings preserved x4; ERRORED (crash) x1'). LoT /
    headline fields come from a representative COMPLETED run where one exists."""
    from collections import Counter
    # Two kinds of errored run, both excluded from fidelity pass/fail:
    #   * CRASHED  — no result.json (driver died before scoring).
    #   * INTEGRITY-ERROR — result.json has overall=="ERROR" (data corruption in
    #     transit, e.g. malformed Unicode escapes — see fidelity_driver
    #     _has_encoding_corruption). These have a result.json but were NOT scored
    #     on fidelity; blaming the model would be wrong.
    crashed = [r for r in runs if _run_crashed(r)]
    scored = [r for r in runs if not _run_crashed(r)]
    scored_rows = [_row_for_run(case_dir, r) for r in scored]
    integrity_rows = [row for row in scored_rows if row["overall"] == "ERROR"]
    graded_rows = [row for row in scored_rows if row["overall"] != "ERROR"]

    n_runs = len(runs)
    n_error = len(crashed) + len(integrity_rows)
    n_pass = sum(1 for r in graded_rows if r["overall"] == "PASS")
    pass_rate = round(n_pass / n_runs, 3) if n_runs else 0.0

    # Modal overall verdict across ALL N runs (both error kinds register 'ERROR').
    verdicts = Counter([r["overall"] for r in graded_rows] + ["ERROR"] * n_error)
    modal_verdict = verdicts.most_common(1)[0][0] if verdicts else "?"

    # Failed-check tally: how many of the N runs each check failed in, PLUS
    # dedicated entries for the two error kinds so they are visible/attributable.
    check_counter: Counter = Counter()
    for r in graded_rows:
        for chk in (r["failed_checks"].split("; ") if r["failed_checks"] else []):
            if chk:
                check_counter[chk] += 1
    if crashed:
        check_counter["ERRORED (crash, no result.json)"] = len(crashed)
    if integrity_rows:
        check_counter["ERRORED (data corruption in transit)"] = len(integrity_rows)
    fail_tally = "; ".join(f"{chk} x{cnt}"
                           for chk, cnt in check_counter.most_common()) or ""

    # Representative row: prefer a GRADED run (carries method/LoT/value_field/
    # truth_value). If every run errored, fall back to empty invariant fields so
    # the (case,model) still appears as all-errored.
    rep = graded_rows[0] if graded_rows else {
        "expect": "", "mode": "", "method": "", "solvent": "",
        "functional": "", "basis": "", "tier": "",
        "value_field": "", "truth_value": "", "reported_value": "",
    }
    # Per-cell timing (over this (case,model)'s scored runs that recorded timing).
    _tstats = {k: _stats([r[k] for r in scored_rows if isinstance(r.get(k), (int, float))])
               for k in _TIMING_KEYS}
    return {
        "case": case_dir.name,
        "model": model,
        "expect": rep["expect"],
        "mode": rep["mode"],
        "n_pass": n_pass,
        "n_error": n_error,
        "n_runs": n_runs,
        "pass_rate": pass_rate,
        "modal_verdict": modal_verdict,
        "fail_tally": fail_tally,
        "method": rep["method"],
        "solvent": rep["solvent"],
        "functional": rep["functional"],
        "basis": rep["basis"],
        "tier": rep["tier"],
        "value_field": rep["value_field"],
        "truth_value": rep["truth_value"],
        "reported_value_latest": rep["reported_value"],
        # Per-cell mean total wall-time (s) + its std/SE across this cell's runs.
        "total_s_mean": _tstats["total_s"]["mean"],
        "total_s_std": _tstats["total_s"]["std"],
        "total_s_se": _tstats["total_s"]["se"],
        "llm_s_mean": _tstats["llm_s"]["mean"],
        "engine_s_mean": _tstats["engine_s"]["mean"],
    }


def collect_repeats(base: Path, n: Optional[int] = None) -> List[Dict[str, Any]]:
    """One aggregated row per (case, model) over the n NEWEST runs each.

    For a --repeat N sweep: for every case and every model, take that model's n
    most-recent scored runs and aggregate them into pass_rate + modal verdict +
    failed-check tally (see _aggregate_repeat). Rows are grouped by model (models
    sorted, cases within each) exactly like collect_all, so the same grouped-CSV
    writer applies. If n is None, aggregates ALL scored runs per (case, model)."""
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for model, runs in _runs_per_model(case_dir, n=n).items():
            if not runs:
                continue
            by_model.setdefault(model, []).append(
                _aggregate_repeat(case_dir, model, runs))
    rows: List[Dict[str, Any]] = []
    for model in sorted(by_model):
        rows.extend(sorted(by_model[model], key=lambda r: r["case"]))
    return rows


def _print_table(rows: List[Dict[str, Any]]) -> None:
    # method shown inline plus the headline value comparison (truth vs reported)
    # and the imaginary-mode count (populated for vibrational analysis; blank
    # elsewhere); functional/basis/tier/solvent/zpe stay in the CSV to keep the
    # console narrow. Rows are printed in per-model sections (a `model: <name>`
    # banner before each group) to mirror the grouped summary.csv.
    cols = ["case", "expect", "overall", "method",
            "value_field", "truth_value", "reported_value", "n_imaginary"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols} if rows else {}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    current: Optional[str] = None
    for r in rows:
        m = r.get("model") or "(default)"
        if m != current:
            print()
            print(f"model: {m}")
            print(header)
            print("-" * len(header))
            current = m
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))
    n_pass = sum(1 for r in rows if r["overall"] == "PASS")
    print(f"\n{n_pass}/{len(rows)} (case,model) rows PASS")


def _print_repeat_table(rows: List[Dict[str, Any]]) -> None:
    """Console table for --repeat aggregation: pass rate + modal verdict +
    n_error + the failed-check tally, in per-model sections (mirrors the grouped
    summary.csv). n_error surfaces crashed runs so they are never hidden."""
    cols = ["case", "n_pass", "n_error", "n_runs", "pass_rate", "modal_verdict",
            "method", "value_field", "truth_value", "fail_tally"]
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols} if rows else {}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    current: Optional[str] = None
    for r in rows:
        m = r.get("model") or "(default)"
        if m != current:
            print()
            print(f"model: {m}")
            print(header)
            print("-" * len(header))
            current = m
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    # Aggregate over all (case,model) rows: mean pass rate, fully-reliable count,
    # and total errored runs — so a crashed sweep is loud, not silent.
    if rows:
        mean_rate = round(sum(r["pass_rate"] for r in rows) / len(rows), 3)
        perfect = sum(1 for r in rows if r["pass_rate"] == 1.0)
        total_err = sum(r.get("n_error", 0) for r in rows)
        err_note = f"; {total_err} ERRORED run(s) across all pairs" if total_err else ""
        print(f"\n{perfect}/{len(rows)} (case,model) pairs are 100% reliable; "
              f"mean pass_rate = {mean_rate}{err_note}")


def model_timing_stats(base: Path, n: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """Per-MODEL wall-clock timing over that model's individual runs.

    Returns per model: stats for llm_s, turns, tool_calls (the genuinely
    MODEL-DEPENDENT metrics — the model's own latency/thinking and how many
    round-trips/tool calls it needed), plus total_s/engine_s for reference.

    IMPORTANT: engine_s (and thus total_s) is TASK-bound, not model-bound — the
    DFT compute is identical whichever model calls it (nitrobenzene ~178 s for
    everyone; furan ~32 s). A per-model engine_s "mean" only reflects which
    molecules that model happened to sample, so DO NOT compare models on engine_s
    or total_s. Compare on llm_s / turns / tool_calls. For the (task-bound)
    compute cost, use molecule_engine_stats(). For an unconfounded model
    comparison holding the task fixed, use the per-(case,model) llm_s in the row
    columns."""
    keys = ("llm_s", "turns", "tool_calls", "total_s", "engine_s")
    per_model: Dict[str, Dict[str, List[float]]] = {}
    for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for model, runs in _runs_per_model(case_dir, n=n).items():
            acc = per_model.setdefault(model, {k: [] for k in keys})
            for run in runs:
                t = _run_timing_full(run)
                for k in keys:
                    if isinstance(t.get(k), (int, float)):
                        acc[k].append(float(t[k]))
    return {model: {k: _stats(acc[k]) for k in keys} for model, acc in per_model.items()}


def molecule_engine_stats(base: Path, n: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """Per-MOLECULE engine (DFT compute) wall-time — the TASK-bound cost.

    engine_s depends on the molecule + level of theory, not the calling model, so
    it is aggregated here per molecule (across ALL models' runs). Mean/std/SE of
    engine_s per molecule characterizes how expensive each calculation is."""
    per_mol: Dict[str, List[float]] = {}
    for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        vals: List[float] = []
        for _model, runs in _runs_per_model(case_dir, n=n).items():
            for run in runs:
                e = _run_timing(run).get("engine_s")
                if isinstance(e, (int, float)):
                    vals.append(float(e))
        if vals:
            per_mol[case_dir.name] = vals
    return {mol: {"engine_s": _stats(v)} for mol, v in per_mol.items()}


def write_grouped_csv(rows: List[Dict[str, Any]], csv_path: Path,
                      base: Optional[Path] = None, n: Optional[int] = None) -> None:
    """Write a human-oriented, model-grouped summary CSV.

    Layout: a header row of column names, then for each model a banner row
    ``model: <name>``, a per-model TIMING summary banner (mean/std/SE of
    total/llm/engine seconds across that model's runs), then that model's case
    rows, with a blank line between model sections. Each data row STILL carries
    the ``model`` column, so the file is also recoverable programmatically by
    skipping lines whose first cell starts with ``model:`` / ``timing:`` or is
    empty. `rows` is expected pre-grouped by model (as collect_repeats returns).

    If `base` is given, the per-model timing banner is computed EXACTLY from the
    raw per-run timings (std/SE over individual runs; pass the same `n` used for
    collect_repeats). Without `base`, the banner is omitted.

    Note: the banner rows make this NOT directly loadable by pandas.read_csv
    without filtering; it is a report, not a tidy CSV. That is intentional per
    the chosen layout."""
    fieldnames = list(rows[0].keys()) if rows else ["case"]
    mtiming = model_timing_stats(base, n=n) if base is not None else {}
    moltiming = molecule_engine_stats(base, n=n) if base is not None else {}

    def _fmt(st: Dict[str, Any]) -> str:
        if not st or st.get("mean") is None:
            return "n/a"
        mean, std, se, k = st["mean"], st.get("std"), st.get("se"), st.get("n")
        s = f"{mean}"
        if std is not None:
            s += f" ± {std} (SE {se})"
        return f"{s} [n={k}]"

    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(fieldnames)  # top column header
        current: Optional[str] = None
        dict_w = csv.DictWriter(fh, fieldnames=fieldnames)
        for r in rows:
            m = r.get("model") or "(default)"
            if m != current:
                if current is not None:
                    w.writerow([])            # blank line between model sections
                w.writerow([f"model: {m}"])   # banner/subtitle row
                mt = mtiming.get(m)
                if mt:
                    # MODEL-dependent metrics only: llm latency + round-trips +
                    # tool calls. engine_s/total_s are task-bound (see below) and
                    # deliberately NOT reported per-model to avoid the task-mix
                    # confound.
                    w.writerow([f"model latency: llm_s={_fmt(mt['llm_s'])}  "
                                f"turns={_fmt(mt['turns'])}  "
                                f"tool_calls={_fmt(mt['tool_calls'])}"])
                current = m
            dict_w.writerow(r)
        # Task-bound compute cost: engine_s per molecule (same for any model).
        if moltiming:
            w.writerow([])
            w.writerow(["# engine (DFT compute) wall-time per molecule — "
                        "TASK-bound (independent of the calling model)"])
            for mol in sorted(moltiming):
                w.writerow([f"engine_s[{mol}]: {_fmt(moltiming[mol]['engine_s'])}"])


def _rescore_all(base: Path) -> None:
    """Re-score every run under `base` in place using the CURRENT scorer, without
    re-invoking the model. Walks the same run folders collect_repeats reads
    (_all_runs), captures each run's old overall verdict, calls
    fidelity_driver.rescore_run(), and reports how many changed / were skipped."""
    import fidelity_driver as _fd  # local import (heavy module)

    def _old_overall(run: Path):
        rj = run / "result.json"
        if not rj.is_file():
            return None
        try:
            return json.loads(rj.read_text()).get("overall")
        except (OSError, ValueError):
            return None

    rescored = changed = skipped = 0
    for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for run in _all_runs(case_dir):
            before = _old_overall(run)
            rec = _fd.rescore_run(run)
            if rec is None:
                skipped += 1  # crashed/unscorable — left untouched
                continue
            rescored += 1
            if before is not None and rec.get("overall") != before:
                changed += 1
                print(f"  re-scored {case_dir.name}/{run.name}: "
                      f"{before} -> {rec.get('overall')}")
    print(f"[rescore] {rescored} run(s) re-scored, {changed} changed verdict, "
          f"{skipped} skipped (crashed/unscorable)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect fidelity results into a summary")
    ap.add_argument("--dir", default=str(_DEFAULT_DIR),
                    help="validation directory of case folders")
    ap.add_argument("--csv", default=None, help="CSV output path (default: <dir>/summary.csv)")
    ap.add_argument("--flat", action="store_true",
                    help="legacy flat CSV: one representative row per case (newest "
                         "run across all models), no per-model grouping. Default is "
                         "the model-grouped report (one row per (case,model)).")
    ap.add_argument("--rescore", action="store_true",
                    help="RE-SCORE every existing run in place before collecting: "
                         "recompute each result.json from its stored agent_run.json "
                         "+ engine-reference + spec using the CURRENT scorer (no "
                         "model calls). Use after a scoring-logic change to update "
                         "old runs without re-running the agent. Rewrites result.json "
                         "files — opt-in only.")
    args = ap.parse_args()

    base = Path(args.dir)
    if not base.is_dir():
        print(f"error: not a directory: {base}")
        return 2

    if args.rescore:
        _rescore_all(base)

    csv_path = Path(args.csv) if args.csv else base / "summary.csv"

    if args.flat:
        rows = collect(base)
        if not rows:
            print(f"No scored runs found under {base}")
            return 1
        _print_table(rows)
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    else:
        rows = collect_all(base)
        if not rows:
            print(f"No scored runs found under {base}")
            return 1
        _print_table(rows)
        write_grouped_csv(rows, csv_path, base=base)

    print(f"\nCSV written: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())