---
description: Build 3D molecule from SMILES or name — When the user wants to convert a SMILES string OR a plain molecule name into a 3D .xyz geometry (e.g. "build this molecule", "make an xyz from SMILES", "build ethanol", "I only have a name", "generate 3D coordinates", "build acetone from CCC(=O)C"). Open Babel --gen3d; a bare name is resolved to SMILES online (PubChem → OPSIN → NIST) with the source reported. Optional QM refinement (--opt xtb/mopac/dft/hf). This is the on-ramp for every other chemkit skill — produces the .xyz that sp / opt / freq / etc. require.
---

# Build 3D Molecule from SMILES (or a Molecule Name)

Convert a SMILES string into a 3D `.xyz` via Open Babel's `--gen3d` 3D-coordinate
generator. Optionally hand off to xtb / MOPAC / DFT / HF for QM refinement so the
user can go from SMILES to publication-quality geometry in one command.

If the user gives only a **molecule name** (not a SMILES or an `.xyz`), the build
resolves the name to a SMILES online before building, trying reliable sources in
order and **reporting which one answered** (with an ACS-format citation):
1. **PubChem** (PUG REST) — name → CID → isomeric SMILES
2. **OPSIN** (EBI) — systematic IUPAC name → SMILES
3. **NIST WebBook** — name → InChI → SMILES (via Open Babel)

Detection is automatic: if Open Babel can parse the input as SMILES it is used
directly (no network call); otherwise it is treated as a name and resolved.

Under the hood the build writes the SMILES to a temporary `.smi` file, runs
`obabel <tmp>.smi --gen3d -O <out>.xyz`, then deletes the temporary `.smi`.

## Arguments
`$ARGUMENTS` should include:
- A SMILES string **or** a plain molecule name (required, positional)
- Optional:
  - `--out-xyz <path>` — destination .xyz (default: sanitized input + `.xyz` in cwd)
  - `--name <str>` — title comment for the xyz (default: the input string)
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
# Just build the xyz from a SMILES
chemkit build 'CCO'                          # ethanol → ethanol.xyz

# Build from a plain NAME — resolved online, source reported
chemkit build 'ethanol'                      # PubChem → CCO → ethanol.xyz
chemkit build 'caffeine' --opt xtb           # PubChem → SMILES → ..._xtb.xyz
chemkit build '2-aminopropanoic acid'        # OPSIN (systematic name) → SMILES

# Build + xtb refinement (most common pattern)
chemkit build 'O=C(O)c1ccccc1' --opt xtb     # benzoic acid → ..._xtb.xyz

# Anionic species — set the charge explicitly for the QM step
chemkit build 'CC(=O)[O-]' --opt xtb --charge -1   # acetate (-1) → ..._xtb.xyz

# DFT-quality starting structure for a downstream TS search
chemkit build 'CC=O' --opt dft --tier fast --out-xyz acetaldehyde.xyz
```

## Steps
1. Parse `$ARGUMENTS`. If the molecule (SMILES or name) is missing → stop and ask.
2. Run `chemkit build <SMILES-or-name> [--out-xyz ...] [--opt <M>] [...]`.
3. Read the JSON. Copy the xyz to the cwd if a custom `--out-xyz` wasn't supplied.
4. Report:
   - **Path to the xyz file** (the headline deliverable) and the **atom count**
   - **If the input was a name**: the resolved SMILES, **which source answered**
     (`smiles_source.source`), and the **ACS citation** (`smiles_source.citation`).
     Always surface the source so the provenance is clear.
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
- **Name resolution** is always attempted over the network when the input is not
  a SMILES. Sources are tried in order (PubChem → OPSIN → NIST WebBook) and the
  first hit wins; the chosen source + ACS citation land in `smiles_source`.
  PubChem and NIST are lookup databases (any common/trade name); OPSIN is a
  systematic-name parser (best for IUPAC names). NIST returns an InChI which is
  converted to SMILES via Open Babel, so stereochemistry is labeled
  `unspecified` for that source.
- Some short names are also valid SMILES (`C` = methane, `N` = ammonia). These
  resolve as SMILES — the right default for a structure builder. If you truly
  mean the element/ion by name, supply an explicit SMILES.

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- Open Babel missing → `conda install -c conda-forge openbabel`.
- "obabel failed to build 3D coordinates" → check the SMILES syntax; SMILES is
  case-sensitive, and most validators (e.g. https://www.daylight.com/smiles/)
  will catch typos. The error message includes obabel's stdout/stderr.
- "Could not resolve '<name>' to a SMILES from any reliable source" → the name
  was not found on PubChem, OPSIN, or NIST. Check spelling, try a systematic
  (IUPAC) name, supply a SMILES directly, or provide an `.xyz` file. Also check
  network connectivity — name resolution requires internet access.

## Running this skill

This skill is a single self-contained script. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python build_from_smiles.py --help                 # full argument list
```

The chemistry engine is inlined into `build_from_smiles.py`; no other files are required.
