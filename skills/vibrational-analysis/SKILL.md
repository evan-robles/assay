---
name: vibrational-analysis
description: Computes vibrational frequencies, zero-point energy, and thermochemistry and verifies whether a geometry is a minimum or a transition state.
category: chemistry
---

# Vibrational Analysis

## Goal
Optimize the input geometry, then build the Hessian to obtain vibrational frequencies, zero-point energy (ZPE), and thermochemical quantities — enthalpy $H$, entropy $S$, Gibbs free energy $G$, and heat capacity $C_p$ — at temperature $T$ and pressure $P$. The frequency spectrum also classifies the stationary point as a minimum (no imaginary modes) or a saddle point / transition state (one or more imaginary modes).

## Instructions
The user invokes this skill through a thin MCP-client script that dispatches to the `freq` subcommand of the chemistry engine. A pre-optimization is automatic and on by default — taking the Hessian at a true stationary point eliminates spurious imaginary modes from residual gradient.

```bash
# Env: anl_env
python skills/vibrational-analysis/scripts/vibrational-analysis.py --method <xtb|mopac|dft|hf> [other args] input.xyz
```

1. **Provide the input geometry.** An `.xyz` path is required; it does NOT need to be pre-optimized (the freq step optimizes it first by default). If missing, ask the user.
2. **Choose a method** (required — if missing, ask the user): `xtb`, `mopac`, `dft`, or `hf`.
3. **Common optional arguments:** `--solvent`, `--charge`, `--mult`, `--temperature <K>` (default 298.15), `--pressure <Pa>` (default 101325).
4. **Partition-function controls** (xtb/dft/hf `IdealGasThermo` path; MOPAC detects these internally): `--geometry {linear,nonlinear,monatomic}` (default nonlinear), `--symmetry <σ>` (rotational symmetry number, default 1).
5. **Pre-optimization controls:** `--no-preopt` skips the automatic opt (use only when the input is already converged at the same method, e.g. you just ran [geometry-optimize](../geometry-optimize/SKILL.md) with the same method); `--preopt-fmax <eV/Å>` (default 0.01, tighter than opt's 0.05, since residual forces propagate into near-zero imaginary modes).
6. **DFT-only:** `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. **HF-only:** `--basis <name>`. Hessians via PySCF cost ~(6N+1)× the SCF time — default to `--tier fast` for screening, reserve `standard` for the final answer.
7. **Read the returned JSON** and report:
   - From the `preopt` block: whether the pre-opt converged, number of opt steps, and the pre-opt energy/HoF (it records the path to the optimized xyz used for the Hessian). If `--no-preopt` was passed, say so.
   - ZPE, enthalpy (H), entropy (S), Gibbs free energy (G), in both eV and kcal/mol where the schema provides both.
   - Number of real / imaginary modes — **warn loudly** if any imaginary modes remain after the pre-opt.
   - Frequency list (cm⁻¹) — top 10 + lowest 10 if there are many.
   - If imaginary modes remain after pre-opt, the geometry is a true saddle point (TS or higher-order); suggest exploring along the imaginary normal mode or searching for a nearby minimum.

## Examples
```bash
# Env: anl_env
python skills/vibrational-analysis/scripts/vibrational-analysis.py --method xtb --temperature 298.15 --symmetry 2 water.xyz
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` is required for all script calls.
- `xtb` (GFN2-xTB) and `mopac` (PM7) are semi-empirical; `dft` and `hf` run via PySCF.
- The pre-opt uses the same method as the freq step, so the optimized geometry is consistent with the Hessian. Mixing methods (e.g. xtb opt + mopac freq) is the classic way to get apparent imaginary modes — avoid it.
- ASE's `IdealGasThermo` (xtb path) assumes ideal gas; gas-phase or implicit-solvent only. The user must supply correct `--geometry` and `--symmetry` for correct rotational/translational partition functions.
- Energy zeros differ across backends — only same-method energies are directly comparable.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; do not volunteer accepted/measured/reference values or editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- Errors: `xtb`/`mopac` not installed → `conda install -c conda-forge xtb mopac`; `pyscf` not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).

## References
- Bannwarth, C.; Ehlert, S.; Grimme, S. "GFN2-xTB", *J. Chem. Theory Comput.* **2019**, 15 (3), 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart, J. J. P. "Optimization of parameters for semiempirical methods VI (PM7)", *J. Mol. Model.* **2013**, 19 (1), 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun, Q.; et al. "Recent developments in the PySCF program package", *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- Larsen, A. H.; et al. "The Atomic Simulation Environment (ASE)", *J. Phys.: Condens. Matter* **2017**, 29, 273002. https://doi.org/10.1088/1361-648X/aa680e
- Mardirossian, N.; Head-Gordon, M. "ωB97X-V", *Phys. Chem. Chem. Phys.* **2014**, 16, 9904-9924. https://doi.org/10.1039/C3CP54374A

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
