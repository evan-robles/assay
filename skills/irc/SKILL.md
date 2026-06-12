---
description: Intrinsic Reaction Coordinate (IRC) — When the user wants to confirm which reactant and product a transition state connects by walking down the gradient from the TS in both directions (e.g. "IRC", "intrinsic reaction coordinate", "trace the reaction path", "follow the gradient down from the TS", "what does this TS connect", "verify the reaction path"). Input must be a TS geometry (typically from /transition_state). Outputs two trajectory xyz files (forward + reverse) and endpoint energies.
---

# Intrinsic Reaction Coordinate

Walk down the gradient from a TS geometry in both directions along the
reaction-coordinate (imaginary-frequency) mode. Confirms which reactant
and product the saddle point connects.

MOPAC backend uses the native `IRC=1` keyword. The xtb backend uses a
simple Python steepest-descent on mass-weighted Cartesian coordinates,
seeded by the lowest-eigenvalue mode of the Eckart-projected Hessian.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path with a **TS geometry** (required — usually the output of /transition_state)
- `--method {xtb,mopac}` (required — if missing, AskUserQuestion)
- Optional: `--solvent <name>`, `--charge N`, `--mult N`,
  `--max-points N` (default 40), `--step <au>` (xtb only, default 0.05)

**Note**: `dft` and `hf` are NOT supported for IRC — the descent algorithm is xtb/mopac-specific and `chemkit irc --method dft` will error out with a clear message. To get a DFT-quality reaction path: run IRC with `--method xtb` or `--method mopac` first, then re-optimize each endpoint individually with `/geometry_optimize --method dft`.

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion (header "Method").
2. Run `chemkit irc --method <METHOD> [--solvent <S>] [--charge <Q>] [--mult <M>] <XYZ>`.
3. Read the printed JSON. Copy to `<basename>_irc_<method>.json` in the cwd. The CLI also writes `<basename>_irc_<method>_forward.xyz` and `..._reverse.xyz` trajectory files; copy them next to the user's input.
4. Report:
   - **Forward endpoint energy** and **reverse endpoint energy** (eV)
   - **Drops** from the TS in each direction (kcal/mol) — both should be negative if the TS is a real saddle.
   - **distinct_endpoints**: whether forward and reverse landed on different minima (true if energies differ by > 0.01 eV).
   - Paths to the two trajectory xyz files
   - Status messages from each direction
5. If `distinct_endpoints` is false, mention that both directions relaxed to the same minimum — usually means the input geometry was not a true TS, or the imaginary mode was very weak. Recommend re-running /transition_state with a different guess or running /vibrational_analysis to verify the input has exactly one imaginary mode.

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- mopac not in PATH → install via `conda install -c conda-forge mopac`.
- xtb path: if no imaginary mode is found at the input geometry, the steepest-descent will collapse to the input itself; flag this and recommend confirming the TS character with /vibrational_analysis first.

## Running this skill

The chemistry engine runs in the **chemkit MCP server**; this skill is a thin
client. Install the server once, then run the skill (it connects automatically):

```bash
pip install -r ../../mcp_server/requirements.txt   # one-time: the engine + server
python irc.py --help                            # full argument list
```

Or expose the server to any MCP-capable client — see `mcp_server/README.md`.
Set `CHEMKIT_MCP=/abs/path/to/mcp_server/server.py` to pin a specific server.
