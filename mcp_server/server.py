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

import datetime
import json
import os
import re
import subprocess
import sys
import threading
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

# log_level="WARNING" keeps the SDK's per-request INFO chatter (e.g.
# "Processing request of type CallToolRequest") off the server's stderr, which a
# stdio caller inherits — so the caller's stderr leads with the live-log path and
# real diagnostics, not transport noise.
mcp = FastMCP("chemkit", log_level="WARNING")


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

    # Live `.out` log the user can `tail -f` while the calculation runs.
    # Written in the CALLER's cwd so it sits next to their inputs/outputs.
    # The engine prints the result JSON to stdout and all human/PySCF/xtb/mopac
    # log text to stderr; we tee ONLY stderr into the .out line-by-line (stdout
    # is kept clean so the returned JSON never gets corrupted), then append the
    # final result JSON under a banner so the file is self-contained.
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(run_cwd, f"{subcommand}_{stamp}.out")

    def _write_header(fh):
        fh.write(f"# chemkit live log\n")
        fh.write(f"# subcommand : {subcommand}\n")
        fh.write(f"# args       : {' '.join(args)}\n")
        fh.write(f"# command    : {' '.join(cmd)}\n")
        fh.write(f"# cwd        : {run_cwd}\n")
        fh.write(f"# started    : {stamp}\n")
        fh.write("# " + "=" * 60 + "\n")
        fh.flush()

    timed_out = False
    try:
        # line-buffered so `tail -f` sees lines as they are produced.
        log_fh = open(out_path, "w", buffering=1, encoding="utf-8")
    except OSError:
        # If the .out can't be created (e.g. read-only cwd), fall back to the
        # original buffered behavior rather than failing the calculation.
        log_fh = None

    try:
        if log_fh is not None:
            _write_header(log_fh)
            proc = subprocess.Popen(
                cmd, cwd=run_cwd, env=env, text=True, bufsize=1,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

            # Reader thread: tee engine stderr -> .out as it arrives.
            stderr_chunks: list[str] = []

            def _pump_stderr():
                assert proc.stderr is not None
                for line in proc.stderr:
                    stderr_chunks.append(line)
                    log_fh.write(line)
                    log_fh.flush()

            t = threading.Thread(target=_pump_stderr, daemon=True)
            t.start()

            # Announce the live-log path AT LAUNCH (before the blocking read
            # below), so a caller learns where to `tail -f` while the
            # calculation is still running — calculation-reporting-standards
            # non-negotiable #9. Flush so it isn't buffered behind the result.
            sys.stderr.write(f"# chemkit live log (tail -f): {out_path}\n")
            sys.stderr.flush()

            stdout_data = ""
            try:
                # proc.stdout.read() blocks until the engine closes stdout,
                # i.e. until the process is done writing its result JSON.
                stdout_data = proc.stdout.read() if proc.stdout else ""
                proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                timed_out = True
            t.join(timeout=5)
            stderr_data = "".join(stderr_chunks)

            # Make the .out self-contained: append the result JSON at the end.
            log_fh.write("\n# " + "=" * 60 + "\n")
            log_fh.write("# ===== RESULT JSON (stdout) =====\n")
            log_fh.write(stdout_data.strip() + "\n")
            log_fh.flush()
        else:
            # Fallback: no live log; behave like the old capture_output path.
            try:
                proc_run = subprocess.run(
                    cmd, cwd=run_cwd, env=env,
                    capture_output=True, text=True, timeout=3600,
                )
                stdout_data, stderr_data = proc_run.stdout, proc_run.stderr
                returncode = proc_run.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                stdout_data = stderr_data = ""
    finally:
        if log_fh is not None:
            log_fh.close()

    if timed_out:
        return json.dumps({"error": "calculation timed out (3600 s)",
                           "subcommand": subcommand, "args": args,
                           "out_log": out_path})

    returncode = proc.returncode if log_fh is not None else returncode
    # (The live-log path is announced once at launch on line ~153 and is also
    # returned to the caller under `out_log`; no end-of-run duplicate needed.)

    if returncode != 0:
        # An integrity hard-abort exits nonzero but STILL prints the full
        # structured result (with an `integrity` block) to stdout. Preserve that
        # structured result — augmented with an `error` key so the caller still
        # treats it as a failure — instead of throwing it away for a truncated
        # stdout stub. This keeps the integrity verdict, warnings, and out-path
        # reachable by the agent while signalling that the number is untrustworthy.
        parsed = None
        try:
            parsed = json.loads(stdout_data.strip())
        except ValueError:
            parsed = None
        # Recognize either the full result JSON (has an `integrity` block, from
        # --stdout json) or the compact pointer (has `status`/`trustworthy`, from
        # --stdout path). Either way the structured verdict is worth preserving.
        is_integrity_result = isinstance(parsed, dict) and (
            isinstance(parsed.get("integrity"), dict)
            or ("trustworthy" in parsed and "status" in parsed)
        )
        if is_integrity_result:
            parsed["error"] = "integrity gate failed (result is not trustworthy)"
            parsed["returncode"] = returncode
            parsed.setdefault("out_log", out_path)
            return json.dumps(parsed)
        return json.dumps({
            "error": "chemkit engine exited non-zero",
            "returncode": returncode,
            "subcommand": subcommand, "args": args,
            "stderr": stderr_data.strip()[-4000:],
            "stdout": stdout_data.strip()[-2000:],
        })
    # On success the JSON result is on stdout. Inject the live-log path under
    # `out_log` so the caller (and ultimately the agent) learns where to
    # `tail -f` it — calculation-reporting-standards non-negotiable #9. The
    # server's own stderr writes (above) do NOT reach a stdio MCP caller, so the
    # returned JSON is the only channel that crosses back to the Bash tool.
    out = stdout_data.strip()
    try:
        parsed = json.loads(out)
        if log_fh is not None and isinstance(parsed, dict) and "out_log" not in parsed:
            parsed["out_log"] = out_path
            return json.dumps(parsed)
        return out
    except ValueError:
        wrapped = {"raw_stdout": out, "stderr": stderr_data.strip()}
        if log_fh is not None:
            wrapped["out_log"] = out_path
        return json.dumps(wrapped)


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
