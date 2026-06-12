#!/usr/bin/env python3
"""chemkit MCP server — one unified engine behind the open MCP protocol.

Exposes every chemkit skill as an MCP tool. The chemistry engine lives once, in
`mcp_server/_engine/`; this server owns it and dispatches each tool call to the
engine's CLI. Built on the official `mcp` SDK (FastMCP) over stdio, so it works
with ANY MCP-capable client, not just one vendor.

Each tool mirrors a chemkit subcommand. A tool takes the same arguments the CLI
takes, as a list of CLI tokens (`args`), runs `python -m _engine.cli <task>
<args>` as an isolated subprocess, and returns the JSON result the engine prints.
Running each calculation in its own process keeps long, stateful QM jobs (pyscf
globals, matplotlib backends, chdir/tmpdirs) from leaking across calls.

Run:  python mcp_server/server.py        # stdio MCP server
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
ENGINE_DIR = HERE / "_engine"
SKILLS_DIR = HERE.parent / "skills"

# tool name -> (engine subcommand, skill folder for its SKILL.md description)
# Tool names == skill folder names (kebab-case). Mirrors the chemkit CLI
# subcommands; one entry per skill.
TOOLS = {
    "single-point-energy":     ("sp",             "single-point-energy"),
    "geometry-optimize":       ("opt",            "geometry-optimize"),
    "vibrational-analysis":    ("freq",           "vibrational-analysis"),
    "binding-energy":          ("binding",        "binding-energy"),
    "redox-potential":         ("redox",          "redox-potential"),
    "conformer-search":        ("confsearch",     "conformer-search"),
    "frontier-orbitals":       ("frontier",       "frontier-orbitals"),
    "electrostatics":          ("electrostatics", "electrostatics"),
    "solvation":               ("solvation",      "solvation"),
    "logp-partition":          ("logp",           "logp-partition"),
    "reaction-profile":        ("profile",        "reaction-profile"),
    "pka-acidity":             ("pka",            "pka-acidity"),
    "build-from-smiles":       ("build",          "build-from-smiles"),
    "fukui-reactivity":        ("fukui",          "fukui-reactivity"),
    "transition-state":        ("ts",             "transition-state"),
    "intrinsic-reaction-coordinate": ("irc",      "intrinsic-reaction-coordinate"),
    "reaction-energy":         ("rxn-energy",     "reaction-energy"),
    "conformational-analysis": ("scan",           "conformational-analysis"),
    "visualize-orbitals":      ("orbitals",       "visualize-orbitals"),
}

mcp = FastMCP("chemkit")


def _description(skill_folder: str, subcommand: str) -> str:
    """Build a tool description from the skill's SKILL.md frontmatter + a usage
    line, so an AI knows what the tool does and how to pass `args`."""
    md = SKILLS_DIR / skill_folder / "SKILL.md"
    desc = ""
    if md.is_file():
        text = md.read_text()
        m = re.search(r"^description:\s*(.+?)\s*$", text, re.MULTILINE)
        if m:
            desc = m.group(1).strip()
    usage = (
        f"\n\nInvoke by passing the chemkit `{subcommand}` arguments as a list "
        f"of CLI tokens in `args` (e.g. [\"--method\", \"xtb\", \"mol.xyz\"]). "
        f"Run with args=[\"--help\"] to see the full argument list. Returns the "
        f"result as JSON."
    )
    return (desc or f"chemkit {subcommand}") + usage


def _run_engine(subcommand: str, args: list[str], cwd: str | None = None) -> str:
    """Run the engine CLI as an isolated subprocess; return its JSON stdout.

    `cwd` is the CALLER's working directory: relative input paths and `--out`
    destinations must resolve against where the user/AI invoked the tool, not
    against the server's own directory. Defaults to the server dir if absent.

    The engine prints the result JSON to stdout and human notes to stderr. On
    failure we return a JSON error object rather than raising, so the client
    always gets structured output.
    """
    env = dict(os.environ)
    # Make `import _engine` resolve to mcp_server/_engine for the subprocess.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERE), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    run_cwd = cwd if (cwd and os.path.isdir(cwd)) else str(HERE)
    cmd = [sys.executable, "-m", "_engine.cli", subcommand, *args]
    try:
        proc = subprocess.run(
            cmd, cwd=run_cwd, env=env,
            capture_output=True, text=True, timeout=3600,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "calculation timed out (3600 s)",
                           "subcommand": subcommand, "args": args})
    if proc.returncode != 0:
        return json.dumps({
            "error": "chemkit engine exited non-zero",
            "returncode": proc.returncode,
            "subcommand": subcommand, "args": args,
            "stderr": proc.stderr.strip()[-4000:],
            "stdout": proc.stdout.strip()[-2000:],
        })
    # On success the JSON result is on stdout; pass it through verbatim if it
    # parses, else wrap it.
    out = proc.stdout.strip()
    try:
        json.loads(out)
        return out
    except ValueError:
        return json.dumps({"raw_stdout": out, "stderr": proc.stderr.strip()})


def _make_tool(tool_name: str, subcommand: str, skill_folder: str):
    """Register one MCP tool that dispatches to `subcommand`."""
    description = _description(skill_folder, subcommand)

    @mcp.tool(name=tool_name, description=description)
    def _tool(args: list[str] | None = None, cwd: str | None = None) -> str:
        """args: chemkit CLI tokens for this task. cwd: directory to resolve
        relative input/output paths against (the caller's working dir)."""
        return _run_engine(subcommand, list(args or []), cwd=cwd)

    return _tool


for _name, (_sub, _folder) in TOOLS.items():
    _make_tool(_name, _sub, _folder)


if __name__ == "__main__":
    mcp.run()  # stdio transport
