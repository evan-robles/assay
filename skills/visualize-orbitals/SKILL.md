---
name: visualize-orbitals
description: Produce viewable molecular-orbital wavefunction files (a molden file always, plus optional cube files for selected orbitals) so the user can inspect HOMO, LUMO, or any MO isosurface in an external viewer.
category: chemistry
---

# Visualize Molecular Orbitals

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
Dump the SCF wavefunction in formats every modern viewer reads so the user can inspect orbital isosurfaces (HOMO, LUMO, or any specific MO). A `.molden` file is always written; `.cube` files for selected orbitals are evaluated on a 3D grid on request. The skill writes files only — it performs **no rendering**.

## Instructions
1. Parse arguments. If the `.xyz` is missing, **stop and ask**. If `--method` is missing, **ask** (header "Method", options `xtb` / `mopac` / `dft` / `hf`).
2. If the user asked to "see the HOMO" / "plot the LUMO" / etc. but didn't specify cubes, default to `--cubes homo,lumo`.
3. Run the engine:

```bash
# Env: anl_env
python skills/visualize-orbitals/scripts/visualize-orbitals.py mol.xyz \
  --method dft --cubes homo,lumo --grid 80
```

   Arguments (port from the engine `orbitals` subcommand):
   - `.xyz` path — **required**.
   - `--method {xtb,mopac,dft,hf}` — **required** (ask if missing).
   - `--solvent <name>` (water, methanol, dmso, mecn, dcm, …), `--charge N`, `--mult N`, `--out <path>` (result JSON; default `<stem>_orbitals_<method>.json` in the run cwd).
   - `--cubes <list>` — comma-separated orbital labels to render as `.cube` files. Each label is one of: `homo`, `lumo`, `homo-1`, `lumo+2`, …; a 1-based MO index (e.g. `5`); optionally suffixed `:alpha` or `:beta` for open-shell (e.g. `homo:alpha`). Default empty — only the molden is written.
   - `--grid N` — cube resolution (default 80; 50 = quick preview, 120 = publication).
   - DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. HF-only: `--basis <name>`. **`--density-fit`** enables RI density fitting (~3-10x faster SCF, ~0.1-0.8 mEh error); OFF by default — chemkit uses exact integrals (plain RKS/UKS, matching hand-run PySCF).
4. Read the result JSON — it is written to `--out` (default `<stem>_orbitals_<method>.json` in the run cwd); read it there.
5. Report: the path to the `.molden` file (always present) and any `.cube` files (one per requested orbital — these are the deliverables the user opens in **Avogadro / Jmol / IboView / VMD / PyMOL / Multiwfn**; no rendering is done here); the MO summary from `mo_summary` (HOMO / LUMO indices and energies in eV; alpha + beta separately for open-shell); method, solvent (or "gas phase"), charge, multiplicity; every warning from the result JSON (including, for MOPAC, the STO-3G re-fit warning), reproduced verbatim — none dropped, summarized, or paraphrased; if there are no warnings, say so; and one short "how to view" line. For orbital energies only, use [frontier-orbitals](../frontier-orbitals/SKILL.md); for electrostatic potential / partial charges, use [electrostatics](../electrostatics/SKILL.md).


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

## Examples
```bash
# Env: anl_env
# HOMO/LUMO cubes at PySCF DFT, publication-resolution grid
python skills/visualize-orbitals/scripts/visualize-orbitals.py benzene.xyz \
  --method dft --tier standard --cubes homo,lumo --grid 120
```
Then: "See [`examples/`](examples/) for a validated example with literature comparison."

## Constraints
- **Environment**: `# Env: anl_env` required for every code block.
- **No rendering**: the skill writes `.molden` (always, ~10–100 KB, holds the full SCF) and optional `.cube` files only — the user opens them in Avogadro, Jmol, IboView, VMD, or Multiwfn. This keeps the skill fast (molden takes ~no time; cubes only on demand) and dependency-light.
- **Backends**: xtb uses `xtb --molden`; PySCF (dft/hf) uses `pyscf.tools.molden.from_scf`; MOPAC uses `GRAPHF` and the molden is synthesized by **re-fitting each PM7 STO as STO-3G Gaussians** so the cube path works. MOPAC shapes are qualitatively right but absolute amplitudes differ from a native PM7 plot — surface this warning.
- **Reporting policy**: **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not editorialize about whether the orbital "looks like" textbook expectations unless the user asks.
- **Install / availability**: xtb / mopac via `conda install -c conda-forge xtb mopac`; `pip install pyscf` for `--method dft`/`hf`. If xtb refuses to write `molden.input` (usually an unsupported element), report the stderr tail. Slow cube generation → suggest `--grid 50` for previews. Malformed `.xyz` → report which line failed.

## References
- Schaftenaar, Noordik. "Molden: a pre- and post-processing program for molecular and electronic structures." *J. Comput.-Aided Mol. Des.* 2000, 14, 123. https://doi.org/10.1023/A:1008193805436
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "PM7." *J. Mol. Model.* 2013, 19, 1. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. "PySCF." *J. Chem. Phys.* 2020, 153, 024109. https://doi.org/10.1063/5.0006074
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV conversion (1 Eh = 27.211386245981 eV) used to report orbital energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
