---
description: Solvation Free Energy (ΔG_solv) — When the user wants the electronic solvation free energy of a molecule in a given solvent (e.g. "ΔG_solv", "solvation energy", "free energy of solvation", "solvation free energy in water/DMSO", "hydration free energy"). Single-point — does NOT optimize. Run /geometry_optimize first if the geometry needs relaxation. For octanol/water partition specifically, use /logp instead.
---

# Solvation Free Energy (ΔG_solv)

Estimate ΔG_solv = E(solvated) − E(gas) using implicit solvation on the supplied geometry. Electronic only — no cavitation, dispersion-repulsion, or thermal corrections. Screening-grade at semi-empirical accuracy (±2–3 kcal/mol typical).

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- `--method {xtb,mopac,dft,hf}` (required — if missing, **AskUserQuestion**)
- A `--solvent <name>` (required — water, methanol, ethanol, acetone, mecn, dmso, thf, dcm, chloroform, toluene, benzene, hexane, ether, octanol)
- Optional: `--charge N`, `--mult N`
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

DFT with `--tier standard` and an implicit solvent gives meaningfully better ΔG_solv than semi-empirical (~±1 kcal/mol vs ±2–3 for xtb/mopac) at much higher cost. The DFT path uses ddCOSMO; for true "research-grade" SMD parameterization you'd need PySCF's `pyscf.solvent.smd` directly.

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion. If solvent missing → stop and ask.
2. Run `chemkit solvation --method <M> --solvent <S> [--charge <Q>] [--mult <M>] <XYZ>`.
3. Read the JSON. Copy to `<basename>_solvation_<solvent>_<method>.json` in the cwd.
4. Report:
   - **ΔG_solv** in kcal/mol (primary) and eV
   - E(gas) and E(solvated) for context
   - Method, solvent, charge/multiplicity
   - Caveats: electronic-only; ±2–3 kcal/mol at semi-empirical; no cavity term.
   - Flag any warnings in the JSON (especially the |ΔG_solv| ≈ 0 silent-drop warning).

## Recommendation
For tighter numbers, run `/geometry_optimize` separately in gas phase and in solvent and compute ΔG_solv from those (chemkit uses ONE geometry for both — quick but ignores geometry relaxation in solvent). For research-grade values, use DFT with a continuum model that includes non-electrostatic terms (e.g. SMD).

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- xtb-python / MOPAC missing → install via `conda install -c conda-forge xtb-python mopac`.
- pyscf not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).
- Unknown solvent → check the list above; `--solvent` is matched case-insensitively.

## Running this skill

The chemistry engine runs in the **chemkit MCP server**; this skill is a thin
client. Install the server once, then run the skill (it connects automatically):

```bash
pip install -r ../../mcp_server/requirements.txt   # one-time: the engine + server
python solvation.py --help                            # full argument list
```

Or expose the server to any MCP-capable client — see `mcp_server/README.md`.
Set `CHEMKIT_MCP=/abs/path/to/mcp_server/server.py` to pin a specific server.
