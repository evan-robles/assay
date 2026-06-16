#!/usr/bin/env python3
"""Drive chemkit from an OpenAI model via the OpenAI Agents SDK (native MCP).

This proves a non-Claude agent can use chemkit's 19 tools with NO per-tool glue:
the Agents SDK speaks MCP natively, so we just point `MCPServerStdio` at the
`chemkit-mcp` console command. The SDK spawns the stdio server, lists its tools,
exposes them to the model, runs the tool-call loop, and tears the process down.

Prerequisites
-------------
1. chemkit installed so the `chemkit-mcp` command exists:
       pip install -e ".[qm]"        # from the repo root
       # or once published:  pip install "chemkit-mcp[qm]"
2. External chemistry binaries on PATH (NOT pip-installable):
       conda install -c conda-forge xtb mopac openbabel
3. The OpenAI Agents SDK and an API key:
       pip install openai-agents
       export OPENAI_API_KEY=sk-...   # your key; never hard-code it

Run
---
    python run_chemkit_agent.py
    python run_chemkit_agent.py "Compute the pKa of acetic acid with xtb."

The default task builds acetone and reports its HOMO/LUMO from a real GFN2-xTB
run. Relative output paths resolve against this process's working directory.

The 19 chemkit tools the model will see:
    single-point-energy, geometry-optimize, vibrational-analysis, binding-energy,
    redox-potential, conformer-search, frontier-orbitals, electrostatics,
    solvation, logp-partition, reaction-profile, pka-acidity, build-from-smiles,
    fukui-reactivity, transition-state, intrinsic-reaction-coordinate,
    reaction-energy, conformational-analysis, visualize-orbitals.
Each takes an `args` list of CLI tokens (e.g. ["--method","xtb","mol.xyz"]);
args=["--help"] on any tool returns its full argument list.
"""
from __future__ import annotations

import asyncio
import os
import sys

try:
    from agents import Agent, Runner
    from agents.mcp import MCPServerStdio
except ImportError:
    sys.exit(
        "openai-agents is not installed. Run:  pip install openai-agents\n"
        "(and set OPENAI_API_KEY in your environment)."
    )

DEFAULT_TASK = (
    "Build acetone from its SMILES (CC(=O)C), then compute its HOMO and LUMO "
    "energies with the xtb method. Report the two orbital energies in eV and "
    "the HOMO-LUMO gap. Use the chemkit tools; do not guess the numbers."
)

MODEL = os.environ.get("CHEMKIT_OPENAI_MODEL", "gpt-4o")

INSTRUCTIONS = (
    "You are a computational-chemistry assistant with access to chemkit's tools "
    "over MCP. Each tool takes an `args` list of CLI tokens mirroring the chemkit "
    "CLI; call a tool with args=['--help'] first if you are unsure of its "
    "arguments. Never fabricate a numerical result — only report values that a "
    "tool actually returned, and state the method/level of theory used."
)


async def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set. export OPENAI_API_KEY=sk-... and retry.")

    task = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TASK

    # Path-free launch: relies on the `chemkit-mcp` console command being on PATH.
    async with MCPServerStdio(
        name="chemkit",
        params={"command": "chemkit-mcp", "args": []},
        # chemkit calls can be slow (QM); give the stdio client generous time.
        client_session_timeout_seconds=600,
    ) as chemkit:
        tools = await chemkit.list_tools()
        print(f"chemkit exposed {len(tools)} tools to the model.\n")

        agent = Agent(
            name="Chem assistant",
            instructions=INSTRUCTIONS,
            mcp_servers=[chemkit],
            model=MODEL,
        )

        print(f"Task: {task}\n{'-' * 60}")
        result = await Runner.run(agent, task)
        print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
