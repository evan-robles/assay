# chemkit MCP server

One unified chemistry engine, exposed over the open **Model Context Protocol**
so any MCP-capable client (not just one vendor) can drive it. The engine lives
once in `chemkit_engine/`; each skill under `../skills/` is a thin client that
calls a tool here.

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
pip install chemkit-mcp            # core: server + xtb/mopac paths
pip install "chemkit-mcp[qm]"      # also pyscf + matplotlib (DFT/HF, plots)
# from a checkout instead:  pip install -e ".[qm]"

# External binaries are NOT pip-installable — install once:
conda install -c conda-forge xtb mopac openbabel

chemkit-mcp                        # start the stdio MCP server
```

## Wire it into any MCP client

The server speaks MCP over stdio and ships a `chemkit-mcp` console command, so
the **same path-free config works in every MCP host** — Claude Desktop, Cursor,
VS Code, custom agents:

```json
{ "mcpServers": { "chemkit": { "command": "chemkit-mcp" } } }
```

Or run it on demand with `uvx` (no install step):

```json
{ "mcpServers": { "chemkit": { "command": "uvx", "args": ["chemkit-mcp"] } } }
```

> Prerequisite: install the non-pip binaries once
> (`conda install -c conda-forge xtb mopac openbabel`). `--method dft`/`hf`
> additionally need the `[qm]` extra.

Then call e.g. the `single_point_energy` tool with
`{"args": ["--method", "xtb", "mol.xyz"]}`.

### OpenAI Agents SDK

The SDK speaks MCP natively — point `MCPServerStdio` at the same command:

```python
from agents import Agent
from agents.mcp import MCPServerStdio

async with MCPServerStdio(name="chemkit",
                          params={"command": "chemkit-mcp", "args": []}) as chemkit:
    agent = Agent(name="Chem assistant", mcp_servers=[chemkit], model="gpt-4o")
    # ... Runner.run(agent, "Build acetone and compute its HOMO/LUMO with xtb.")
```

Relative input/output paths resolve against the agent process's working
directory; the conda binaries must be installed first.

### Run from a checkout (no install)

The older path-based form still works if you don't want to install:

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

## Run a skill from the shell

The thin clients connect for you:

```bash
python ../skills/single_point_energy/single_point_energy.py --method xtb mol.xyz
```

Set `CHEMKIT_MCP=/abs/path/to/mcp_server/server.py` to pin a specific server.
