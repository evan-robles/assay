---
name: reaction-energy
description: Compute the reaction energy, enthalpy, and free energy for a balanced chemical reaction by evaluating every species at one consistent level of theory and subtracting reactants from products.
category: chemistry
---

# Reaction Energy

## Goal
Compute $\Delta E_\text{rxn}$, $\Delta H_\text{rxn}$, and $\Delta G_\text{rxn}$ for a stoichiometrically balanced reaction by evaluating each species at the **same** level of theory and subtracting reactants from products. Negative $\Delta G_\text{rxn}$ → spontaneous; negative $\Delta H_\text{rxn}$ → exothermic.

## Instructions
1. Parse arguments. If no `--reactant` or no `--product` is given, **stop and ask**. If `--method` is missing, **ask** (header "Method"). Species are supplied via **repeated `--reactant` / `--product` specs**, not a single input file.
2. Run the engine:

```bash
# Env: anl_env
python skills/reaction-energy/scripts/reaction-energy.py \
  --method xtb --mode opt \
  --reactant '2*h2.xyz' --reactant o2.xyz,mult=3 \
  --product  '2*h2o.xyz'
```

   Arguments (engine `rxn-energy` subcommand):
   - `--reactant SPEC` — **required, repeatable** (≥1).
   - `--product SPEC` — **required, repeatable** (≥1).
   - `--method {xtb,mopac,dft,hf}` — **required** (ask if missing).
   - `--mode {sp,opt,freq}` (default `sp`): `sp` = single point per xyz, returns $\Delta E$ only; `opt` = optimize each species then SP; `freq` = full opt + freq per species, returns $\Delta E$, $\Delta H(T)$, $\Delta G(T)$. Use `freq` whenever $\Delta G$ matters.
   - `--solvent <name>`, `--temperature <K>` (default 298.15, `freq` only), `--pressure <Pa>` (default 101325, `freq` only).
   - DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. HF-only: `--basis <name>`.
   - **Species spec syntax**: `[COEF*]PATH[,charge=Q][,mult=M]` — e.g. `2*h2.xyz`, `acetate.xyz,charge=-1`, `3*radical.xyz,mult=2`, `complex.xyz,charge=-2,mult=3`. Each species carries its own charge/mult; the top-level `--charge` flag is **ignored** for this skill because reactants and products can differ (acid-base, redox, fragmentation).
3. Read the JSON. Copy it to a sensible filename in the cwd (default name uses the first reactant's stem).
4. Report: the balanced reaction string (e.g. `2 H2 + O2 → 2 H2O`); **$\Delta E$** (always, in kcal/mol and eV); **$\Delta H$** and **$\Delta G$** (when `--mode freq`); per-species energy/enthalpy/$G$ with converged yes/no; method, solvent, temperature, pressure; the sign-convention reminder; and every warning from the JSON (especially atom/charge imbalance and imaginary-mode flags).
5. For an activation energy $\Delta G^{\ddagger}$ this skill is **not** the right tool — use [transition-state](../transition-state/SKILL.md) + [vibrational-analysis](../vibrational-analysis/SKILL.md), or the composite [reaction-profile](../reaction-profile/SKILL.md) skill.

## Examples
```bash
# Env: anl_env
# ΔG of dimerization in water at DFT
python skills/reaction-energy/scripts/reaction-energy.py \
  --method dft --tier standard --solvent water --mode freq \
  --reactant '2*monomer.xyz' --product dimer.xyz
```
Then: "See [`examples/`](examples/) for a validated example with literature comparison."

## Constraints
- **Environment**: `# Env: anl_env` required for every code block.
- **Same-method enforcement**: the engine takes one `--method` and applies it to every species, preventing the common error of scoring reactants and products at different functionals.
- **Balance warnings**: charge imbalance and per-element atom-count imbalance are flagged as **warnings, not hard refusals** (many real reactions carry an implicit counter-ion or H⁺/e⁻). Surface the warning prominently — atom imbalance almost always indicates a user error — but proceed.
- **`--mode freq` accuracy**: every species runs a preopt + Hessian; $\Delta G$ accuracy depends on each species being a true minimum (`n_imaginary_modes == 0`). Imaginary modes are flagged in warnings.
- **Accuracy grade**: semi-empirical reaction energies are screening-grade (±3–5 kcal/mol typical). For publication numbers use `--method dft --tier standard` (or `accurate`).
- **Reporting policy**: **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- **Install / availability**: backend dependencies via `conda install -c conda-forge xtb-python mopac` or `pip install pyscf` (for `--method dft`/`hf`). A missing species file → fix the path in the spec.

## References
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "PM7." *J. Mol. Model.* 2013, 19, 1. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. "PySCF." *J. Chem. Phys.* 2020, 153, 024109. https://doi.org/10.1063/5.0006074
- Larsen et al. "The Atomic Simulation Environment (ASE)." *J. Phys.: Condens. Matter* 2017, 29, 273002. https://doi.org/10.1088/1361-648X/aa680e
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV (1 Eh = 27.211386245981 eV) and eV→kcal/mol (23.060547830619) conversions used to report reaction energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
