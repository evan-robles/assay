---
name: electrostatics
description: Computes a molecule's dipole moment and atomic partial charges at a fixed geometry.
category: chemistry
---

# Electrostatics

> [!IMPORTANT]
> **Before running — confirm the level of theory; never guess.** If the user did
> not specify `--method` (xtb | mopac | dft | hf) — and, where relevant,
> `--functional`/`--basis`/`--tier`, `--solvent` (or explicit gas phase),
> `--charge`, `--mult` — **stop and ask the user** (do not silently default or
> carry over the previous run's choice). The engine refuses a DFT/HF run that
> omits the consequential knobs unless you pass `--accept-defaults`.
> **At launch, immediately give the user the live `.out` log path and offer
> `tail -f`** — do not wait for the run to finish. (calculation-reporting-standards
> non-negotiables #10 and #9.)

## Goal
Compute the dipole moment (magnitude and Cartesian vector, in Debye) and atomic partial charges (Mulliken for every backend) on the supplied geometry, with optional implicit solvent. No geometry optimization is performed.

## Instructions
1. Parse arguments. If the `.xyz` path is missing, stop and ask. If `--method` is missing, use **AskUserQuestion** (header "Method", options `xtb` / `mopac` / `dft` / `hf`).
2. Run the engine.

```bash
# Env: anl_env
python skills/electrostatics/scripts/electrostatics.py --method <xtb|mopac|dft|hf> [--tier <T>] [--functional <F>] [--basis <B>] [--solvent <S>] [--charge N] [--mult N] [--out <path>] input.xyz
```

Arguments:
- `input.xyz` — molecular geometry (required).
- `--method {xtb,mopac,dft,hf}` — **required**.
- `--solvent <name>` — implicit solvent (water, methanol, dmso, mecn, dcm, …).
- `--charge N`, `--mult N` — molecular charge and spin multiplicity.
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. **`--density-fit`** enables RI density fitting (~3-10x faster SCF, ~0.1-0.8 mEh error); OFF by default — chemkit uses exact integrals (plain RKS/UKS, matching hand-run PySCF).
- HF-only: `--basis <name>`.
- `--out <path>` — result JSON (default `<stem>_electrostatics_<method>.json` in the run cwd).

Read the JSON — it is already written to `--out` (default `<stem>_electrostatics_<method>.json` in the run cwd). Report: the dipole moment in Debye (magnitude + Cartesian vector); atomic partial charges as a table (1-based atom index, element symbol, charge); the sum of charges as a sanity check (should match the total molecular charge); method, solvent (or "gas phase"), and molecular charge/multiplicity; the partitioning scheme (Mulliken for every backend); and every warning from the result JSON, reproduced verbatim — none dropped, summarized, or paraphrased; if there are no warnings, say so. Mulliken charges are basis-set-dependent and not a physical observable — transferable charges need ESP-fit methods (not available in this build). This is a single point; relax the geometry first with [geometry-optimize](../geometry-optimize/SKILL.md) if needed.


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

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
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the ea₀→Debye conversion (2.541746471) used to report the dipole moment.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
