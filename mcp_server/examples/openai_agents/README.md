# Drive chemkit from an OpenAI model (OpenAI Agents SDK)

A minimal, runnable example showing that a **non-Claude** agent can use all 19
chemkit tools with no per-tool glue code. The OpenAI Agents SDK speaks MCP
natively, so it just launches the `chemkit-mcp` stdio server and exposes its
tools to the model.

## Setup

```bash
# 1. chemkit itself (provides the `chemkit-mcp` command)
pip install -e ".[qm]"                 # from the repo root
#   or, once published:  pip install "chemkit-mcp[qm]"

# 2. external chemistry binaries (NOT pip-installable)
conda install -c conda-forge xtb mopac openbabel

# 3. the OpenAI Agents SDK + your key
pip install openai-agents
export OPENAI_API_KEY=sk-...           # your key — never commit it
```

## Run

```bash
python run_chemkit_agent.py
# or a custom task:
python run_chemkit_agent.py "Optimize water with xtb, then compute its dipole."
```

Expected: the script prints `chemkit exposed 19 tools to the model.`, the model
picks `build-from-smiles` + `frontier-orbitals`, chemkit runs a real GFN2-xTB
calculation, and the model reports the actual HOMO/LUMO energies (≈ −10.84 eV /
−6.92 eV, gap ≈ 3.92 eV for acetone) — not guessed numbers.

## How it works

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

async with MCPServerStdio(name="chemkit",
                          params={"command": "chemkit-mcp", "args": []}) as chemkit:
    agent = Agent(name="Chem assistant", mcp_servers=[chemkit], model="gpt-4o")
    result = await Runner.run(agent, "Build acetone and compute its HOMO/LUMO with xtb.")
```

The same `{"command": "chemkit-mcp"}` launch spec works in every MCP host
(Claude Desktop, Cursor, VS Code, custom agents) — this example just wires it to
an OpenAI model. Override the model with `CHEMKIT_OPENAI_MODEL=gpt-4o-mini`.

## Notes

- Relative input/output paths in a tool call resolve against the **agent
  process's** working directory.
- chemkit calls can be slow (real QM); the example sets a 600 s MCP client
  timeout.
- `--method dft`/`hf` need the `[qm]` extra (pyscf); `xtb`/`mopac` need their
  conda binaries.
