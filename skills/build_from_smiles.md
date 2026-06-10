---
description: Build 3D molecule from SMILES — When the user wants to convert a SMILES string into a 3D .xyz geometry (e.g. "build this molecule", "make an xyz from SMILES", "I only have a SMILES", "generate 3D coordinates", "build acetone from CCC(=O)C"). RDKit ETKDG embedding + MMFF/UFF cleanup, with optional QM refinement (--opt xtb/mopac/dft/hf). This is the on-ramp for every other chemkit skill — produces the .xyz that sp / opt / freq / etc. require.
---

# Build 3D Molecule from SMILES

Convert a SMILES string into a 3D `.xyz` via RDKit's ETKDGv3 embedding + force-field
cleanup (MMFF94 by default). Optionally hand off to xtb / MOPAC / DFT / HF for QM
refinement so the user can go from SMILES to publication-quality geometry in one
command.

## Arguments
`$ARGUMENTS` should include:
- A SMILES string (required, positional)
- Optional:
  - `--out-xyz <path>` — destination .xyz (default: sanitized SMILES + `.xyz` in cwd)
  - `--name <str>` — title comment for the xyz (default: canonical SMILES)
  - `--n-confs N` (default 5) — ETKDG conformers to embed; lowest FF energy wins
  - `--forcefield {mmff,uff}` (default `mmff`) — UFF if MMFF lacks parameters
    (rare for organics, common for transition metals); the task falls back to UFF
    silently per-conformer when MMFF has no params
  - `--seed N` (default `0xC0FFEE`) — ETKDG random seed (reproducible embedding)
  - `--opt {xtb,mopac,dft,hf}` — optional QM refinement after FF cleanup. The
    QM-relaxed xyz becomes the canonical output (the FF-only file is kept as
    `xyz_path_ff` for transparency).
  - `--solvent <name>` — implicit solvent for the optional QM step
  - `--charge N` — override the charge inferred from SMILES (charge is normally
    inferred from formal charges in the SMILES: `[NH4+]` → +1, `[O-]C=O` → -1)
  - `--mult N` — override multiplicity (normally inferred from radical electron count)
  - DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
  - HF-only: `--basis <name>`

## Examples
```bash
# Just build the xyz
chemkit build 'CCO'                          # ethanol → ethanol.xyz

# Build + xtb refinement (most common pattern)
chemkit build 'O=C(O)c1ccccc1' --opt xtb     # benzoic acid → ..._xtb.xyz

# Anionic species — charge inferred from SMILES
chemkit build 'CC(=O)[O-]' --opt xtb         # acetate (-1) → ..._xtb.xyz

# DFT-quality starting structure for a downstream TS search
chemkit build 'CC=O' --opt dft --tier fast --out-xyz acetaldehyde.xyz
```

## Steps
1. Parse `$ARGUMENTS`. If SMILES missing → stop and ask.
2. Run `chemkit build <SMILES> [--out-xyz ...] [--opt <M>] [--solvent ...] [...]`.
3. Read the JSON. Copy the xyz to the cwd if a custom `--out-xyz` wasn't supplied.
4. Report:
   - **Canonical SMILES** (canonicalized by RDKit) and **molecular formula**
   - **Inferred charge** and **multiplicity** — flag if the user might have wanted
     a different charge state (e.g. carboxylic acid SMILES vs. carboxylate)
   - **Path to the xyz file** (the headline deliverable)
   - FF energy of the selected conformer; if `--opt` was used, also the QM
     energy and convergence flag
   - The next-step recipe: "to compute X on this, run `/single_point_energy` /
     `/geometry_optimize` / etc. with this xyz"

## Recommendation
- Default `--n-confs 5` is enough for rigid molecules; bump to 20–50 for
  flexible ones if you want a representative starting structure.
- Skip `--opt` if you're going to immediately run another skill that does its
  own optimization (`/geometry_optimize`, `/vibrational_analysis`).
- Use `--opt xtb` as the standard "good enough" QM clean-up — it's the cheapest
  way to fix the bond lengths and angles that MMFF tends to mis-parameterize
  (e.g. carbonyl C=O, aromatic ring planarity for fused systems).
- For floppy molecules with many rotatable bonds, build a single conformer then
  hand off to `/conformer_search` rather than asking ETKDG for many conformers
  here — CREST is the proper tool for that.

## Notes
- RDKit's ETKDGv3 (Riniker–Landrum 2015) uses knowledge-based torsion
  preferences. It often produces a chemically reasonable conformer on the
  first try, but for very flexible molecules (chain length > 12, peptides,
  macrocycles) you should use `/conformer_search` instead.
- The inferred charge/multiplicity come from the SMILES — `[O-]` adds -1,
  `[NH+]` adds +1, `[O]` (an unbound oxygen radical) bumps multiplicity to 2.
  If the user wants a specific protonation state, write the SMILES that way.
- The optional QM step uses chemkit's normal `opt` machinery — same convergence
  criteria, same backends, same caveats around basis sets / solvents.

## Errors
- RDKit missing → `conda install -c conda-forge rdkit` or `pip install rdkit`.
- "RDKit could not parse SMILES" → check syntax; SMILES is case-sensitive,
  and most validators (e.g. https://www.daylight.com/smiles/) will catch typos.
- "ETKDG failed to embed any conformers" → try a larger `--n-confs`, a different
  `--seed`, or supply a hand-built starting structure.
