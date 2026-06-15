---
name: transition-state
description: Locate the first-order saddle point (transition state) for a chemical reaction starting from a TS-guess geometry, with a frequency check confirming exactly one imaginary mode.
category: chemistry
---

# Transition-State Search

## Goal
Locate a first-order saddle point on the potential-energy surface starting from a TS-guess geometry, then verify it via a Hessian that the converged structure has exactly one imaginary frequency (the reaction-coordinate mode). This yields the geometry needed for an activation energy $\Delta E^{\ddagger}$ / $\Delta G^{\ddagger}$.

## Instructions
1. Parse arguments. If the `.xyz` guess is missing, **stop and ask the user**. If `--method` is missing, **ask the user** (default to `mopac`, since every other backend requires Sella to be installed separately).
2. Run the engine at the actual script path:

```bash
# Env: anl_env
python skills/transition-state/scripts/transition-state.py guess.xyz --method mopac
```

   Arguments (port from the engine `ts` subcommand):
   - `.xyz` path with the TS guess — **required**. A good guess is the energy-maximum frame of a [conformational-analysis](../conformational-analysis/SKILL.md) dihedral scan for rotation barriers; otherwise build by hand.
   - `--method {xtb,mopac,dft,hf}` — **required** (ask if missing; default `mopac`).
   - `--solvent <name>`, `--charge N`, `--mult N`, `--steps N` (default 500).
   - `--no-verify-freq` — skip the post-TS frequency verification.
   - DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`.
   - HF-only: `--basis <name>`.
3. Read the returned JSON. Copy it to `<basename>_ts_<method>.json` in the cwd. The engine also writes `<basename>_ts_<method>.xyz` with the converged TS geometry; copy it next to the user's input.
4. Report: **converged?** and the optimizer status; **heat of formation** (kcal/mol, MOPAC only) and total energy (eV); the verification-frequency result — **is this a valid TS?** (yes iff `verify_freq.n_imaginary_modes == 1`), the **imaginary frequency** (cm⁻¹) of the reaction-coordinate mode, and the **number of imaginary modes** (flag explicitly if 0 = collapsed to a minimum, or >1 = higher-order saddle); and the path to the saved TS xyz.
5. Recommend running [intrinsic-reaction-coordinate](../intrinsic-reaction-coordinate/SKILL.md) next to confirm which reactant and product the TS connects. For publication geometries, re-refine the saddle with `--method dft --tier fast` (DFT/HF TS searches are 10–100× slower and benefit greatly from a high-quality guess). See also the composite [reaction-profile](../reaction-profile/SKILL.md) skill.

## Examples
```bash
# Env: anl_env
python skills/transition-state/scripts/transition-state.py sn2_guess.xyz \
  --method mopac --charge -1 --steps 500
```
Then: "See [`examples/`](examples/) for a validated example with literature comparison."

## Constraints
- **Environment**: `# Env: anl_env` required for every code block.
- **Backend / optimizer**: `--method mopac` uses MOPAC's native `TS` keyword and needs **no extra install**. `--method xtb`, `--method dft`, and `--method hf` use the **Sella** saddle-search optimizer, which is not bundled — install once with `pip install sella`. If Sella is missing the task errors out immediately with a clear message.
- **Cost**: DFT/HF TS searches are 10–100× slower than MOPAC; supply a high-quality guess and consider locating the saddle with `mopac` first, then refining at DFT.
- **Convergence**: If the MOPAC TS does not converge, the input is likely too far from the saddle — run a [conformational-analysis](../conformational-analysis/SKILL.md) scan to find an energy maximum as a better guess.
- **Reporting policy**: **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- **Install / availability**: `pyscf` is required for `--method dft`/`--method hf` (`pip install pyscf`); MOPAC and xtb via `conda install -c conda-forge mopac xtb`.

## References
- Hermes, Sargsyan, Schaefer. "Accelerating Saddle-Point Searches" (Sella). *J. Chem. Theory Comput.* 2019, 15, 6536. https://doi.org/10.1021/acs.jctc.9b00869
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "PM7." *J. Mol. Model.* 2013, 19, 1. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. "PySCF." *J. Chem. Phys.* 2020, 153, 024109. https://doi.org/10.1063/5.0006074
- Larsen et al. "The Atomic Simulation Environment (ASE)." *J. Phys.: Condens. Matter* 2017, 29, 273002. https://doi.org/10.1088/1361-648X/aa680e
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV (1 Eh = 27.211386245981 eV) and eV→kcal/mol (23.060547830619) conversions used to report barrier energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
