---
description: Build 3D molecule from SMILES — When the user wants to convert a SMILES string into a 3D .xyz geometry (e.g. "build this molecule", "make an xyz from SMILES", "I only have a SMILES", "generate 3D coordinates", "build acetone from CCC(=O)C"). Open Babel --gen3d, with optional QM refinement (--opt xtb/mopac/dft/hf). This is the on-ramp for every other chemkit skill — produces the .xyz that sp / opt / freq / etc. require.
---

# Build 3D Molecule from SMILES

Convert a SMILES string into a 3D `.xyz` via Open Babel's `--gen3d` 3D-coordinate
generator. Optionally hand off to xtb / MOPAC / DFT / HF for QM refinement so the
user can go from SMILES to publication-quality geometry in one command.

Under the hood the build writes the SMILES to a temporary `.smi` file, runs
`obabel <tmp>.smi --gen3d -O <out>.xyz`, then deletes the temporary `.smi`.

## Arguments
`$ARGUMENTS` should include:
- A SMILES string (required, positional)
- Optional:
  - `--out-xyz <path>` — destination .xyz (default: sanitized SMILES + `.xyz` in cwd)
  - `--name <str>` — title comment for the xyz (default: the SMILES string)
  - `--opt {xtb,mopac,dft,hf}` — optional QM refinement after the obabel build. The
    QM-relaxed xyz becomes the canonical output (the obabel-only file is kept as
    `xyz_path_obabel` for transparency).
  - `--solvent <name>` — implicit solvent for the optional QM step
  - `--charge N` — net charge forwarded to the QM step (default 0). obabel does not
    infer charge here, so set this explicitly for ions (e.g. `-1` for a carboxylate).
  - `--mult N` — spin multiplicity forwarded to the QM step (default 1)
  - DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
  - HF-only: `--basis <name>`

## Examples
```bash
# Just build the xyz
chemkit build 'CCO'                          # ethanol → ethanol.xyz

# Build + xtb refinement (most common pattern)
chemkit build 'O=C(O)c1ccccc1' --opt xtb     # benzoic acid → ..._xtb.xyz

# Anionic species — set the charge explicitly for the QM step
chemkit build 'CC(=O)[O-]' --opt xtb --charge -1   # acetate (-1) → ..._xtb.xyz

# DFT-quality starting structure for a downstream TS search
chemkit build 'CC=O' --opt dft --tier fast --out-xyz acetaldehyde.xyz
```

## Steps
1. Parse `$ARGUMENTS`. If SMILES missing → stop and ask.
2. Run `chemkit build <SMILES> [--out-xyz ...] [--opt <M>] [--solvent ...] [...]`.
3. Read the JSON. Copy the xyz to the cwd if a custom `--out-xyz` wasn't supplied.
4. Report:
   - **Path to the xyz file** (the headline deliverable) and the **atom count**
   - If `--opt` was used, the QM energy and convergence flag, plus the charge /
     multiplicity that were used
   - The next-step recipe: "to compute X on this, run `/single_point_energy` /
     `/geometry_optimize` / etc. with this xyz"

## Recommendation
- Skip `--opt` if you're going to immediately run another skill that does its
  own optimization (`/geometry_optimize`, `/vibrational_analysis`).
- Use `--opt xtb` as the standard "good enough" QM clean-up — it's the cheapest
  way to fix the bond lengths and angles that a force field tends to
  mis-parameterize (e.g. carbonyl C=O, aromatic ring planarity for fused systems).
- `obabel --gen3d` builds a single conformer. For floppy molecules with many
  rotatable bonds, build once here then hand off to `/conformer_search` — CREST
  is the proper tool for stochastic conformer sampling.
- For ions and radicals, set `--charge` / `--mult` explicitly: obabel writes only
  the geometry, and the QM step needs the right electronic state.

## Notes
- The optional QM step uses chemkit's normal `opt` machinery — same convergence
  criteria, same backends, same caveats around basis sets / solvents.
- The result JSON records the exact `obabel` command that produced the geometry
  under `build.command` for reproducibility.

## Errors
- Open Babel missing → `conda install -c conda-forge openbabel`.
- "obabel failed to build 3D coordinates" → check the SMILES syntax; SMILES is
  case-sensitive, and most validators (e.g. https://www.daylight.com/smiles/)
  will catch typos. The error message includes obabel's stdout/stderr.
