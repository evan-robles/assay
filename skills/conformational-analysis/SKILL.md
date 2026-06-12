---
name: conformational-analysis
description: Map the torsional energy profile and rotation barrier around a specific bond via a relaxed dihedral scan, producing a per-dihedral energy-vs-angle PNG and a relaxed trajectory.
category: chemistry
---

# Conformational Analysis (Relaxed Dihedral Scan)

## Goal
Map the torsional energy profile $\Delta E(\theta)$ around a rotatable bond. At each angle in a 0–360° sweep the geometry is re-optimized with the chosen dihedral held at (or strongly biased toward) the target, yielding the rotation-barrier height and the connectivity of conformer wells. Output is a per-dihedral PNG plot and a relaxed XYZ trajectory; per-point data is recorded in the JSON.

## Instructions
1. Parse arguments. If the `.xyz` is missing, **stop and ask the user**.
2. Run the engine at the actual script path:

```bash
# Env: anl_env
python skills/conformational-analysis/scripts/conformational-analysis.py butane.xyz --method xtb
```

   Arguments (port from the engine `scan` subcommand):
   - `.xyz` path — **required**.
   - `--method {xtb,mopac,dft,hf}` — **required**.
   - `--dihedral i,j,k,l` — **1-based** atom indices of the four atoms defining the torsion. If omitted, the task **auto-detects all non-ring rotatable single bonds** (including methyl rotors) and scans each independently.
   - `--steps N` (default 24, i.e. 15° resolution).
   - `--fmax <eV/Å>` (default 0.05) — per-step force convergence.
   - `--opt-steps N` (default 200) — max iterations per scan point.
   - `--charge N`, `--mult N`, `--solvent <name>`.
   - DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. HF-only: `--basis <name>`.
3. Read the returned JSON. For each dihedral entry report: the 4-atom selection (element symbols, 1-based, e.g. `C1–C2–C3–C4`); `barrier_kcal_mol`, `min_angle_deg`, `max_angle_deg`; `n_converged` / `n_points`; the **PNG path** (always surface this as the primary deliverable — it is the headline result); and the path to the `.xyz` trajectory.
4. Two files are written next to the JSON per scanned dihedral (both required): `<stem>_dih<i>_<a>_<b>_<l>.png` — ΔE-vs-angle line plot (matplotlib, 150 dpi; title carries IUPAC name via Open Babel → PubChem with filename fallback, the method, and the four atoms) — and `<stem>_dih<i>_<a>_<b>_<l>.xyz` — relaxed trajectory, one frame per step. Per-point data (step / target° / measured° / E / ΔE / converged) is in the `points` array of the JSON.
5. If `n_dihedrals_scanned == 0`, the molecule has no rotatable bonds — suggest `--dihedral i,j,k,l` to force a scan. The energy-maximum frame is a good TS guess for [transition-state](../transition-state/SKILL.md). Use [conformer-search](../conformer-search/SKILL.md) instead for stochastic ensemble sampling of many coupled flexible degrees of freedom.

## Examples
```bash
# Env: anl_env
# 72-point (5°) scan of the central C–C torsion of butane
python skills/conformational-analysis/scripts/conformational-analysis.py butane.xyz \
  --method xtb --dihedral 1,2,3,4 --steps 72
```
Then: "See [`examples/`](examples/) for a validated example with literature comparison."

## Constraints
- **Environment**: `# Env: anl_env` required for every code block.
- **Auto-detection**: with no `--dihedral`, the task auto-detects all non-ring rotatable single bonds (including methyl rotors) and scans each independently; for multi-bond auto-detect each scan is independent — coupling effects (e.g. φ-ψ ribbons in peptides) are not captured.
- **Constraint mechanics**: for `xtb`/`dft`/`hf`, ASE `FixInternals` holds the dihedral exactly (measured ≈ target within ~0.1°). For `mopac`, PM7's EF optimizer has no clean per-dihedral constraint, so each point pre-rotates the side atoms then runs a normal optimization; EF may drift the dihedral a few degrees, and `measured_deg` reflects what actually came out. The PNG is sorted by measured angle, so trace direction may not match scan order near boundaries under mopac drift.
- **Indexing**: atom indices in filenames/labels reflect the canonical chain ordering applied automatically (longest heavy-atom path via BFS, RDKit canonical ranks for tie-breaks); user-supplied `--dihedral` indices are remapped to this ordering automatically.
- **Resolution / cost**: default 24 points suits typical sp3-sp3 alkane rotors (3-fold periodicity → 8 points/well); bump to 36 (10°) or 72 (5°) for sharp profiles. A 24-point DFT scan at `--tier standard` runs 24 constrained optimizations (~30–60 min for a 15-atom molecule on 8 cores); use `--tier fast` for screening, or pre-locate with `xtb` then refine with DFT.
- **Reporting policy**: **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- **Install / availability**: backends via `conda install -c conda-forge xtb mopac`; `pip install pyscf` for `--method dft`/`hf`. PNG titles need Open Babel + network for IUPAC lookup (falls back to filename stem otherwise).

## References
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "PM7." *J. Mol. Model.* 2013, 19, 1. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. "PySCF." *J. Chem. Phys.* 2020, 153, 024109. https://doi.org/10.1063/5.0006074
- Larsen et al. "The Atomic Simulation Environment (ASE)." *J. Phys.: Condens. Matter* 2017, 29, 273002. https://doi.org/10.1088/1361-648X/aa680e

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
