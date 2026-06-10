---
description: Conformational Analysis (Relaxed Dihedral Scan) — When the user wants a deterministic torsional energy profile or rotation barrier around a specific bond (e.g. "dihedral scan", "torsion scan", "rotation barrier", "torsional energy profile", "scan this bond", "conformational analysis"). Produces a PNG plot and a relaxed trajectory per dihedral, with per-point data recorded in the JSON. Do NOT use for stochastic conformer ensemble sampling — that's conformer_search.
---

# Conformational Analysis (Relaxed Dihedral Scan)

Map the torsional energy profile around a rotatable bond. At each angle in a
0–360° sweep, the geometry is re-optimized with the chosen dihedral held (or
strongly biased toward) the target. Output is a per-dihedral PNG plot and a
relaxed XYZ trajectory; per-point data is recorded in the JSON.

This complements `conformer_search`: confsearch *finds* minima stochastically,
scan *connects* minima with a deterministic barrier profile.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- `--method {xtb,mopac,dft,hf}` (required)
- Optional:
  - `--dihedral i,j,k,l` — **1-based** atom indices of the four atoms defining
    the torsion (matches the C1, C2, ... labels in plots and filenames). If
    omitted, the task auto-detects all non-methyl, non-ring rotatable single
    bonds and scans each independently.
  - `--steps N` (default 24, i.e. 15° resolution)
  - `--fmax <eV/Å>` (default 0.05) — per-step force convergence
  - `--opt-steps N` (default 200) — max iterations per scan point
  - `--charge N`, `--mult N`, `--solvent <name>`
  - DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
  - HF-only: `--basis <name>`

## Constraint mechanics
- **xtb / dft / hf**: ASE `FixInternals` holds the dihedral *exactly* at the
  target. Measured ≈ target within ~0.1°.
- **mopac**: PM7's EF optimizer doesn't expose a clean per-dihedral constraint,
  so each scan point pre-rotates the side atoms to the target dihedral and runs
  a normal optimization. EF may drift the dihedral by a few degrees while
  relaxing other internal modes; the reported `measured_deg` reflects what
  actually came out. This is the same trade-off `conformer_search`'s post-opt
  step makes — accept some drift in exchange for robust convergence.

## DFT/HF cost
A 24-point scan at `--tier standard` runs 24 constrained optimizations. For a 15-atom molecule expect ~30–60 min total on 8 cores. Use `--tier fast` (r²SCAN/def2-SVP) for screening, or pre-locate the dihedral with `--method xtb` then refine the saddle region with DFT.

## When to use
- You want the **rotational barrier height** for a specific bond.
- You suspect a molecule has multiple conformers and want to see them connected
  on the PES (one scan → minima at every well, saddles between them).
- You want a publication-quality torsional profile plot.
- Use `conformer_search` instead when the molecule has *many* coupled flexible
  degrees of freedom and you just want the lowest-energy ensemble.

## Steps
1. Parse args. Stop and ask if `.xyz` missing.
2. Run `chemkit scan --method <m> [--dihedral i,j,k,l] [--steps N] <XYZ>`.
3. Read the JSON. For each dihedral entry, report:
   - The 4-atom selection (with element symbols, 1-based, e.g. `C1–C2–C3–C4`)
   - `barrier_kcal_mol`, `min_angle_deg`, `max_angle_deg`
   - `n_converged` / `n_points`
   - **PNG path** (always surface this as a primary deliverable — it is the
     headline result of a torsional scan)
   - Path to the `.xyz` trajectory
4. If `n_dihedrals_scanned == 0`, the molecule has no rotatable bonds. Suggest
   `--dihedral i,j,k,l` if the user wants to force a scan anyway.

## Outputs (REQUIRED — every successful scan produces both)
For each scanned dihedral, two files are written next to the JSON:
- `<stem>_dih<i>_<a>_<b>_<l>.png` — **ΔE vs angle line plot** (matplotlib,
  150 dpi). This is a required deliverable, not optional. The title contains:
  - The molecule's IUPAC name (resolved via Open Babel → PubChem); falls back
    to the input filename stem if lookup fails or there's no network.
  - The method used (e.g. `GFN2-xTB` or `PM7 / MOPAC`).
  - The four atoms defining the dihedral, labeled with element symbol and
    canonical chain index, **1-based** (e.g. `C1–C2–C3–C4`). For PDB inputs,
    labels use residue.atom notation (e.g. `ASP47.CA–ASP47.CB–ASP47.CG–ASP47.OD1`).
- `<stem>_dih<i>_<a>_<b>_<l>.xyz` — relaxed trajectory, one frame per step

Per-point data (step / target° / measured° / E / ΔE / converged) is recorded
in the `points` array of the JSON result.

Atom indices in filenames and labels reflect the canonical chain ordering that
this task applies automatically (longest heavy-atom path via BFS, with RDKit
canonical ranks for direction tie-breaks). User-supplied `--dihedral i,j,k,l`
indices are remapped to this ordering automatically.

## Notes
- Default 24 points is sufficient for typical sp3-sp3 alkane rotation barriers
  (3-fold periodicity → 8 points per well). Bump to 36 (10° resolution) or 72
  (5°) for sharp profiles or transition-state localization.
- For multi-bond auto-detect, each dihedral scan is independent — coupling
  effects (e.g. φ-ψ ribbons in peptides) are *not* captured. Use the 2D-scan
  path if/when added.
- The PNG is sorted by measured angle for clean lines, so the trace direction
  may not match scan order if mopac drift reorders points near boundaries.
