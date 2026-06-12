# chemkit MCP server

One unified chemistry engine, exposed over the open **Model Context Protocol**
so any MCP-capable client (not just one vendor) can drive it. The engine lives
once in `_engine/`; each skill under `../skills/` is a thin client that calls a
tool here.

## What it exposes

One MCP tool per skill (19 total): `single_point_energy`, `geometry_optimize`,
`vibrational_analysis`, `binding_energy`, `redox_potential`, `conformer_search`,
`frontier_orbitals`, `electrostatics`, `solvation`, `logp`, `reaction_profile`,
`pka`, `build_from_smiles`, `fukui`, `transition_state`, `irc`,
`reaction_energy`, `conformational_analysis`, `visualize_orbitals`.

Each tool takes:
- `args`: the chemkit CLI tokens for that task, e.g.
  `["--method", "xtb", "mol.xyz", "--out", "mol_sp.json"]`. Use `["--help"]` to
  list a task's arguments.
- `cwd` (optional): the directory to resolve relative input/output paths against
  (the caller's working directory). The thin clients set this automatically.

The tool returns the engine's result JSON. Each call runs the engine in an
isolated subprocess so long, stateful QM jobs don't leak across calls.

## Install & run

```bash
pip install -r requirements.txt
# plus external binaries: conda install -c conda-forge xtb mopac openbabel ase
python server.py            # stdio MCP server
```

## Wire it into any MCP client

The server speaks MCP over stdio. A generic client config (works with any host
that supports MCP servers — Claude, IDEs, custom agents, etc.):

```json
{
  "mcpServers": {
    "chemkit": {
      "command": "python",
      "args": ["/abs/path/to/chem-skills/mcp_server/server.py"]
    }
  }
}
```

Then call e.g. the `single_point_energy` tool with
`{"args": ["--method", "xtb", "mol.xyz"]}`.

## Run a skill from the shell

The thin clients connect for you:

```bash
python ../skills/single_point_energy/single_point_energy.py --method xtb mol.xyz
```

Set `CHEMKIT_MCP=/abs/path/to/mcp_server/server.py` to pin a specific server.
