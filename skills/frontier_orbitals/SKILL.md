---
description: Frontier Molecular Orbitals + HOMO-LUMO Gap — When the user wants the energies of frontier orbitals (HOMO, LUMO, HOMO-1, LUMO+1, ...), the HOMO-LUMO gap, or Koopmans-based reactivity descriptors at a fixed geometry (e.g. "HOMO", "LUMO", "frontier orbitals", "FMO", "HOMO-LUMO gap", "orbital energies", "Koopmans IP/EA", "chemical hardness", "electronegativity", "electrophilicity index"). Do NOT use for TD-DFT spectra or to optimize the geometry first — use `/geometry_optimize` beforehand if needed.
---

# Frontier Orbitals + HOMO-LUMO Gap

Compute HOMO, LUMO, the HOMO-LUMO gap, the K neighbouring frontier orbitals on each side
(HOMO-K..HOMO and LUMO..LUMO+K), and the standard Koopmans-based global reactivity
descriptors (vertical IP, vertical EA, electronegativity χ, hardness η, softness S,
electrophilicity index ω). Backends: GFN2-xTB (xtb-python), PM7 (MOPAC), DFT (PySCF
Kohn-Sham eigenvalues), HF (PySCF). Geometry is taken as-is — no optimization.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- `--method {xtb,mopac,dft,hf}` (required — if missing, use **AskUserQuestion**)
- Optional: `--solvent <name>` (water, methanol, dmso, mecn, dcm, ...),
  `--charge N`, `--mult N`
- Optional: `--nfrontier K` (default 3 — orbitals on each side of the gap)
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion (header "Method", options `xtb` / `mopac` / `dft` / `hf`).
2. Run `chemkit frontier --method <METHOD> [--tier <T>] [--functional <F>] [--basis <B>] [--solvent <S>] [--charge <Q>] [--mult <M>] [--nfrontier <K>] <XYZ>`.
3. Read the printed JSON. Copy the JSON result to `<basename>_frontier_<method>.json` in the cwd.
4. Report to the user:
   - **HOMO**, **LUMO**, **HOMO-LUMO gap** (eV)
   - The full **frontier table** (HOMO-K..HOMO, LUMO..LUMO+K) — render as a compact table sorted by energy
   - **Koopmans descriptors** from `koopmans`: vertical IP, vertical EA, χ, η, S, ω
   - Method, solvent (or "gas phase"), charge, multiplicity
   - Path to the JSON output
   - For MOPAC: also surface heat of formation and dipole from `code_specific`
   - Note: xtb and MOPAC orbital zeros differ — compare orbital energies only within the same method. Koopmans values are first-order estimates; for quantitative IP/EA use ΔSCF with DFT.

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- xtb / mopac not installed → install via `conda install -c conda-forge xtb mopac`.
- xtb-python missing (orbital eigenvalues require it for the xtb path) → install via `conda install -c conda-forge xtb-python` or `pip install xtb`.
- pyscf not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).
- Malformed `.xyz` → report which line failed.
- Open-shell systems: results are spin-restricted; flag `multiplicity > 1` in the report.

## Running this skill

The chemistry engine runs in the **chemkit MCP server**; this skill is a thin
client. Install the server once, then run the skill (it connects automatically):

```bash
pip install -r ../../mcp_server/requirements.txt   # one-time: the engine + server
python frontier_orbitals.py --help                            # full argument list
```

Or expose the server to any MCP-capable client — see `mcp_server/README.md`.
Set `CHEMKIT_MCP=/abs/path/to/mcp_server/server.py` to pin a specific server.
