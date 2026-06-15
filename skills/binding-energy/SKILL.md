---
name: binding-energy
description: Computes the binding or interaction energy between a molecular complex and its constituent fragments.
category: chemistry
---

# Binding Energy

## Goal
Compute the binding (interaction) energy of a molecular complex relative to its separated fragments, $\Delta E_\text{bind} = E_\text{complex} - \sum_i E_{\text{monomer},i}$, where a negative value indicates a stable complex. Applicable to host-guest, ligand, dimerization, and other non-covalent or covalent association problems.

## Instructions
A thin MCP-client script dispatches to the engine's `binding` subcommand.

```bash
# Env: anl_env
python skills/binding-energy/scripts/binding-energy.py --method <xtb|mopac|dft|hf> --monomer m1.xyz --monomer m2.xyz [other args] complex.xyz
```

1. **Inputs.** The complex `.xyz` path is required, plus one or more `--monomer <path.xyz>` (≥2 required). If any path is missing, stop and ask.
2. **Method** (required — if missing, ask): `xtb`, `mopac`, `dft`, or `hf`.
3. **Optional arguments:** `--solvent`, `--charge`, `--mult` (apply to the complex); `--monomer-charge N` / `--monomer-mult N` (repeat once per monomer); `--out <path>` (result JSON; default `<complex-stem>_binding_<method>.json` in the run cwd). **DFT-only:** `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. **HF-only:** `--basis <name>`.
4. **Pick a method that captures dispersion for non-covalent complexes** (H-bonds, π-stacking, host-guest). `--method dft --tier standard` (ωB97X-V) and `--tier accurate` (ωB97M-V) both include VV10 nonlocal correlation. Bare HF does not, so HF binding energies for non-covalent systems are systematically too repulsive.
5. **Pre-optimize first.** Run [geometry-optimize](../geometry-optimize/SKILL.md) on the complex and on each monomer separately before calling this skill — otherwise the binding energy is contaminated by fragment deformation energy.
6. **Read the JSON** and report:
   - **Binding energy** in eV, kcal/mol, and Hartree (negative = stable complex).
   - E(complex), E(monomer1), E(monomer2), ...
   - Warning: no BSSE correction; geometries are used as-supplied.
   - The saved JSON path (`--out`, default `<complex-stem>_binding_<method>.json`).


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

## Examples
```bash
# Env: anl_env
python skills/binding-energy/scripts/binding-energy.py --method dft --tier standard --monomer water.xyz --monomer water.xyz water_dimer.xyz
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` is required for all script calls.
- `xtb` (GFN2-xTB) and `mopac` (PM7) are semi-empirical; `dft` and `hf` run via PySCF.
- No BSSE (basis set superposition error) correction is applied; geometries are used as-supplied — pre-optimize fragments to avoid deformation contamination.
- HF lacks dispersion and over-repels non-covalent complexes; use DFT tiers with VV10 for those.
- Solvent treatment is implicit only. All fragment energies must be computed with the same method for the difference to be meaningful.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; do not volunteer accepted/measured/reference values or editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- Errors: `xtb`/`mopac` not installed → `conda install -c conda-forge xtb mopac`; `pyscf` not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).

## References
- Bannwarth, C.; Ehlert, S.; Grimme, S. "GFN2-xTB", *J. Chem. Theory Comput.* **2019**, 15 (3), 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart, J. J. P. "Optimization of parameters for semiempirical methods VI (PM7)", *J. Mol. Model.* **2013**, 19 (1), 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun, Q.; et al. "Recent developments in the PySCF program package", *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- Mardirossian, N.; Head-Gordon, M. "ωB97X-V", *Phys. Chem. Chem. Phys.* **2014**, 16, 9904-9924. https://doi.org/10.1039/C3CP54374A
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV (1 Eh = 27.211386245981 eV) and eV→kcal/mol (23.060547830619) conversions used to report interaction energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
