"""Shared thin MCP client for the chemkit skills.

Each skill's `<name>.py` is a ~10-line wrapper that calls `run_skill(...)` here.
This module speaks the open MCP protocol to the chemkit MCP server (which owns
the one unified engine), so no skill carries the chemistry engine itself.

Connection:
  * If the env var CHEMKIT_MCP is set, it is treated as the path to a running
    server's stdio command is NOT assumed — instead CHEMKIT_MCP may point to the
    server.py to launch (so a caller can pin a specific server). If unset, we
    launch the bundled mcp_server/server.py.
  * A fresh server subprocess is spawned per invocation by default (simple and
    robust). Long-lived reuse is handled by the AI/MCP host, which keeps the
    server running and calls tools directly — bypassing this script entirely.

Requires the `mcp` Python SDK (pip install mcp).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Keep the MCP SDK's own INFO chatter (e.g. "Processing request of type
# CallToolRequest") off our stderr, so the live-log line and any real
# diagnostics are the first things the caller sees rather than being buried
# behind transport logging.
logging.getLogger("mcp").setLevel(logging.WARNING)

# Repo layout: skills/_mcp_client.py  and  mcp_server/server.py
_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_SERVER = _REPO / "mcp_server" / "server.py"


def _server_path() -> str:
    env = os.environ.get("CHEMKIT_MCP")
    if env:
        return env
    return str(_DEFAULT_SERVER)


async def _call(tool_name: str, args: list[str]) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = _server_path()
    params = StdioServerParameters(
        command=sys.executable, args=[server], cwd=str(Path(server).parent),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                tool_name, {"args": list(args), "cwd": os.getcwd()},
            )
            # FastMCP returns the tool's string return as text content.
            parts = [c.text for c in result.content if getattr(c, "text", None)]
            return "\n".join(parts) if parts else ""


def run_skill(tool_name: str, argv: list[str] | None = None) -> int:
    """Call the MCP tool `tool_name` with CLI-style argv; print JSON; return rc.

    Mirrors the old CLI behavior: the result JSON goes to stdout. Exit code is 1
    if the engine reported an error object, else 0.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        out = asyncio.run(_call(tool_name, argv))
    except ModuleNotFoundError as e:
        if e.name == "mcp":
            sys.stderr.write(
                "This skill needs the MCP client SDK: pip install mcp\n"
            )
            return 2
        raise
    except Exception as e:  # noqa: BLE001 - surface any transport error cleanly
        sys.stderr.write(f"MCP call failed: {e}\n")
        return 2
    # Detect the engine's structured error object. On error, surface the
    # engine's own stderr to our stderr (so `python <skill>.py` behaves like the
    # old CLI: diagnostic text on stderr, nonzero exit), and skip printing the
    # error JSON blob to stdout.
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = None

    # Surface the live `.out` log path as the FIRST stderr line so it lands at
    # the top of the agent's Bash tool result on EVERY run, regardless of the
    # --stdout mode. The server injects `out_log` into the returned JSON; stderr
    # is part of the Bash result, so this makes the live-log path
    # model-independent — the agent no longer has to choose to fetch it.
    # calculation-reporting-standards non-negotiable #9.
    if isinstance(parsed, dict):
        out_log = parsed.get("out_log")
        if out_log:
            sys.stderr.write(
                f"chemkit: live log (watch now): tail -f {out_log}\n"
                "# Tell the user this path immediately, while the run is going "
                "(non-negotiable #9).\n"
            )

    if isinstance(parsed, dict) and "error" in parsed:
        engine_stderr = parsed.get("stderr") or ""
        if engine_stderr:
            sys.stderr.write(engine_stderr.rstrip() + "\n")
        sys.stderr.write(f"chemkit: {parsed['error']}\n")
        return 1
    print(out)
    return 0
