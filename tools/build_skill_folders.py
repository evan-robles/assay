#!/usr/bin/env python3
"""Generate the thin per-skill client scripts (conforming to skill-standards.md).

Architecture: the chemistry engine lives ONCE behind the MCP server
(`mcp_server/server.py` + `mcp_server/chemkit_engine/`). Each skill folder is:

  skills/<kebab-name>/
    SKILL.md                  - the skill doc (authored; left untouched here)
    scripts/<kebab-name>.py   - thin MCP client -> calls the skill's MCP tool
    requirements.txt          - client deps (the `mcp` SDK) + a server pointer
    examples/<...>/README.md  - validated example(s) (authored; not generated)

Skill (folder) names are kebab-case and equal to their MCP tool names (see
mcp_server/server.py::TOOLS). No chemistry engine is duplicated into the skills.

Run from the repo root:  python tools/build_skill_folders.py
"""
from __future__ import annotations

import os
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(REPO, "skills")

# Skill folders == MCP tool names (kebab-case). One entry per skill.
TOOLS = [
    "single-point-energy", "geometry-optimize", "vibrational-analysis",
    "binding-energy", "redox-potential", "conformer-search",
    "frontier-orbitals", "electrostatics", "solvation", "logp-partition",
    "reaction-profile", "pka-acidity", "build-from-smiles", "fukui-reactivity",
    "transition-state", "intrinsic-reaction-coordinate", "reaction-energy",
    "conformational-analysis", "visualize-orbitals",
]

# scripts/<name>.py sits 3 levels under the repo root
#   skills/<name>/scripts/<name>.py -> repo: ../../..
# and imports the shared client at skills/_mcp_client.py (../.. from scripts).
CLIENT_TEMPLATE = '''#!/usr/bin/env python3
"""Thin client for the `{name}` chemkit skill.

The chemistry engine runs in the chemkit MCP server (mcp_server/server.py); this
script forwards its arguments to the `{name}` MCP tool and prints the JSON
result. Set CHEMKIT_MCP to point at a specific server.py to use a custom server.

Usage:
    # Env: anl_env
    python skills/{name}/scripts/{name}.py [chemkit args]   # try --help

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
        scripts = os.path.join(folder, "scripts")
        os.makedirs(scripts, exist_ok=True)
        with open(os.path.join(scripts, f"{name}.py"), "w") as f:
            f.write(CLIENT_TEMPLATE.format(name=name))
        with open(os.path.join(folder, "requirements.txt"), "w") as f:
            f.write(REQUIREMENTS)
        # Remove stale root-level client + any old inlined engine tree
        # (handles both the legacy `_engine` name and the current `chemkit_engine`).
        for stale in (os.path.join(folder, f"{name}.py"),):
            if os.path.isfile(stale):
                os.remove(stale)
        for engine_name in ("_engine", "chemkit_engine"):
            engine = os.path.join(folder, engine_name)
            if os.path.isdir(engine):
                shutil.rmtree(engine)
        n += 1
        print(f"  wrote skills/{name}/scripts/{name}.py (thin client)")
    print(f"\nGenerated {n} thin skill clients.")


if __name__ == "__main__":
    main()
