#!/usr/bin/env python3
"""Generate + install the ASSAY MCP server wiring from assay.toml.

Reads the declarative config in ``assay.toml``, resolves the local conda env's
absolute paths, and writes ``mcp_config.json`` (the concrete env->python->server
command an MCP host launches). Optionally installs that entry into the agent's
MCP config (``.mcp.json`` at the repo root by default), mirroring the
AtomisticSkills ``configure_mcp.py`` pattern — but single-env, since every ASSAY
skill shares one conda env and imports assay_core in-process.

Usage:
    python configure_mcp.py                 # write mcp_config.json
    python configure_mcp.py --install       # also merge into ./.mcp.json
    python configure_mcp.py --print         # print the config, write nothing
    python configure_mcp.py --conda-env X   # override the env from assay.toml
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent
_TOML = _REPO / "assay.toml"


def _load_toml(path: Path) -> dict:
    try:
        import tomllib  # py311+
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ModuleNotFoundError:
        try:
            import tomli  # backport
            with open(path, "rb") as fh:
                return tomli.load(fh)
        except ModuleNotFoundError:
            sys.exit("configure_mcp: need Python 3.11+ or `pip install tomli` to read assay.toml")


def _conda_prefix(env_name: str) -> Path | None:
    """Absolute prefix of the named conda env, or None if it can't be located.

    Tries `conda env list` output; falls back to the common miniforge/anaconda
    layout <base>/envs/<name> derived from the active CONDA_PREFIX.
    """
    conda = shutil.which("conda")
    if conda:
        import subprocess
        try:
            out = subprocess.run([conda, "env", "list", "--json"],
                                 capture_output=True, text=True, timeout=30)
            envs = json.loads(out.stdout).get("envs", [])
            for e in envs:
                if Path(e).name == env_name:
                    return Path(e)
        except Exception:  # noqa: BLE001
            pass
    # Fallback: sibling of the active env under <base>/envs/.
    active = os.environ.get("CONDA_PREFIX")
    if active:
        base = Path(active)
        # If we're in an env, its parent is <base>/envs; if in base, use base/envs.
        envs_dir = base.parent if base.parent.name == "envs" else base / "envs"
        cand = envs_dir / env_name
        if cand.exists():
            return cand
    return None


def build_config(cfg: dict, env_name: str) -> dict:
    """Build the {mcpServers: {name: {command, args, env}}} wiring."""
    server = cfg.get("server", {})
    name = server.get("name", "assay")
    entry = server.get("entry_point", "assay-mcp")
    module = server.get("module", "mcp_server.server")

    prefix = _conda_prefix(env_name)
    if prefix is not None and (prefix / "bin" / entry).exists():
        # Preferred: the installed console script in the env — no activation needed.
        command = str(prefix / "bin" / entry)
        args: list[str] = []
        env = {}
    elif prefix is not None:
        # Env exists but the package isn't installed there yet: run the module
        # with that env's python + repo on PYTHONPATH.
        command = str(prefix / "bin" / "python")
        args = ["-m", module]
        env = {"PYTHONPATH": str(_REPO)}
    else:
        # No resolvable env: fall back to a bare entry-point name (assumes it is
        # on PATH when the host launches it) + repo on PYTHONPATH.
        command = entry
        args = []
        env = {"PYTHONPATH": str(_REPO)}

    return {"mcpServers": {name: {"command": command, "args": args, "env": env}}}


def _merge_into(target: Path, config: dict) -> None:
    existing = {}
    if target.is_file():
        try:
            existing = json.loads(target.read_text())
        except ValueError:
            existing = {}
    existing.setdefault("mcpServers", {})
    existing["mcpServers"].update(config["mcpServers"])
    target.write_text(json.dumps(existing, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="generate/install the ASSAY MCP wiring")
    ap.add_argument("--conda-env", default=None, help="override the env from assay.toml")
    ap.add_argument("--install", action="store_true",
                    help="merge the wiring into ./.mcp.json (the agent MCP config)")
    ap.add_argument("--install-into", default=None,
                    help="merge into a specific MCP config path instead of ./.mcp.json")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="print the config to stdout and write nothing")
    args = ap.parse_args(argv)

    cfg = _load_toml(_TOML)
    env_name = args.conda_env or cfg.get("project", {}).get("conda_env", "anl_env")
    config = build_config(cfg, env_name)
    blob = json.dumps(config, indent=2)

    if args.print_only:
        print(blob)
        return 0

    out_path = _REPO / "mcp_config.json"
    out_path.write_text(blob + "\n")
    print(f"wrote {out_path.relative_to(_REPO)} (conda_env={env_name})")

    if args.install or args.install_into:
        target = Path(args.install_into) if args.install_into else (_REPO / ".mcp.json")
        _merge_into(target, config)
        print(f"installed 'assay' MCP server into {target}")
    else:
        print("run with --install to merge it into ./.mcp.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
