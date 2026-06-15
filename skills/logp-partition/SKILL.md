---
name: logp-partition
description: Estimates the octanol-water partition coefficient of a neutral molecule from a solvation-free-energy thermodynamic cycle.
category: chemistry
---

# Logp Partition

## Goal
Estimate $\log P$ from $\Delta G_\text{solv}(\text{water}) - \Delta G_\text{solv}(\text{octanol})$ at 298.15 K via three single-point energies (gas, water, octanol) on the supplied geometry. Screening-grade (±1 log unit typical at semi-empirical level). Sign convention: positive $\log P$ → prefers octanol (lipophilic). Defined for neutral species only.

## Instructions
1. Parse arguments. If the `.xyz` path is missing, stop and ask. If `--method` is missing, use **AskUserQuestion**. If `--charge != 0` was passed, stop and explain logD (see Constraints).
2. Run the engine.

```bash
# Env: anl_env
python skills/logp-partition/scripts/logp-partition.py --method <xtb|mopac|dft|hf> [--mult N] [--tier <T>] [--functional <F>] [--basis <B>] input.xyz
```

Arguments:
- `input.xyz` — geometry of the **neutral** molecule (required).
- `--method {xtb,mopac,dft,hf}` — **required**.
- `--mult N` — spin multiplicity (default 1, closed-shell neutral).
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`.
- HF-only: `--basis <name>`.
- `--out <path>` (result JSON; default `<stem>_logp_<method>.json` in the run cwd).

DFT $\log P$ is slower but meaningfully better than semi-empirical for polar / H-bonding scaffolds. The ddCOSMO model in PySCF lacks cavitation / dispersion-repulsion terms (no SMD parameterization for octanol), so DFT $\log P$ here is still screening-grade — better than xtb/mopac but not a substitute for SMD-parameterized DFT.

Read the result JSON, written to `--out` (default `<stem>_logp_<method>.json` in the run cwd). Report: $\log P$ (headline number); $\Delta G_\text{solv}(\text{water})$ and $\Delta G_\text{solv}(\text{octanol})$, both in kcal/mol; $\Delta\Delta G$ (water − octanol); method and charge/multiplicity (charge is always 0); and the caveats (±1 log unit; electronic $\Delta G_\text{solv}$ only). Flag any JSON warnings. For a single-solvent value on an ionization state, see [solvation](../solvation/SKILL.md). For a fast chemoinformatic comparison, RDKit's `Crippen.MolLogP` (group-contribution) is often comparably accurate for drug-like molecules; the thermodynamic-cycle approach here has the advantage of reflecting the actual electronic structure / conformation for unusual scaffolds.

## Examples
```bash
# Env: anl_env
python skills/logp-partition/scripts/logp-partition.py --method xtb mol.xyz
```
See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required for all calls.
- **Neutral species only**: refuses `--charge != 0`. $\log P$ is defined for the neutral species; for ionizable molecules at physiological pH the user wants **logD** (pH-dependent, out of scope). Offer to compute $\Delta G_\text{solv}$ in water via [solvation](../solvation/SKILL.md) on the relevant ionization state instead.
- **Fixed geometry**: no optimization; pass an already-relaxed structure.
- **Method required**: `--method` must be supplied (ask the user if absent).
- **Screening-grade**: electronic $\Delta G_\text{solv}$ only; DFT uses ddCOSMO (no SMD for octanol).
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; compare to experiment only if the user explicitly asks.
- **Availability**: xtb-python / MOPAC via `conda install -c conda-forge xtb-python mopac`; pyscf via `pip install pyscf` (required for `--method dft` or `--method hf`).

## References
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "Optimization of parameters for semiempirical methods VI: PM7." *J. Mol. Model.* 2013, 19, 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. "Recent developments in the PySCF program package." *J. Chem. Phys.* 2020, 153, 024109. https://doi.org/10.1063/5.0006074
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the energy-unit conversions (Hartree↔eV, 1 Eh = 27.211386245981 eV; eV→kcal/mol, 23.060547830619) used in the partition free-energy estimate.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
