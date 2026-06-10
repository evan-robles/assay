---
description: Conformer Search — When the user wants to sample/find the low-energy conformers of a flexible molecule (e.g. "find conformers", "conformer search", "conformational ensemble", "lowest-energy conformer", "CREST", "what are the stable conformations of this molecule"). Uses CREST stochastic sampling. Do NOT use for a deterministic torsional energy profile around a specific bond — that's conformational_analysis.
---

# Conformer Search

Find low-energy conformers of a flexible molecule using CREST (built on GFN2-xTB),
optionally re-ranked at PM7 (MOPAC) to resolve minima that GFN2 smooths over.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- Optional:
  - `--solvent <name>` — implicit ALPB solvent (water, methanol, dmso, …)
  - `--charge N`, `--mult N`
  - `--max-conformers N` (default 20)
  - `--postopt {none,mopac}` (default `mopac`) — re-optimize the CREST ensemble at PM7. Pass `none` to skip.
  - `--postopt-rmsd <Å>` (default 0.25) — RMSD threshold for deduping post-opt structures
  - `--postopt-ewin <kcal/mol>` (default 6.0) — energy window kept after post-opt
- Method is always `xtb` here. CREST is built on GFN2-xTB; there is no DFT/HF/MOPAC
  CREST backend. `--postopt mopac` is a separate re-optimization step on top of
  CREST's xtb ensemble.
- **For DFT-quality conformers**: run this skill with the xtb + mopac post-opt
  pipeline, then re-optimize the top-K conformers individually with
  `/geometry_optimize --method dft --tier standard`.

## When to use `--postopt mopac`
GFN2-xTB underestimates rotational well depths for short alkanes and other
flexible aliphatics; conformers that exist experimentally collapse to a single
minimum during CREST's CREGEN filtering. Re-optimizing the MTD ensemble with
PM7 typically recovers them with reasonable HoF spacing (~0.5–1.5 kcal/mol per
gauche substitution). Use it when CREST returns suspiciously few conformers for
a molecule you expect to be flexible.

When CREST returns only one conformer, the post-opt step generates seeds by
rotating each non-methyl single C-C bond through {gauche+, anti, gauche-} and
also samples the metadynamics trajectory. Each seed is jittered slightly to
break input symmetry, optimized at PM7, and rejected if any backbone dihedral
remains at an eclipsed (saddle) position. Final conformers are deduped by
heavy-atom Kabsch RMSD.

## Ring puckering (automatic)
For any non-aromatic ring of size 4–8 detected in the input, the seed pool
also includes canonical puckered geometries built from Cremer–Pople (CP)
puckering coordinates:
- 4-ring: planar + butterfly puckers
- 5-ring: envelope (E1..E5) + twist (T1..T5)
- 6-ring: chair, inverted chair, 4× twist-boat at equator phases
- 7-ring: chair / twist-chair (q₃-dominant) + boat / twist-boat (q₂-dominant)
- 8-ring: crown, boat-chair, twist-boat-chair, etc.

Each CP seed is built by displacing ring atoms along the local ring normal
to match the target pucker, then constrained-relaxed at GFN2-xTB with the
ring dihedrals frozen so substituent H positions settle into the pucker. The
relaxed seeds are then fed through the standard PM7 post-opt + RMSD dedup
pipeline. This is what lets cyclohexane recover its twist-boat conformer
(~3–5 kcal/mol above chair) — CREST's MTD never visits it because the chair
basin is so wide on the GFN2 PES.

If CREST fails to produce an ensemble (some flexible rings trip its internal
preopt), ring-pucker seeds still get optimized at PM7 and reported.

## Steps
1. Parse args. Stop and ask if `.xyz` missing.
2. Run `chemkit confsearch --method xtb [--solvent <S>] [--max-conformers <N>] [--postopt mopac] <XYZ>`.
3. Read JSON, copy to `<basename>_confsearch.json`. The CLI also writes a
   `<basename>_conformers.xyz` next to the JSON containing all unique post-opt
   conformers (or the CREST ensemble if `--postopt none`); copy that next to
   the user's input too.
4. Report:
   - CREST result: `n_conformers_found` / `n_conformers_kept`, relative energies (kcal/mol)
   - Paths to `crest_best.xyz` and `crest_conformers.xyz`
   - If `postopt` block is present, additionally report:
     - `postopt.method`, `n_input`, `n_converged`, `n_unique`, `n_failed`
     - Each conformer's `rel_hof_kcal_mol`, `degeneracy`, and `xyz_path`
     - `seed_source` — origin of seeds, e.g. `crest_best + ring_pucker (6) + crest_dynamics.trj (48 frames)`
5. If only one conformer survives both stages and the molecule has rotatable
   bonds, mention that this is the converged answer at PM7 (not a bug).

## Notes
- CREST must be installed (`conda install -c conda-forge crest`).
- For rigid molecules the search may return only one or two conformers — expected.
- Output XYZ files live in a tmp work directory (`work_directory` in the JSON);
  if the user wants to keep them, copy next to the input file.
