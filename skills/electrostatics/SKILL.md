---
name: electrostatics
description: Computes a molecule's dipole moment and atomic partial charges at a fixed geometry.
category: chemistry
---

# Electrostatics

## Goal
Compute the dipole moment (magnitude and Cartesian vector, in Debye) and atomic partial charges (Mulliken for every backend) on the supplied geometry, with optional implicit solvent. No geometry optimization is performed.

## Instructions
1. Parse arguments. If the `.xyz` path is missing, stop and ask. If `--method` is missing, use **AskUserQuestion** (header "Method", options `xtb` / `mopac` / `dft` / `hf`).
2. Run the engine.

```bash
# Env: anl_env
python skills/electrostatics/scripts/electrostatics.py --method <xtb|mopac|dft|hf> [--tier <T>] [--functional <F>] [--basis <B>] [--solvent <S>] [--charge N] [--mult N] input.xyz
```

Arguments:
- `input.xyz` — molecular geometry (required).
- `--method {xtb,mopac,dft,hf}` — **required**.
- `--solvent <name>` — implicit solvent (water, methanol, dmso, mecn, dcm, …).
- `--charge N`, `--mult N` — molecular charge and spin multiplicity.
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`.
- HF-only: `--basis <name>`.

Read the printed JSON and copy it to `<basename>_electrostatics_<method>.json` in the cwd. Report: the dipole moment in Debye (magnitude + Cartesian vector); atomic partial charges as a table (1-based atom index, element symbol, charge); the sum of charges as a sanity check (should match the total molecular charge); method, solvent (or "gas phase"), and molecular charge/multiplicity; and the partitioning scheme (Mulliken for every backend). Note that Mulliken charges are basis-set-dependent and not a physical observable — for transferable charges, ESP-fit methods would be needed (not available in this build). This is a single point; relax the geometry first with [geometry-optimize](../geometry-optimize/SKILL.md) if needed.

## Examples
```bash
# Env: anl_env
python skills/electrostatics/scripts/electrostatics.py --method xtb --solvent water mol.xyz
```
See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required for all calls.
- **Fixed geometry**: no optimization is performed; pass an already-relaxed structure.
- **Method required**: `--method` must be supplied (ask the user if absent).
- **Charge scheme**: Mulliken only; basis-set-dependent and not a physical observable. ESP-fit charges are not available in this build.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; compare to experiment only if the user explicitly asks.
- **Availability**: xtb-python via `conda install -c conda-forge xtb-python` or `pip install xtb`; mopac via `conda install -c conda-forge mopac`; pyscf via `pip install pyscf` (required for `--method dft` or `--method hf`).

## References
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "Optimization of parameters for semiempirical methods VI: PM7." *J. Mol. Model.* 2013, 19, 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. "Recent developments in the PySCF program package." *J. Chem. Phys.* 2020, 153, 024109. https://doi.org/10.1063/5.0006074

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
