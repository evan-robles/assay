"""Parameter persistence — the `<result-stem>_input_configs.yaml` writer.

skill-standards.md (§Parameter Persistence) requires that any skill accepting
input kwargs/hyperparameters save ALL of them — both the values the caller set
AND the defaults of the ones they did not — to a SEPARATE config file
(`<result-stem>_input_configs.yaml`) in the same output directory as the result, so a run can be fully interpreted and
reproduced later without re-reading the source or the shell history. Configs are
deliberately kept OUT of the result JSON (no `"config"` key inside results).

This lives in `assay_core` so the engine CLI and every stand-alone skill run
persist configs identically (preservation matrix #12).
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict

# argparse attributes that are plumbing, not scientific parameters, so they are
# excluded from the persisted config (they don't affect the result's meaning).
_NON_PARAM_KEYS = {"task", "func", "stdout"}


def _args_to_params(args: argparse.Namespace) -> Dict[str, Any]:
    """All parsed argparse values (user-set AND defaulted), minus pure plumbing.

    argparse's Namespace already contains every declared option with its default
    filled in when the caller omitted it — which is exactly the "user values +
    defaults" the standard asks for."""
    params: Dict[str, Any] = {}
    for k, v in sorted(vars(args).items()):
        if k in _NON_PARAM_KEYS or k.startswith("_"):
            continue
        params[k] = v
    return params


def write_input_configs(args: argparse.Namespace, out_path: str, *,
                        task: str) -> str:
    """Write `<result-stem>_input_configs.yaml` next to the result at `out_path`.

    Returns the path written. Best-effort: the caller wraps this so a persistence
    failure never fails the calculation.
    """
    out_dir = os.path.dirname(os.path.abspath(out_path)) or os.getcwd()
    # Named after the RESULT it documents, not a bare `input_configs.yaml`.
    # A fixed filename is silently overwritten by the next run in the same
    # directory: result JSONs are uniquely named (<stem>_<task>_<method>.json),
    # so a directory could hold five results but only one config -- describing
    # whichever ran last, and quietly voiding the reproducibility guarantee this
    # file exists to provide. Pairing it with the result stem keeps one config
    # per result.
    stem = os.path.splitext(os.path.basename(out_path))[0]
    cfg_path = os.path.join(out_dir, f"{stem}_input_configs.yaml")
    payload = {
        "task": task,
        "result_json": os.path.abspath(out_path),
        "parameters": _args_to_params(args),
    }
    try:
        import yaml
        text = yaml.safe_dump(payload, default_flow_style=False, sort_keys=False)
    except Exception:  # noqa: BLE001 - pyyaml missing or unserializable value
        text = _minimal_yaml(payload)
    with open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return cfg_path


def _minimal_yaml(payload: Dict[str, Any]) -> str:
    """Tiny YAML fallback for the flat {task, result_json, parameters:{...}} shape
    used here, so config persistence still works if pyyaml is unavailable."""
    lines = [f"task: {payload['task']}",
             f"result_json: {payload['result_json']}",
             "parameters:"]
    for k, v in payload["parameters"].items():
        if v is None:
            rendered = "null"
        elif isinstance(v, bool):
            rendered = "true" if v else "false"
        else:
            rendered = str(v)
        lines.append(f"  {k}: {rendered}")
    return "\n".join(lines) + "\n"
