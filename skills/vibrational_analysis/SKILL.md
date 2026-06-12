---
description: Vibrational Analysis + Thermochemistry (Opt-Freq) — When the user wants frequencies, IR spectrum, normal modes, zero-point energy, thermochemistry (H, S, G, Cp), or to verify a stationary point is a minimum or transition state (e.g. "compute frequencies", "freq", "opt-freq", "thermochemistry", "Gibbs free energy", "ZPE", "imaginary frequencies", "is this a TS").
---

# Vibrational Analysis + Thermochemistry (Opt-Freq)

Optimize the input geometry, then compute the finite-difference / analytic
Hessian → vibrational frequencies, ZPE, enthalpy, entropy, Gibbs energy at T,P.

The pre-optimization is automatic and on by default. This is the standard
"opt-freq" workflow used in every electronic-structure package: taking the
Hessian at a true stationary point eliminates spurious imaginary modes that
appear when the geometry has residual gradient. Pass `--no-preopt` only when
the input is already converged at the same method (e.g. you just ran `opt`
with the same method and don't want to repeat it).

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required) — does NOT need to be pre-optimized; the freq
  step optimizes it first by default
- `--method {xtb,mopac,dft,hf}` (required)
- Optional:
  - `--solvent`, `--charge`, `--mult`
  - `--temperature <K>` (default 298.15)
  - `--pressure <Pa>` (default 101325)
  - `--geometry {linear,nonlinear,monatomic}` (default nonlinear) — used for
    the xtb/dft/hf (`IdealGasThermo`) path; MOPAC detects this from the
    moment of inertia internally.
  - `--symmetry <σ>` (default 1) — rotational symmetry number, IdealGasThermo path only
  - `--no-preopt` — skip the automatic optimization step
  - `--preopt-fmax <eV/Å>` (default 0.01, tighter than `opt`'s 0.05) — residual
    forces propagate into near-zero imaginary modes, so the pre-opt aims tighter
  - DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
  - HF-only: `--basis <name>`

## DFT/HF cost
Hessians via PySCF are roughly (6N+1)× the SCF time. For a 20-atom molecule at `--tier standard` expect ~30 min on 8 cores. Default to `--tier fast` for screening and reserve `standard` for the final answer.

## Steps
1. Parse args. If method missing, AskUserQuestion.
2. Run `chemkit freq --method <M> [--symmetry <σ>] [...] <XYZ>`.
3. Read JSON, copy to `<basename>_freq_<method>.json`.
4. Report:
   - From `preopt` block: whether the pre-opt converged, number of opt steps,
     and the pre-opt energy. If the user passed `--no-preopt`, say so.
   - ZPE, enthalpy (H), entropy (S), Gibbs free energy (G), in both eV and
     kcal/mol where the schema provides both.
   - Number of real / imaginary modes (warn loudly if any imaginary modes remain
     *after* the pre-opt — that's a real saddle point, not a residual-gradient
     artifact).
   - Frequency list (cm⁻¹) — top 10 + lowest 10 if there are many
5. If imaginary modes remain after pre-opt → the geometry is a true saddle
   point (transition state or higher-order). Suggest the user explore in the
   direction of the imaginary normal mode, or use `conformer_search` to find a
   nearby minimum.

## Notes
- The pre-opt uses the same method as the freq step, so the optimized geometry
  is consistent with the Hessian. Mixing methods (e.g. xtb opt + mopac freq)
  is the classic way to get apparent imaginary modes — avoid it.
- ASE's `IdealGasThermo` (xtb path) assumes ideal gas; gas-phase or
  implicit-solvent only.
- For the xtb path, the user must supply correct `--geometry` (linear vs
  nonlinear) and `--symmetry` (rotational σ) for correct rotational and
  translational partition functions.
- The `preopt` block in the result JSON records the path to the optimized xyz
  used for the Hessian, plus its energy/HoF — useful for cross-checking.

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Running this skill

This skill is a single self-contained script. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python vibrational_analysis.py --help                 # full argument list
```

The chemistry engine is inlined into `vibrational_analysis.py`; no other files are required.

### In-terminal 3D view (asciimol)

When run on an interactive terminal with `asciimol` installed, this skill opens
the resulting geometry in an ASCII 3D viewer automatically (press `q` to quit).
Pass `--no-view` to disable it. The viewer never launches in non-interactive
runs (pipes, tests, agent automation), so it is safe to script.

```bash
pip install asciimol     # one-time: enables the in-terminal viewer
```
