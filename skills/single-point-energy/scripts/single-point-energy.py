#!/usr/bin/env python3
"""Thin client for the `single-point-energy` chemkit skill.

The chemistry engine runs in the chemkit MCP server (mcp_server/server.py); this
script forwards its arguments to the `single-point-energy` MCP tool and prints the JSON
result. Set CHEMKIT_MCP to point at a specific server.py to use a custom server.

Usage:
    # Env: anl_env
    python skills/single-point-energy/scripts/single-point-energy.py [chemkit args]   # try --help

Requirements:
    - Conda environment: anl_env
    - Required packages: mcp (the chemkit MCP server hosts the engine)
"""
import os
import sys

# skills/<name>/scripts/<name>.py -> skills/ holds the shared _mcp_client.
_SKILLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _SKILLS_DIR)
from _mcp_client import run_skill  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_skill("single-point-energy", sys.argv[1:]))
