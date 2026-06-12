---
description: Molecular Electrostatics (dipole + partial charges) — When the user wants the dipole moment, atomic partial charges (Mulliken), or general electrostatic-character information about a molecule (e.g. "dipole", "dipole moment", "partial charges", "Mulliken charges", "atomic charges", "electrostatic potential", "is this molecule polar"). Single-point — does NOT optimize the geometry first. Run /geometry_optimize beforehand if the geometry needs relaxation.
---

# Molecular Electrostatics

Compute dipole moment (magnitude + vector) and atomic partial charges (Mulliken
for every backend) on the supplied geometry, with optional implicit solvent.
No geometry optimization — pass an already-optimized xyz.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- `--method {xtb,mopac,dft,hf}` (required — if missing, use **AskUserQuestion**)
- Optional: `--solvent <name>` (water, methanol, dmso, mecn, dcm, ...),
  `--charge N`, `--mult N`
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion (header "Method", options `xtb` / `mopac` / `dft` / `hf`).
2. Run `chemkit electrostatics --method <METHOD> [--tier <T>] [--functional <F>] [--basis <B>] [--solvent <S>] [--charge <Q>] [--mult <M>] <XYZ>`.
3. Read the printed JSON. Copy to `<basename>_electrostatics_<method>.json` in the cwd.
4. Report:
   - **Dipole moment** in Debye (magnitude + Cartesian vector)
   - **Atomic partial charges** as a table: atom index (1-based), element symbol, charge
   - **Sum of charges** (sanity check — should match the total molecular charge)
   - Method, solvent (or "gas phase"), molecular charge/multiplicity
   - Mention the partitioning scheme used (Mulliken for every backend)
   - Note: Mulliken charges are basis-set-dependent and not a physical observable; for transferable charges use ESP-fit methods (not available in this build).

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- xtb-python missing → install via `conda install -c conda-forge xtb-python` or `pip install xtb`.
- mopac not in PATH → install via `conda install -c conda-forge mopac`.
- pyscf not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).

## Running this skill

This skill is a single self-contained script. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python electrostatics.py --help                 # full argument list
```

The chemistry engine is inlined into `electrostatics.py`; no other files are required.
