---
description: Geometry Optimization — When the user wants to relax a structure to a local minimum, find the equilibrium geometry, or pre-optimize a sketch-quality input (e.g. "optimize this geometry", "minimize the structure", "relax", "opt", "find the minimum-energy geometry"). Do NOT use for energy-only evaluation (use single_point_energy) or barrier scans (use conformational_analysis).
---

# Geometry Optimization

Relax a molecular structure to a local minimum on the GFN2-xTB or PM7 surface.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- A method: `xtb` or `mopac` (required — if missing, use **AskUserQuestion**)
- Optional: `--solvent <name>`, `--charge N`, `--mult N`, `--fmax <eV/Å>` (default 0.05)

## Steps
1. Parse args (same rules as single_point_energy).
2. Run `chemkit opt --method <METHOD> [...] <XYZ>`.
3. Read the JSON. Copy the produced `*_opt.xyz` next to the user's input file as `<stem>_<method>_opt.xyz`.
4. Report:
   - Whether the optimization converged
     - For `xtb`: include the number of BFGS steps (`n_steps`)
     - For `mopac`: include `mopac_status` and `mopac_gradient_norm_kcal_per_A` (MOPAC uses its native EF optimizer, not BFGS — `n_steps` is not reported)
   - Final total energy (and `final_heat_of_formation_kcal_mol` when present, for `mopac`)
   - Path to the optimized `.xyz` file (paste its contents in a fenced block)
   - Path to the JSON
   - Any `warnings` entries verbatim

## Errors
- If not converged → still deliver the last geometry, flag `converged: false` prominently.
