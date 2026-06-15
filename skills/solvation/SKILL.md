---
name: solvation
description: Estimates the electronic solvation free energy of a molecule in a given implicit solvent.
category: chemistry
---

# Solvation

## Goal
Estimate the solvation free energy $\Delta G_\text{solv} = E_\text{solvated} - E_\text{gas}$ using implicit solvation on the supplied geometry. Electronic only — no cavitation, dispersion-repulsion, or thermal corrections. Screening-grade at semi-empirical accuracy (±2–3 kcal/mol typical). For octanol/water partition specifically, use [logp-partition](../logp-partition/SKILL.md) instead.

## Instructions
1. Parse arguments. If the `.xyz` path is missing, stop and ask. If `--method` is missing, use **AskUserQuestion**. If `--solvent` is missing, stop and ask.
2. Run the engine:

```bash
# Env: anl_env
python skills/solvation/scripts/solvation.py --method <xtb|mopac|dft|hf> --solvent <S> [--charge N] [--mult N] [--tier <T>] [--functional <F>] [--basis <B>] [--out <path>] input.xyz
```

Arguments:
- `input.xyz` — molecular geometry (required).
- `--method {xtb,mopac,dft,hf}` — **required**.
- `--solvent <name>` — **required**; one of water, methanol, ethanol, acetone, mecn, dmso, thf, dcm, chloroform, toluene, benzene, hexane, ether, octanol (matched case-insensitively).
- `--charge N`, `--mult N` — molecular charge and spin multiplicity.
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`.
- HF-only: `--basis <name>`.
- `--out <path>` — result JSON (default `<stem>_solvation_<method>.json` in the run cwd).

DFT with `--tier standard` and an implicit solvent gives meaningfully better $\Delta G_\text{solv}$ than semi-empirical (~±1 kcal/mol vs ±2–3 for xtb/mopac) at much higher cost. The DFT path uses ddCOSMO; true research-grade SMD parameterization would require PySCF's `pyscf.solvent.smd` directly.

Read the JSON — it is already written to `--out` (default `<stem>_solvation_<method>.json` in the run cwd). Report: $\Delta G_\text{solv}$ in kcal/mol (primary) and eV; $E_\text{gas}$ and $E_\text{solvated}$ for context; method, solvent, charge/multiplicity; and the caveats (electronic-only; ±2–3 kcal/mol at semi-empirical; no cavity term). Flag any JSON warnings, especially the $|\Delta G_\text{solv}| \approx 0$ silent-drop warning. For tighter numbers, run [geometry-optimize](../geometry-optimize/SKILL.md) separately in gas phase and in solvent and compute $\Delta G_\text{solv}$ from those (this skill uses ONE geometry for both); for research-grade values use DFT with a continuum model including non-electrostatic terms (e.g. SMD).


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

## Examples
```bash
# Env: anl_env
python skills/solvation/scripts/solvation.py --method xtb --solvent water mol.xyz
```
See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required for all calls.
- **Fixed geometry**: no optimization; one geometry is used for both gas and solvated points, ignoring relaxation in solvent.
- **Method and solvent required**: both `--method` and `--solvent` must be supplied.
- **Electronic only**: no cavitation, dispersion-repulsion, or thermal corrections; screening-grade. DFT uses ddCOSMO (no SMD).
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; compare to experiment only if the user explicitly asks.
- **Availability**: xtb-python / MOPAC via `conda install -c conda-forge xtb-python mopac`; pyscf via `pip install pyscf` (required for `--method dft` or `--method hf`). For an unknown solvent, check the list above.

## References
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "Optimization of parameters for semiempirical methods VI: PM7." *J. Mol. Model.* 2013, 19, 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. "Recent developments in the PySCF program package." *J. Chem. Phys.* 2020, 153, 024109. https://doi.org/10.1063/5.0006074
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the energy-unit conversions (Hartree↔eV, 1 Eh = 27.211386245981 eV; eV→kcal/mol, 23.060547830619) used to report solvation free energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
