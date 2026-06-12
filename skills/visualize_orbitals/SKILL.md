---
description: Visualize Molecular Orbitals — When the user wants to SEE an orbital (HOMO, LUMO, frontier, specific MO), generate a molden / cube file, or otherwise produce viewable orbital wavefunctions (e.g. "show me the HOMO", "plot the LUMO", "visualize this orbital", "MO isosurface", "molden file", "cube file", "Avogadro", "Jmol", "what does the HOMO look like", "draw the orbitals"). Writes `.molden` (always) + optional `.cube` files for selected orbitals — no rendering. The user opens the files in their viewer of choice. Do NOT use for orbital energies only (use `/frontier_orbitals`) or for electrostatic potential / partial charges (use `/electrostatics`).
---

# Visualize Molecular Orbitals

Dump the wavefunction in formats every modern viewer reads. `.molden` is always written (~10–100 KB, holds the full SCF). On request, `.cube` files for specific orbitals (HOMO, LUMO, HOMO-1, etc.) are evaluated on a 3D grid.

The skill does NOT render the orbitals itself — every viewer (Avogadro, Jmol, IboView, VMD, Multiwfn) does a much better job, and ships with proper lighting, isosurface controls, and rotation. This keeps the skill fast (molden takes ~no time; cubes only on demand) and dependency-light.

**Backends.** xtb uses `xtb --molden`; PySCF (dft/hf) uses `pyscf.tools.molden.from_scf`; MOPAC uses `GRAPHF` and we synthesize a molden by re-fitting each PM7 STO as STO-3G Gaussians so the cube path works. MOPAC shapes are qualitatively right but absolute amplitudes differ from a native PM7 plot.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- `--method {xtb,mopac,dft,hf}` (required — if missing, use **AskUserQuestion**)
- Optional: `--solvent <name>` (water, methanol, dmso, mecn, dcm, ...),
  `--charge N`, `--mult N`
- Optional: `--cubes <list>` — comma-separated orbital labels to render as `.cube` files. Each label is one of:
    - `homo`, `lumo`, `homo-1`, `lumo+2`, …
    - a 1-based MO index (e.g. `5`)
    - optionally suffix `:alpha` or `:beta` for open-shell (`homo:alpha`)
  Default empty — only the molden is written.
- Optional: `--grid N` — cube resolution (default 80; 50 = quick preview, 120 = publication)
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion (header "Method", options `xtb` / `mopac` / `dft` / `hf`).
2. If the user asked to "see the HOMO" / "plot the LUMO" / etc. but didn't specify cubes, default `--cubes homo,lumo`.
3. Run `chemkit orbitals --method <METHOD> [--cubes <list>] [--grid <N>] [...usual flags] <XYZ>`.
4. Read the printed JSON. Copy the JSON result to `<basename>_orbitals_<method>.json` in the cwd.
5. Report to the user:
   - Path to the `.molden` file (always present) and the `.cube` files (one per requested orbital)
   - MO summary from `mo_summary`: HOMO / LUMO indices and energies (eV); for open-shell, alpha + beta separately
   - Method, solvent (or "gas phase"), charge, multiplicity
   - For MOPAC: the warning about STO-3G re-fitting
   - **How to view the files** — one short line: "Open `<name>.molden` in Avogadro / Jmol / IboView. Open `.cube` files in VMD / PyMOL / Avogadro to see the isosurface."

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Don't editorialize about whether the orbital "looks like" textbook expectations unless the user asks.

## Errors
- xtb / mopac not installed → install via `conda install -c conda-forge xtb mopac`.
- pyscf not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).
- xtb refused to write `molden.input` → usually an unsupported element; report the stderr tail.
- Cube generation slow → suggest `--grid 50` for previews.
- Malformed `.xyz` → report which line failed.

## Running this skill

The chemistry engine runs in the **chemkit MCP server**; this skill is a thin
client. Install the server once, then run the skill (it connects automatically):

```bash
pip install -r ../../mcp_server/requirements.txt   # one-time: the engine + server
python visualize_orbitals.py --help                            # full argument list
```

Or expose the server to any MCP-capable client — see `mcp_server/README.md`.
Set `CHEMKIT_MCP=/abs/path/to/mcp_server/server.py` to pin a specific server.
