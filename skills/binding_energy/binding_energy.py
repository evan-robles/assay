#!/usr/bin/env python3
"""Thin client for the `binding_energy` chemkit skill.

The chemistry engine runs in the chemkit MCP server (mcp_server/server.py); this
script just forwards its arguments to the `binding_energy` MCP tool and prints the JSON
result. Set CHEMKIT_MCP to point at a specific server.py to use a custom server.

Usage:  python binding_energy.py [chemkit args...]      (try --help)
"""
import os
import sys

# Make the shared MCP client importable (skills/_mcp_client.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _mcp_client import run_skill  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_skill("binding_energy", sys.argv[1:]))
