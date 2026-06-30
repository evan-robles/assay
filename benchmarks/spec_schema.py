#!/usr/bin/env python3
"""Static schema validator for fidelity *.spec.json files.

This is the STATIC complement to probe_specs.py (which runs the engine). It
validates every spec's SHAPE without running any chemistry, so a malformed spec
— a typo'd key, a `report_value_field` the engine can't emit, an `intended.method`
that isn't real, a referenced `xyz` that doesn't exist — is caught in <1 s
across all suites instead of at run time (or, worse, silently mis-scoring the
crown-jewel fidelity benchmark).

The check that matters most: `report_value_field` must be either null or the
CANONICAL headline field the engine actually emits for that skill's task — read
from chemkit_engine.result_schema.HEADLINE (the same registry the engine's
canonicalize() uses). This makes "Layer C scored the right field" verifiable
statically, not dependent on hand-kept maps.

Usage:
    # Env: anl_env
    python benchmarks/spec_schema.py                 # validate every *-validation suite
    python benchmarks/spec_schema.py <suite-dir>     # one suite
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "mcp_server"))

# Allowed enum values, mirrored from the engine CLI.
_METHODS = {"xtb", "mopac", "dft", "hf"}
_EXPECT = {"compute", "failure", "structure", "smiles", "refusal"}
_INPUT_KIND = {"xyz", "string", "none"}

# Skills that do NOT take a QM --method {xtb,mopac,dft,hf}: build/resolve have no
# method (intended.method ""), conformer-search uses a force field. Their
# intended.method is exempt from the QM-enum check.
_NON_QM_SKILLS = {"build-from-smiles", "name-to-smiles", "conformer-search"}

# Top-level keys a spec may carry (superset across all expect modes). Unknown
# keys are flagged so a typo'd key name can't silently do nothing.
_KNOWN_KEYS = {
    "name", "skill", "prompt", "intended_flags", "intended", "rules",
    "report_value_field", "xyz", "input", "inputs", "input_kind", "expect",
    "value_tol", "energy_tol_eV", "expected_n_atoms", "expected_formula",
}
_REQUIRED_KEYS = {"name", "skill", "prompt", "intended_flags", "intended"}


def _skill_to_taskid() -> Dict[str, Optional[str]]:
    """Map a spec's `skill` (kebab folder name) -> the engine task-id used as the
    HEADLINE registry key. Built from the live engine, so it can't drift:
      skill(folder) --TOOLS--> subcommand --engine--> task-id.
    """
    from server import TOOLS  # folder == tool name == spec.skill
    from chemkit_engine import cli  # subcommand set (validation only)

    folder_to_sub = {folder: sub for (sub, folder) in TOOLS.values()}
    # subcommand -> task-id: the engine's _dispatch maps subcommand to a task
    # module whose base_result task= string is the registry key. We resolve it
    # by importing the dispatch table indirectly: the result_schema HEADLINE keys
    # ARE the task-ids, and cli subcommands map 1:1; build the bridge explicitly
    # from the documented correspondence (subcommand -> task-id).
    sub_to_task = {
        "sp": "single_point", "opt": "geometry_optimization",
        "freq": "vibrational_thermochemistry", "binding": "binding_energy",
        "redox": "redox_potential", "confsearch": "conformational_search",
        "frontier": "frontier_orbitals", "electrostatics": "electrostatics",
        "solvation": "solvation", "logp": "logp", "profile": "reaction_profile",
        "pka": "pka", "build": "build_from_smiles", "resolve": "name_to_smiles",
        "fukui": "fukui", "ts": "transition_state",
        "irc": "intrinsic_reaction_coordinate", "rxn-energy": "reaction_energy",
        "scan": "conformational_analysis", "orbitals": "visualize_orbitals",
    }
    # sanity: every subcommand the CLI knows must be covered here
    cli_subs = set(cli.subcommand_names())
    assert cli_subs <= set(sub_to_task), \
        f"sub_to_task missing CLI subcommands: {cli_subs - set(sub_to_task)}"
    return {folder: sub_to_task.get(sub) for folder, sub in folder_to_sub.items()}


def _resolve_path(p: str) -> Path:
    return Path(p) if os.path.isabs(p) else _REPO / p


def validate_spec(spec_path: Path, skill_to_task: Dict[str, Optional[str]],
                  headline: Dict[str, Any]) -> List[str]:
    """Return a list of problem strings for one spec ([] = valid)."""
    problems: List[str] = []
    try:
        d = json.loads(spec_path.read_text())
    except (ValueError, OSError) as e:
        return [f"unreadable/invalid JSON: {e}"]

    # required keys + unknown-key typo guard
    for k in _REQUIRED_KEYS:
        if k not in d:
            problems.append(f"missing required key {k!r}")
    for k in d:
        if k not in _KNOWN_KEYS:
            problems.append(f"unknown key {k!r} (typo?)")

    skill = d.get("skill")
    expect = d.get("expect", "compute")
    if expect not in _EXPECT:
        problems.append(f"expect={expect!r} not in {sorted(_EXPECT)}")

    # intended block + method
    intended = d.get("intended")
    if not isinstance(intended, dict):
        problems.append("intended must be an object")
    else:
        m = intended.get("method")
        # Only QM skills take a --method {xtb,mopac,dft,hf}. build/resolve have
        # no method; conformer-search uses a force field. Exempt those.
        if (m is not None and m not in _METHODS
                and skill not in _NON_QM_SKILLS):
            problems.append(f"intended.method={m!r} not in {sorted(_METHODS)}")

    if "input_kind" in d and d["input_kind"] not in _INPUT_KIND:
        problems.append(f"input_kind={d['input_kind']!r} not in {sorted(_INPUT_KIND)}")

    # input existence: xyz / inputs[].xyz / inputs[].spec must point at real files
    if "xyz" in d and not _resolve_path(d["xyz"]).is_file():
        problems.append(f"xyz not found: {d['xyz']}")
    for item in d.get("inputs", []) or []:
        if item.get("xyz") and not _resolve_path(item["xyz"]).is_file():
            problems.append(f"inputs xyz not found: {item['xyz']}")
        if item.get("spec"):
            # spec form: "[coef*]path[,mult=N]" — strip coefficient and ,mult=
            raw = item["spec"].split("*", 1)[-1].split(",", 1)[0]
            if not _resolve_path(raw).is_file():
                problems.append(f"inputs spec path not found: {raw}")

    # the headline check: report_value_field must be null OR the canonical
    # headline field for this skill's task (so Layer-C scores the right field).
    if "report_value_field" in d:
        rvf = d["report_value_field"]
        task = skill_to_task.get(skill)
        if task is None and skill is not None:
            problems.append(f"skill {skill!r} has no known engine task mapping")
        elif rvf is not None and task is not None:
            hf = headline.get(task)
            # redox's field carries an electrode suffix -> accept the prefix
            if task == "redox_potential":
                if not str(rvf).startswith("redox_potential_V_vs_"):
                    problems.append(
                        f"report_value_field {rvf!r} not a redox_potential_V_vs_* field")
            elif hf is None:
                problems.append(
                    f"skill {skill!r} (task {task}) reports no scalar headline, "
                    f"but report_value_field={rvf!r} is set (should be null)")
            elif rvf != hf[0]:
                problems.append(
                    f"report_value_field {rvf!r} != canonical headline {hf[0]!r} "
                    f"for task {task} (Layer-C would score the wrong/absent field)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="static fidelity-spec schema validator")
    ap.add_argument("suite", nargs="?", help="one *-validation folder (default: all)")
    args = ap.parse_args()

    from chemkit_engine import result_schema
    skill_to_task = _skill_to_taskid()
    headline = result_schema.HEADLINE

    base = _REPO / "benchmarks" / "fidelity"
    if args.suite:
        s = Path(args.suite)
        suites = [s if s.is_dir() else _REPO / args.suite]
    else:
        suites = sorted(base.glob("*-validation"))

    specs = sorted(p for suite in suites for p in suite.glob("*/*.spec.json"))
    if not specs:
        # single-point-validation nests specs one level shallower in some cases
        specs = sorted(p for suite in suites for p in suite.glob("**/*.spec.json"))

    n_bad = 0
    for sp in specs:
        problems = validate_spec(sp, skill_to_task, headline)
        if problems:
            n_bad += 1
            rel = sp.relative_to(_REPO)
            print(f"[FAIL] {rel}")
            for p in problems:
                print(f"        - {p}")
    total = len(specs)
    print(f"\n{total - n_bad}/{total} specs valid"
          + (f" ({n_bad} with problems)" if n_bad else " — all clean"))
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
