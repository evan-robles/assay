---
name: conformer-search
description: Samples the low-energy conformers of a flexible molecule and returns a ranked conformational ensemble.
category: chemistry
---

# Conformer Search

> [!IMPORTANT]
> **Before running — confirm the level of theory; never guess.** If the user did
> not specify `--method` (xtb | mopac | dft | hf) — and, where relevant,
> `--functional`/`--basis`/`--tier`, `--solvent` (or explicit gas phase),
> `--charge`, `--mult` — **stop and ask the user** (do not silently default or
> carry over the previous run's choice). The engine refuses a DFT/HF run that
> omits the consequential knobs unless you pass `--accept-defaults`.
> **At launch, immediately give the user the live `.out` log path and offer
> `tail -f`** — do not wait for the run to finish. (calculation-reporting-standards
> non-negotiables #10 and #9.)

## Goal
Find the low-energy conformers of a flexible molecule using Open Babel's `confab` diverse-conformer generator (force-field sampling), rank them by force-field energy (`obenergy`, MMFF94 with UFF fallback), and optionally re-rank at PM7 (MOPAC) to resolve minima that force-field sampling smooths over. Use this for a conformational ensemble, not for a deterministic torsional scan around one bond.

## Instructions
1. Parse arguments. Stop and ask if the `.xyz` path is missing.
2. Run the engine. The `--method` token is always `xtb` for CLI uniformity; the actual sampler is Open Babel confab (force-field). `--postopt mopac` is a separate PM7 re-optimization on top of the obabel ensemble.

```bash
# Env: anl_env
python skills/conformer-search/scripts/conformer-search.py --method xtb [--solvent <name>] [--charge N] [--mult N] [--max-conformers N] [--postopt {none,mopac}] [--postopt-rmsd <Å>] [--postopt-ewin <kcal/mol>] [--out <path>] input.xyz
```

Arguments:
- `input.xyz` — molecular geometry (required).
- `--method xtb` — canonical token only; sampling is always force-field confab. No DFT/HF sampling backend.
- `--solvent <name>` — implicit ALPB solvent (water, methanol, dmso, …).
- `--charge N`, `--mult N` — molecular charge and spin multiplicity.
- `--max-conformers N` — max conformers generated (default 20).
- `--postopt {none,mopac}` — re-optimize the ensemble at PM7 (default `mopac`); `none` skips it.
- `--postopt-rmsd <Å>` — RMSD dedup threshold for post-opt structures, also the confab diversity cutoff (default 0.25).
- `--postopt-ewin <kcal/mol>` — energy window kept after post-opt (default 6.0).
- `--out <path>` — result JSON (default `<stem>_confsearch_<method>.json` in the run cwd).

When to use `--postopt mopac`: force-field sampling underestimates rotational well depths for short alkanes and flexible aliphatics, collapsing distinct minima. PM7 re-optimization recovers them (~0.5–1.5 kcal/mol per gauche substitution). When confab returns one conformer, post-opt seeds by rotating each non-methyl C–C bond through {gauche+, anti, gauche−}, jitters to break symmetry, optimizes at PM7, and rejects eclipsed saddles; finals are deduped by heavy-atom Kabsch RMSD.

Ring puckering (automatic): for any non-aromatic ring of size 4–8, the seed pool adds canonical Cremer–Pople puckered geometries (4-ring planar/butterfly; 5-ring envelope/twist; 6-ring chair/inverted-chair/twist-boats; 7- and 8-ring chairs/boats/crowns). Each is constrained-relaxed at GFN2-xTB with ring dihedrals frozen, then fed through PM7 post-opt + RMSD dedup. This recovers e.g. the cyclohexane twist-boat that force-field sampling never visits.

Read the JSON — it is already written to `--out` (default `<stem>_confsearch_<method>.json` in the run cwd); the CLI also writes `<basename>_conformers.xyz` (all unique post-opt conformers, or the obabel ensemble if `--postopt none`). Report: `n_conformers_found` / `n_conformers_kept`, relative force-field energies (kcal/mol), and paths to `best_conformer_xyz` and `all_conformers_xyz`. If a `postopt` block is present, also report `postopt.method`, `n_input`, `n_converged`, `n_unique`, `n_failed`, each conformer's `rel_hof_kcal_mol` / `degeneracy` / `xyz_path`, and `seed_source`. If only one conformer survives both stages for a flexible molecule, note that this is the converged PM7 answer, not a bug. For DFT-quality conformers, run this skill then re-optimize the top-K with [geometry-optimize](../geometry-optimize/SKILL.md) at DFT.


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

## Examples
```bash
# Env: anl_env
python skills/conformer-search/scripts/conformer-search.py --method xtb --max-conformers 20 --postopt mopac mol.xyz
```
See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required for all calls.
- **Sampler**: force-field confab only (MMFF94/UFF); there is no DFT/HF sampling backend. Raw conformer quality is lower than a GFN2-xTB metadynamics search — the PM7 post-opt + ring-pucker / dihedral-grid seeding is what recovers quality. Keep `--postopt mopac` on for best results.
- **Rigid molecules**: may return only one or two conformers — expected.
- **Output location**: XYZ files live in a tmp `work_directory` (in the JSON); copy them next to the input to keep them.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; compare to experiment only if the user explicitly asks.
- **Availability**: Open Babel must be installed (`conda install -c conda-forge openbabel`) — provides `obabel` (confab) and `obenergy` (ranking). `--postopt mopac` (default) additionally requires MOPAC for the PM7 step.

## References
- O'Boyle et al. "Open Babel: An open chemical toolbox." *J. Cheminform.* 2011, 3, 33. https://doi.org/10.1186/1758-2946-3-33
- O'Boyle et al. "Confab — Systematic generation of diverse low-energy conformers." *J. Cheminform.* 2011, 3, 8. https://doi.org/10.1186/1758-2946-3-8
- Stewart. "Optimization of parameters for semiempirical methods VI: PM7." *J. Mol. Model.* 2013, 19, 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
