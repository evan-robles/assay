---
description: Geometry Optimization — When the user wants to relax a structure to a local minimum, find the equilibrium geometry, or pre-optimize a sketch-quality input (e.g. "optimize this geometry", "minimize the structure", "relax", "opt", "find the minimum-energy geometry"). Do NOT use for energy-only evaluation (use single_point_energy) or barrier scans (use conformational_analysis).
---

# Geometry Optimization

Relax a molecular structure to a local minimum on the chosen PES. Supported methods:
- `xtb` (GFN2-xTB) — fast semi-empirical, ASE BFGS
- `mopac` (PM7) — fast semi-empirical, MOPAC's native EF optimizer
- `dft` — ab initio DFT via PySCF, ASE BFGS with analytic gradients
- `hf` — Hartree-Fock via PySCF, ASE BFGS with analytic gradients

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- `--method {xtb,mopac,dft,hf}` (required — if missing, use **AskUserQuestion**)
- All methods: `--solvent <name>`, `--charge N`, `--mult N`, `--fmax <eV/Å>` (default 0.05)
- `dft` only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- `hf` only: `--basis <name>`

## DFT/HF cost
DFT optimizations are 10–100× slower than xtb. Default to `--tier fast` (r²SCAN/def2-SVP) for first-pass relaxation, then re-optimize at `--tier standard` if needed. For very flexible molecules consider pre-optimizing at `--method xtb` first.

## Steps
1. Parse args (same rules as single_point_energy).
2. Run `chemkit opt --method <METHOD> [--tier <T>] [--functional <F>] [--basis <B>] [...] <XYZ>`.
3. Read the JSON. Copy the produced `*_opt.xyz` next to the user's input file as `<stem>_<method>_opt.xyz`.
4. Report:
   - Whether the optimization converged
     - For `xtb`/`dft`/`hf`: include the number of BFGS steps (`n_steps`)
     - For `mopac`: include `mopac_status` and `mopac_gradient_norm_kcal_per_A` (MOPAC uses its native EF optimizer, not BFGS — `n_steps` is not reported)
   - Final total energy (and `final_heat_of_formation_kcal_mol` when present, for `mopac`)
   - For `dft`/`hf`: also surface functional/basis/tier
   - Path to the optimized `.xyz` file (paste its contents in a fenced block)
   - Path to the JSON
   - Any `warnings` entries verbatim

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- If not converged → still deliver the last geometry, flag `converged: false` prominently.
- pyscf not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).

## Running this skill

This skill is a single self-contained script. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python geometry_optimize.py --help                 # full argument list
```

The chemistry engine is inlined into `geometry_optimize.py`; no other files are required.
