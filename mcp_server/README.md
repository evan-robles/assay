# ASSAY MCP server

Exposes the 20 ASSAY chemistry skills over the open **Model Context Protocol**,
so any MCP-capable client can drive them. The server discovers the skills on disk
and runs each skill's `scripts/run.py` directly; the shared physics lives once in
`assay_core/`.

## What it exposes

One MCP tool per skill (kebab-case, matching the skill's display name):
`single-point-energy`, `geometry-optimize`, `vibrational-analysis`,
`binding-energy`, `redox-potential`, `conformer-search`, `frontier-orbitals`,
`electrostatics`, `solvation`, `logp-partition`, `reaction-profile`,
`pka-acidity`, `build-from-smiles`, `name-to-smiles`, `fukui-reactivity`,
`transition-state`, `intrinsic-reaction-coordinate`, `reaction-energy`,
`conformational-analysis`, `visualize-orbitals`.

Each tool advertises its own typed parameters (the exact flags that skill takes,
introspected from its `build_parser()`), plus:

- `cwd` — directory for resolving relative input/output paths.
- `run_on` — `local` (default) or `aurora` (run this call on a remote compute
  node over ssh; see the main README's Aurora section).
- `args` — a raw CLI token list, accepted for back-compat.

The tool returns the result JSON. Each call runs in an isolated subprocess so
stateful QM jobs don't leak across calls.

## Install & run

```bash
conda install -c conda-forge xtb xtb-python mopac openbabel rdkit   # non-pip backends
pip install chemkit-mcp        # or, from a checkout: pip install -e .

assay-mcp                      # start the stdio MCP server
```

`--method dft`/`hf` use PySCF, installed by pip with everything else.

## Wire it into any MCP client

The server speaks MCP over stdio and ships an `assay-mcp` console command, so the
same path-free config works in every host (Claude Desktop, Cursor, VS Code,
custom agents):

```json
{ "mcpServers": { "assay": { "command": "assay-mcp" } } }
```

Or run it on demand with `uvx`:

```json
{ "mcpServers": { "assay": { "command": "uvx", "args": ["chemkit-mcp"] } } }
```

### OpenAI Agents SDK

```python
from agents import Agent
from agents.mcp import MCPServerStdio

async with MCPServerStdio(name="assay",
                          params={"command": "assay-mcp", "args": []}) as assay:
    agent = Agent(name="Chem assistant", mcp_servers=[assay], model="gpt-4o")
    # ... Runner.run(agent, "Build acetone and compute its HOMO/LUMO with xtb.")
```

### Run from a checkout (no install)

```json
{ "mcpServers": { "assay": {
    "command": "python",
    "args": ["/abs/path/to/chem-skills/mcp_server/server.py"] } } }
```

## Run from the shell

Two console commands (`chemkit`/`chemkit-mcp` are aliases):

- **`assay`** — human CLI, runs one calculation by dispatching to the skill:
  ```bash
  assay sp --method xtb mol.xyz
  assay redox --method dft --tier standard --ox-charge 0 --red-charge -1 mol.xyz
  assay sp --help          # per-subcommand arguments
  assay --list-skills      # list skills
  ```
  Every path (this CLI, an MCP tool, or the skill's `run.py` directly) runs the
  same skill through the same guardrails: the level-of-theory gate, the integrity
  gate, and the live `.out` log.

- **`assay-mcp`** — starts the stdio server for agents to connect to.

## DFT/HF defaults: density fitting is OFF

By default `--method dft`/`hf` use **exact four-center integrals** (`RKS`/`UKS`/
`RHF`/`UHF`), matching a hand-written PySCF run — no silent density-fitting (RI)
approximation.

Pass **`--density-fit`** to opt into RI: ~3–10× faster SCF for a typically
negligible ~0.1–0.8 mEh error (it largely cancels in energy *differences*). The
auxiliary basis is chosen automatically and the treatment is reported in the
result JSON.

```bash
assay sp --method dft --tier standard mol.xyz                # exact (default)
assay sp --method dft --tier standard --density-fit mol.xyz  # RI, faster
```
