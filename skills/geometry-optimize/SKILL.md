---
name: geometry-optimize
description: Relaxes a molecular structure to a local minimum on the potential energy surface to obtain its equilibrium geometry.
category: chemistry
---

# Geometry Optimize

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
Relax an input molecular structure to a local minimum on the chosen potential energy surface, returning the equilibrium geometry and its final energy $E$. For energy-only evaluation at a fixed geometry, use [single-point-energy](../single-point-energy/SKILL.md); for verifying the minimum and obtaining thermochemistry, use [vibrational-analysis](../vibrational-analysis/SKILL.md).

## Instructions
A thin MCP-client script dispatches to the engine's `opt` subcommand.

```bash
# Env: anl_env
python skills/geometry-optimize/scripts/geometry-optimize.py --method <xtb|mopac|dft|hf> [other args] input.xyz
```

1. **Input geometry** — an `.xyz` path is required; if missing, stop and ask.
2. **`--method`** (required; if missing, ask):
   - `xtb` — GFN2-xTB, fast semi-empirical, ASE BFGS
   - `mopac` — PM7, fast semi-empirical, MOPAC's native EF optimizer
   - `dft` — ab initio DFT via PySCF, ASE BFGS with analytic gradients
   - `hf` — Hartree-Fock via PySCF, ASE BFGS with analytic gradients
3. **All methods:** `--solvent <name>`, `--charge N`, `--mult N`, `--fmax <eV/Å>` (default 0.05), `--steps N` (default 500, max optimizer iterations), `--xyz-out <path>` (relaxed-geometry destination; default `<stem>_<method>_opt.xyz`), `--out <path>` (result JSON; default `<stem>_opt_<method>.json` in the run cwd).
4. **DFT-only:** `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. **HF-only:** `--basis <name>`. **`--density-fit`** enables RI density fitting (~3-10x faster SCF, ~0.1-0.8 mEh error); OFF by default — chemkit uses exact integrals (plain RKS/UKS, matching hand-run PySCF).
5. **Cost.** DFT optimizations are 10–100× slower than xtb. Default to `--tier fast` (r²SCAN/def2-SVP) for first-pass relaxation, then re-optimize at `--tier standard` if needed. For very flexible molecules, pre-optimize at `--method xtb` first.
6. **Read the JSON** and report:
   - Whether the optimization converged. For `xtb`/`dft`/`hf` include the BFGS step count (`n_steps`); for `mopac` include `mopac_status` and `mopac_gradient_norm_kcal_per_A` (native EF optimizer — `n_steps` is not reported).
   - Final total energy (and `final_heat_of_formation_kcal_mol` for `mopac`).
   - For `dft`/`hf`: functional, basis, tier.
   - Path to the optimized `.xyz` (`--xyz-out`, default `<stem>_<method>_opt.xyz`; paste its contents in a fenced block) and to the result JSON (`--out`, default `<stem>_opt_<method>.json`).
   - Any `warnings` entries verbatim. If not converged, still deliver the last geometry and flag `converged: false` prominently.


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

## Examples
```bash
# Env: anl_env
python skills/geometry-optimize/scripts/geometry-optimize.py --method xtb --fmax 0.02 caffeine.xyz
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` is required for all script calls.
- `xtb` (GFN2-xTB) and `mopac` (PM7) are semi-empirical; `dft` and `hf` run via PySCF with analytic gradients.
- Solvent treatment is implicit only. Energy zeros differ across backends — only same-method energies are directly comparable.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; do not volunteer accepted/measured/reference values or editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- Errors: non-convergence → still deliver the last geometry and flag `converged: false`; `pyscf` not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).

## References
- Bannwarth, C.; Ehlert, S.; Grimme, S. "GFN2-xTB", *J. Chem. Theory Comput.* **2019**, 15 (3), 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart, J. J. P. "Optimization of parameters for semiempirical methods VI (PM7)", *J. Mol. Model.* **2013**, 19 (1), 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun, Q.; et al. "Recent developments in the PySCF program package", *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- Larsen, A. H.; et al. "The Atomic Simulation Environment (ASE)", *J. Phys.: Condens. Matter* **2017**, 29, 273002. https://doi.org/10.1088/1361-648X/aa680e
- Mardirossian, N.; Head-Gordon, M. "ωB97X-V", *Phys. Chem. Chem. Phys.* **2014**, 16, 9904-9924. https://doi.org/10.1039/C3CP54374A
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV conversion (1 Eh = 27.211386245981 eV) used to report energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
