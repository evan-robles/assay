# chem-skills / chemkit

ASE-based computational chemistry suite — xtb (GFN2) and MOPAC (PM7), with optional COSMO/ALPB solvation.

## Layout

One unified chemistry engine lives behind an **MCP server**; each skill is a
thin client that calls it over the open Model Context Protocol. The engine is
*not* duplicated into the skills — they're ~18-line wrappers.

```
~/chem-skills/
├── rules/
│   └── skill-standards.md         # the skill authoring standard (follow it!)
├── mcp_server/
│   ├── server.py          # MCP server (FastMCP, stdio) — exposes 19 tools
│   ├── _engine/           # the ONE chemistry engine (calculators, tasks, backends)
│   ├── requirements.txt   # engine + server deps
│   └── README.md          # how to wire the server into any MCP client
├── skills/
│   ├── _mcp_client.py                         # shared thin MCP client
│   ├── single-point-energy/                   # kebab-case skill name
│   │   ├── SKILL.md                           # the skill doc (frontmatter + sections)
│   │   ├── scripts/single-point-energy.py     # ~18-line thin client -> MCP tool
│   │   ├── requirements.txt                   # just the `mcp` client SDK
│   │   └── examples/<calc-name>/              # README.md + generated .json/.xyz/.png
│   └── (19 skill folders total)
├── tools/build_skill_folders.py   # regenerates the thin clients
└── tests/                         # regression suite (drives the thin clients)
```

Skill folders are **kebab-case** and conform to `rules/skill-standards.md`
(frontmatter with `name`/`description`/`category`, Goal/Instructions/Examples/
Constraints/References sections, a `scripts/` client, and a validated `examples/`
folder). The MCP server speaks the open protocol, so **any** MCP-capable client
can drive it (not just one vendor). See `mcp_server/README.md` for a generic
client config.

## Install & run

```bash
# 1. Install the server (the engine) once:
pip install -r mcp_server/requirements.txt
conda install -c conda-forge xtb mopac openbabel ase    # external binaries

# 2a. Run a skill from the shell (the thin client spawns/uses the server):
python skills/single-point-energy/scripts/single-point-energy.py --method xtb --solvent water mol.xyz
python skills/single-point-energy/scripts/single-point-energy.py --help

# 2b. Or run the MCP server directly and connect any MCP client:
python mcp_server/server.py
```

External binaries are NOT pip-installable — install separately (above):
`xtb` for `--method xtb`; `mopac` for `--method mopac` / PM7 post-opt;
`openbabel` provides `obabel`/`obenergy` for SMILES→3D, name lookup, and
conformer search. `pyscf` (in the server requirements) enables
`--method dft`/`--method hf`; `sella` enables transition-state searches on the
xtb/dft/hf backends (MOPAC has a native TS optimizer).

Set `CHEMKIT_MCP=/abs/path/to/mcp_server/server.py` to point the thin clients at
a specific server.

## Quick examples

```bash
python skills/single-point-energy/scripts/single-point-energy.py --method xtb --solvent water mol.xyz
python skills/geometry-optimize/scripts/geometry-optimize.py     --method mopac --charge 0 mol.xyz
python skills/vibrational-analysis/scripts/vibrational-analysis.py --method xtb --symmetry 2 mol_opt.xyz
python skills/binding-energy/scripts/binding-energy.py --method xtb --monomer A.xyz --monomer B.xyz complex.xyz
python skills/redox-potential/scripts/redox-potential.py --method xtb --ox-charge 0 --red-charge -1 --solvent water mol.xyz
python skills/conformer-search/scripts/conformer-search.py --method xtb mol.xyz
python skills/build-from-smiles/scripts/build-from-smiles.py ethanol   # name or SMILES → 3D xyz
```

All tasks write a single JSON file with a common header:
`{task, method, program, input_file, n_atoms, atoms, charge, multiplicity, solvent, cli_invocation, ...}`

## How the agentic skills work

Each skill folder pairs a runnable Python script with a `SKILL.md` that turns it
into something an agent can drive directly. The `SKILL.md` is a Markdown skill
file with YAML frontmatter so it shows up as a slash command
(`/single-point-energy`, `/geometry-optimize`, `/vibrational-analysis`,
`/binding-energy`, `/redox-potential`, `/conformer-search`,
`/conformational-analysis`, ...).

Each skill follows the same pipeline:

1. **Trigger** — the frontmatter `description:` is what the agent matches against
   the user's request (e.g. "binding energy", "what's the energy of this
   structure"); it also states what the skill should *not* be used for, to
   disambiguate from neighboring skills (e.g. `single-point-energy` vs. `geometry-optimize`).
2. **Parse arguments** — the skill spells out which flags `$ARGUMENTS` should
   contain (an `.xyz` path is always required) and which are optional
   (`--method`, `--solvent`, `--charge`, `--mult`, task-specific flags like
   `--postopt` for conformer search). If something required is missing, the
   skill tells the agent to stop and either ask directly or use
   **AskUserQuestion** (e.g. method selection for `single-point-energy`).
3. **Invoke the script** — the skill gives the literal
   `python <skill>.py ...` invocation to run as a subprocess.
4. **Read the JSON** — every skill prints one JSON result with the
   common header described above plus task-specific fields. The skill tells
   the agent to copy this to `<basename>_<task>_<method>.json` next to the
   user's input (and, for tasks that produce structures, to copy the
   accompanying `.xyz` files too) so results persist outside the tmp work
   directory.
5. **Report** — the skill enumerates exactly which fields to surface and how
   (units, which `code_specific` keys matter, caveats to mention — e.g. that
   xtb/MOPAC energy zeros aren't comparable, or that a single surviving
   conformer after post-opt is the converged answer, not a bug).

This keeps the heavy lifting (geometry I/O, calculator setup, parsing program
output into a stable schema) in the one engine behind the MCP server, while the
`SKILL.md` encodes the *judgment calls* — when to ask the user for
clarification, what's worth flagging as a caveat, and how to translate raw JSON
into something a chemist would actually want to read.

> Regenerating the thin clients: `tools/build_skill_folders.py` rewrites each
> `skills/<name>/scripts/<name>.py` as the standard ~18-line MCP client (and refreshes
> `requirements.txt`). The engine and tool list live in `mcp_server/`.

## Notes / caveats

- **PM7 transition-metal parameters are spotty** — the schema flags this in `warnings` when relevant.
- **Redox potentials and conformer search are screening-grade**, not publication-grade. The skill output warns about this.
