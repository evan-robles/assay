---
name: pka-acidity
description: Estimate the aqueous pKa of an acid HA from a thermodynamic cycle, either absolutely or anchored against a known reference acid.
category: chemistry
---

# pKa Estimation

## Goal
Compute an aqueous $\mathrm{p}K_a$ from the thermodynamic cycle $\mathrm{HA(aq)} \rightleftharpoons \mathrm{A^-(aq)} + \mathrm{H^+(aq)}$ with full optimization and frequency analysis on each species in implicit solvent. Two modes are available: **absolute** (uses a literature $G(\mathrm{H^+,aq})$ reference; large systematic error) and **reference** (anchors against a known acid via an isodesmic exchange so most systematic errors cancel; strongly recommended).

## Instructions
Run:

```bash
# Env: anl_env
python skills/pka-acidity/scripts/pka-acidity.py [args]
```

Arguments:
- `--ha <path>` (required) — xyz of the protonated form HA.
- `--a-minus <path>` (required) — xyz of the deprotonated form A⁻.
- `--method {xtb,mopac,dft,hf}` (required — if missing, ask the user).
- `--mode {absolute,reference}` (default `absolute`).
- `--solvent <name>` (default `water` — the absolute $G(\mathrm{H^+})$ ref only applies to water).
- `--ha-charge N` (default 0; A⁻ charge is automatically HA charge − 1).
- `--ha-mult N`, `--a-minus-mult N` (defaults 1).
- `--hplus-reference {tissandier_1998,kelly_2006}` (default `tissandier_1998`, −270.28 kcal/mol; Kelly gives −265.9, shifting every pKa by ~1.4 units).
- `--temperature K` (default 298.15), `--pressure Pa` (default 101325).
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`.
- HF-only: `--basis <name>`.
- Reference-mode extras (all required when `--mode reference`): `--ref-ha <path>`, `--ref-a-minus <path>`, `--pka-ref FLOAT` (the experimental pKa of the reference acid — a user-supplied input), `--ref-ha-charge N`, `--ref-ha-mult N`, `--ref-a-minus-mult N`.

Stop and ask if `--ha` or `--a-minus` is missing, if `--method` is missing, or if `--mode reference` is requested without `--ref-ha` / `--ref-a-minus` / `--pka-ref`.

You must supply both HA and A⁻ xyz files yourself — build the deprotonated form via [build-from-smiles](../build-from-smiles/SKILL.md) (e.g. `CC(=O)O` and `CC(=O)[O-]`) or hand-edit an xyz to delete the acidic proton and run [geometry-optimize](../geometry-optimize/SKILL.md).

Then read the returned JSON and report: the $\mathrm{p}K_a$ (headline number); mode, solvent, temperature; for absolute mode $G(\mathrm{HA})$, $G(\mathrm{A^-})$, $G(\mathrm{H^+,aq})$, the standard-state correction, and $\Delta G_\mathrm{dissociation}$; for reference mode $\Delta G_\mathrm{isodesmic}$ and the reference acid with its experimental pKa; the reminder that lower pKa → stronger acid; every warning (especially imaginary modes on any species); and an estimate of expected error (xtb absolute is not meaningful; xtb reference ±2 units; DFT absolute standard tier ±3 units; DFT reference with a similar anchor ±1 unit or better).

## Examples
```bash
# Env: anl_env
python skills/pka-acidity/scripts/pka-acidity.py --method dft --tier standard \
  --solvent water --mode reference \
  --ha propionic.xyz --a-minus propionate.xyz \
  --ref-ha acetic.xyz --ref-a-minus acetate.xyz --pka-ref 4.76
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required.
- **Inputs**: both HA and A⁻ xyz files required; HA and A⁻ charges differing by anything other than +1 is a hard error (malformed cycle).
- **Modes**: absolute mode carries a large method-dependent systematic error (xtb can be off by 50+ units); reference mode cancels most of it and is strongly recommended whenever a chemically similar anchor exists. The standard-state correction (+1.89 kcal/mol = RT ln 24.46 at 298 K) is applied automatically in absolute mode.
- **Solvent**: a non-aqueous solvent in absolute mode triggers a warning — the $G(\mathrm{H^+})$ reference is parametrized for water.
- **Scope**: each pKa of a polyprotic acid needs its own run; very strong acids/bases (pKa < 0 or > 14) compute but should be treated as order-of-magnitude.
- **Cost**: at DFT this is 2× full opt+freq for absolute mode, 4× for reference mode (~10–60 min per species at standard tier on a 15-atom molecule).
- **Reporting policy**: Never automatically provide experimental or literature data for comparison; report only computed values; compare to experiment only if the user explicitly asks. (Reference mode's `--pka-ref` is a user-supplied input, not auto-reported literature, and is allowed.)
- **Install/availability**: `conda install -c conda-forge xtb-python mopac` or `pip install pyscf`. Soft modes are raised to a 50 cm⁻¹ floor (quasi-RRHO).

## References
- Bannwarth, Ehlert, Grimme. *J. Chem. Theory Comput.* **2019**, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. *J. Mol. Model.* **2013**, 19, 1. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- Larsen et al. *J. Phys.: Condens. Matter* **2017**, 29, 273002. https://doi.org/10.1088/1361-648X/aa680e

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
