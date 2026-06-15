---
name: intrinsic-reaction-coordinate
description: Confirm which reactant and product a transition state connects by walking down the gradient from the saddle in both directions, producing forward and reverse reaction-path trajectories.
category: chemistry
---

# Intrinsic Reaction Coordinate (IRC)

## Goal
Starting from a transition-state geometry, walk down the gradient along the reaction-coordinate (imaginary-frequency) mode in both directions to trace the minimum-energy path as a function of the reaction coordinate $s$. This confirms which reactant and product the saddle point connects.

## Instructions
1. Parse arguments. If the `.xyz` is missing, **stop and ask the user**. If `--method` is missing, **ask the user** (header "Method").
2. Run the engine at the actual script path:

```bash
# Env: anl_env
python skills/intrinsic-reaction-coordinate/scripts/intrinsic-reaction-coordinate.py ts.xyz --method mopac
```

   Arguments (port from the engine `irc` subcommand):
   - `.xyz` path with a **TS geometry** — **required** (usually the output of [transition-state](../transition-state/SKILL.md)).
   - `--method {xtb,mopac}` — **required** (ask if missing).
   - `--solvent <name>`, `--charge N`, `--mult N`.
   - `--max-points N` (default 40).
   - `--step <au>` (xtb only, default 0.05).
3. Read the returned JSON. Copy it to `<basename>_irc_<method>.json` in the cwd. The engine also writes `<basename>_irc_<method>_forward.xyz` and `..._reverse.xyz` trajectory files; copy them next to the user's input.
4. Report: **forward** and **reverse endpoint energies** (eV); the **energy drops** from the TS in each direction (kcal/mol — both should be negative for a real saddle); **distinct_endpoints** (true if the two endpoints differ by > 0.01 eV); the paths to the two trajectory xyz files; and the per-direction status messages.
5. If `distinct_endpoints` is false, both directions relaxed to the same minimum — usually the input was not a true TS, or the imaginary mode was very weak. Recommend re-running [transition-state](../transition-state/SKILL.md) with a different guess, or running [vibrational-analysis](../vibrational-analysis/SKILL.md) to verify exactly one imaginary mode. To get a DFT-quality path, run IRC at `xtb`/`mopac` first, then re-optimize each endpoint with [geometry-optimize](../geometry-optimize/SKILL.md) at DFT.

## Examples
```bash
# Env: anl_env
python skills/intrinsic-reaction-coordinate/scripts/intrinsic-reaction-coordinate.py \
  sn2_ts.xyz --method mopac --charge -1 --max-points 40
```
Then: "See [`examples/`](examples/) for a validated example with literature comparison."

## Constraints
- **Environment**: `# Env: anl_env` required for every code block.
- **Backend**: MOPAC uses the native `IRC=1` keyword. The xtb backend uses a simple Python steepest-descent on mass-weighted Cartesian coordinates, seeded by the lowest-eigenvalue mode of the Eckart-projected Hessian.
- **dft / hf NOT supported**: the descent algorithm is xtb/mopac-specific; `irc --method dft` (or `hf`) errors out with a clear message. For a DFT-quality reaction path, run IRC with `--method xtb` or `--method mopac`, then re-optimize each endpoint individually with [geometry-optimize](../geometry-optimize/SKILL.md) at `--method dft`.
- **xtb caveat**: if no imaginary mode is found at the input geometry, the steepest-descent collapses to the input itself — flag this and recommend confirming TS character with [vibrational-analysis](../vibrational-analysis/SKILL.md) first.
- **Reporting policy**: **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- **Install / availability**: MOPAC must be on PATH (`conda install -c conda-forge mopac`); xtb via `conda install -c conda-forge xtb`.

## References
- Hermes, Sargsyan, Schaefer. "Accelerating Saddle-Point Searches" (Sella). *J. Chem. Theory Comput.* 2019, 15, 6536. https://doi.org/10.1021/acs.jctc.9b00869
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "PM7." *J. Mol. Model.* 2013, 19, 1. https://doi.org/10.1007/s00894-012-1667-x
- Larsen et al. "The Atomic Simulation Environment (ASE)." *J. Phys.: Condens. Matter* 2017, 29, 273002. https://doi.org/10.1088/1361-648X/aa680e
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV (1 Eh = 27.211386245981 eV) and eV→kcal/mol (23.060547830619) conversions used to report energies along the path.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
