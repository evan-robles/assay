#!/usr/bin/env python3
"""Generate the thin per-skill client scripts.

Architecture: the chemistry engine lives ONCE behind the MCP server
(`mcp_server/server.py` + `mcp_server/_engine/`). Each skill folder keeps just:

  skills/<name>/
    SKILL.md            - the skill doc (authored; left untouched here)
    <name>.py           - thin MCP client (~12 lines) -> calls the skill's tool
    requirements.txt    - client deps (the `mcp` SDK) + a pointer to the server

The thin client imports skills/_mcp_client.py, which speaks the open MCP protocol
to the server. No engine code is duplicated into the skills anymore.

Run from the repo root:  python tools/build_skill_folders.py
"""
from __future__ import annotations

import os
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(REPO, "skills")

# Skill folders == MCP tool names (set by mcp_server/server.py::TOOLS).
TOOLS = [
    "single_point_energy", "geometry_optimize", "vibrational_analysis",
    "binding_energy", "redox_potential", "conformer_search",
    "frontier_orbitals", "electrostatics", "solvation", "logp",
    "reaction_profile", "pka", "build_from_smiles", "fukui",
    "transition_state", "irc", "reaction_energy", "conformational_analysis",
    "visualize_orbitals",
]

CLIENT_TEMPLATE = '''#!/usr/bin/env python3
"""Thin client for the `{name}` chemkit skill.

The chemistry engine runs in the chemkit MCP server (mcp_server/server.py); this
script just forwards its arguments to the `{name}` MCP tool and prints the JSON
result. Set CHEMKIT_MCP to point at a specific server.py to use a custom server.

Usage:  python {name}.py [chemkit args...]      (try --help)
"""
import os
import sys

# Make the shared MCP client importable (skills/_mcp_client.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _mcp_client import run_skill  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_skill("{name}", sys.argv[1:]))
'''

REQUIREMENTS = """\
# This skill is a thin client for the chemkit MCP server; it carries no
# chemistry engine. It needs only the MCP client SDK:
mcp

# The actual computation runs in the chemkit MCP server. Install the server's
# dependencies once (see mcp_server/requirements.txt): ase, numpy, pyscf,
# matplotlib, plus the external binaries xtb / mopac / openbabel.
"""


def main():
    n = 0
    for name in TOOLS:
        folder = os.path.join(SKILLS, name)
        if not os.path.isdir(folder):
            print(f"  WARNING: skills/{name}/ missing — skipping")
            continue
        with open(os.path.join(folder, f"{name}.py"), "w") as f:
            f.write(CLIENT_TEMPLATE.format(name=name))
        with open(os.path.join(folder, "requirements.txt"), "w") as f:
            f.write(REQUIREMENTS)
        # Remove any stale inlined _engine tree from the previous architecture.
        engine = os.path.join(folder, "_engine")
        if os.path.isdir(engine):
            shutil.rmtree(engine)
        n += 1
        print(f"  wrote skills/{name}/{name}.py (thin client)")
    print(f"\nGenerated {n} thin skill clients.")


if __name__ == "__main__":
    main()
