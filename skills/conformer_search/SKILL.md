---
description: Conformer Search — When the user wants to sample/find the low-energy conformers of a flexible molecule (e.g. "find conformers", "conformer search", "conformational ensemble", "lowest-energy conformer", "what are the stable conformations of this molecule"). Uses Open Babel's confab diverse-conformer generator. Do NOT use for a deterministic torsional energy profile around a specific bond — that's conformational_analysis.
---

# Conformer Search

Find low-energy conformers of a flexible molecule using Open Babel's `confab`
diverse-conformer generator (force-field based), ranked by force-field energy
(`obenergy`, MMFF94 with UFF fallback), and optionally re-ranked at PM7 (MOPAC)
to resolve minima that force-field sampling smooths over.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- Optional:
  - `--solvent <name>` — implicit ALPB solvent (water, methanol, dmso, …)
  - `--charge N`, `--mult N`
  - `--max-conformers N` (default 20)
  - `--postopt {none,mopac}` (default `mopac`) — re-optimize the obabel ensemble at PM7. Pass `none` to skip.
  - `--postopt-rmsd <Å>` (default 0.25) — RMSD threshold for deduping post-opt structures (also the confab diversity cutoff)
  - `--postopt-ewin <kcal/mol>` (default 6.0) — energy window kept after post-opt
- Method is always `xtb` here — that is the canonical token kept for CLI
  uniformity; the actual sampler is Open Babel confab (force-field). There is no
  DFT/HF backend for the sampling step. `--postopt mopac` is a separate PM7
  re-optimization step on top of the obabel ensemble.
- **For DFT-quality conformers**: run this skill with the obabel + mopac post-opt
  pipeline, then re-optimize the top-K conformers individually with
  `/geometry_optimize --method dft --tier standard`.

## When to use `--postopt mopac`
Force-field sampling underestimates rotational well depths for short alkanes and
other flexible aliphatics; distinct minima can collapse together during
generation. Re-optimizing the ensemble with PM7 typically recovers them with
reasonable HoF spacing (~0.5–1.5 kcal/mol per gauche substitution). Use it when
the obabel search returns suspiciously few conformers for a molecule you expect
to be flexible.

When the obabel search returns only one conformer, the post-opt step generates
seeds by rotating each non-methyl single C-C bond through {gauche+, anti,
gauche-}. Each seed is jittered slightly to break input symmetry, optimized at
PM7, and rejected if any backbone dihedral remains at an eclipsed (saddle)
position. Final conformers are deduped by heavy-atom Kabsch RMSD.

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
(~3–5 kcal/mol above chair) — force-field sampling never visits it because the
chair basin is so wide.

If the obabel search returns only one conformer (some flexible rings sample
poorly), ring-pucker seeds still get optimized at PM7 and reported.

## Steps
1. Parse args. Stop and ask if `.xyz` missing.
2. Run `chemkit confsearch --method xtb [--solvent <S>] [--max-conformers <N>] [--postopt mopac] <XYZ>`.
3. Read JSON, copy to `<basename>_confsearch.json`. The CLI also writes a
   `<basename>_conformers.xyz` next to the JSON containing all unique post-opt
   conformers (or the obabel ensemble if `--postopt none`); copy that next to
   the user's input too.
4. Report:
   - Sampler result: `n_conformers_found` / `n_conformers_kept`, relative force-field energies (kcal/mol)
   - Paths to `best_conformer_xyz` and `all_conformers_xyz`
   - If `postopt` block is present, additionally report:
     - `postopt.method`, `n_input`, `n_converged`, `n_unique`, `n_failed`
     - Each conformer's `rel_hof_kcal_mol`, `degeneracy`, and `xyz_path`
     - `seed_source` — origin of seeds, e.g. `obabel_best + ring_pucker (6) + dihedral_grid (12)`
5. If only one conformer survives both stages and the molecule has rotatable
   bonds, mention that this is the converged answer at PM7 (not a bug).

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Notes
- Open Babel must be installed (`conda install -c conda-forge openbabel`); it
  provides both `obabel` (confab sampling) and `obenergy` (force-field ranking).
- `--postopt mopac` (the default) additionally requires MOPAC for the PM7 step.
- The sampler is force-field only, so raw conformer quality is lower than a
  GFN2-xTB metadynamics search; the PM7 post-opt + ring-pucker / dihedral-grid
  seeding is what recovers quality. For best results keep `--postopt mopac` on.
- For rigid molecules the search may return only one or two conformers — expected.
- Output XYZ files live in a tmp work directory (`work_directory` in the JSON);
  if the user wants to keep them, copy next to the input file.

## Running this skill

This skill is a single self-contained script. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python conformer_search.py --help                 # full argument list
```

The chemistry engine is inlined into `conformer_search.py`; no other files are required.

### In-terminal 3D view (asciimol)

When run on an interactive terminal with `asciimol` installed, this skill opens
the resulting geometry in an ASCII 3D viewer automatically (press `q` to quit).
Pass `--no-view` to disable it. The viewer never launches in non-interactive
runs (pipes, tests, agent automation), so it is safe to script.

```bash
pip install asciimol     # one-time: enables the in-terminal viewer
```
