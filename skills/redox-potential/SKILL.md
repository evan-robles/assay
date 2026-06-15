---
name: redox-potential
description: Estimates the one- or multi-electron oxidation or reduction potential of a redox-active species against a chosen reference electrode.
category: chemistry
---

# Redox Potential

## Goal
Estimate a one- or $n$-electron redox potential $E^\circ$ of a redox-active species from the energy difference between its oxidized and reduced states, referenced to SHE, Ag/AgCl, or Fc⁺/Fc. Intended for redox-active species; neutral closed-shell hydrocarbons have no meaningful aqueous redox potential.

## Instructions
A thin MCP-client script dispatches to the engine's `redox` subcommand.

```bash
# Env: anl_env
python skills/redox-potential/scripts/redox-potential.py --method <xtb|mopac|dft|hf> --ox-charge <Qo> --red-charge <Qr> [other args] input.xyz
```

1. **Inputs.** An `.xyz` path is required (the same geometry is used for both oxidation states), plus `--ox-charge N` and `--red-charge N` (e.g. 0 and −1 for a 1-electron reduction). Stop and ask if any of `xyz`, method, `--ox-charge`, or `--red-charge` is missing.
2. **Method** (required; if missing, ask): `xtb`, `mopac`, `dft`, or `hf`.
3. **Optional:** `--ox-mult` (default 1), `--red-mult` (default 2), `--solvent` (strongly recommended), `--ref {SHE,Ag/AgCl,Fc+/Fc}` (default SHE), `--n-electrons N` (default 1). **DFT-only:** `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. **HF-only:** `--basis <name>`.
4. **Accuracy is method-dependent.** DFT redox potentials are typically ±0.1–0.2 V vs experiment with a range-separated hybrid + implicit solvent — significantly better than semi-empirical (±0.3–0.5 V). Anions auto-promote to a diffuse basis (def2-tzvp → def2-tzvpd).
5. **For publication-grade values**, optimize each oxidation state with [geometry-optimize](../geometry-optimize/SKILL.md), run [vibrational-analysis](../vibrational-analysis/SKILL.md) on each for ΔG, and ideally cross-check with a higher-level method. This skill is for screening, not final answers.
6. **Read the JSON** and report:
   - **E° vs reference** (in V).
   - ΔE_redox (eV, kcal/mol).
   - Energies of the oxidized and reduced states.
   - **Warn explicitly**: semi-empirical methods give ±0.3–0.5 V at best; the calculation uses the same geometry for both states (no reorganization energy); solvation correction is implicit-only.
   - Path to the saved JSON.

## Examples
```bash
# Env: anl_env
python skills/redox-potential/scripts/redox-potential.py --method dft --tier standard --ox-charge 0 --red-charge -1 --solvent water --ref SHE benzoquinone.xyz
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` is required for all script calls.
- `xtb` (GFN2-xTB) and `mopac` (PM7) are semi-empirical; `dft` and `hf` run via PySCF.
- Same geometry is used for both oxidation states — no reorganization energy is included.
- Solvation is implicit only and strongly recommended (use `--solvent`); aqueous redox potentials are meaningless without it. Anions auto-promote to a diffuse basis.
- Energy zeros differ across backends — only same-method energies are directly comparable.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; do not volunteer accepted/measured/reference values or editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- Errors: `xtb`/`mopac` not installed → `conda install -c conda-forge xtb mopac`; `pyscf` not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).

## References
- Bannwarth, C.; Ehlert, S.; Grimme, S. "GFN2-xTB", *J. Chem. Theory Comput.* **2019**, 15 (3), 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart, J. J. P. "Optimization of parameters for semiempirical methods VI (PM7)", *J. Mol. Model.* **2013**, 19 (1), 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun, Q.; et al. "Recent developments in the PySCF program package", *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- Mardirossian, N.; Head-Gordon, M. "ωB97X-V", *Phys. Chem. Chem. Phys.* **2014**, 16, 9904-9924. https://doi.org/10.1039/C3CP54374A
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV (1 Eh = 27.211386245981 eV) and eV→kcal/mol (23.060547830619) conversions used in the redox thermodynamic cycle.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
