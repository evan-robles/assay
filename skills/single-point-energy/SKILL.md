---
name: single-point-energy
description: Computes the total electronic energy and frontier-orbital properties of a molecule at a fixed geometry without optimizing it.
category: chemistry
---

# Single-Point Energy

## Goal
Evaluate the total electronic energy $E$ of a molecule at a single, fixed nuclear geometry, along with frontier-orbital data (HOMO, LUMO, gap). No geometry relaxation is performed — for the relaxed minimum-energy structure, use [geometry-optimize](../geometry-optimize/SKILL.md) instead.

## Instructions
The user invokes this skill through a thin MCP-client script that dispatches to the `sp` subcommand of the chemistry engine.

```bash
# Env: anl_env
python skills/single-point-energy/scripts/single-point-energy.py --method <xtb|mopac|dft|hf> [other args] input.xyz
```

1. **Provide the input geometry.** An `.xyz` path is required. If it is missing, stop and ask the user.
2. **Choose a method** (required — if missing, ask the user):
   - `xtb` — GFN2-xTB, fast semi-empirical
   - `mopac` — PM7, fast semi-empirical
   - `dft` — ab initio DFT via PySCF (tier presets or explicit functional/basis)
   - `hf` — Hartree-Fock via PySCF (basis only)
3. **Common optional arguments** (all methods): `--solvent <name>` (water, methanol, dmso, ...), `--charge N`, `--mult N`.
4. **DFT-only arguments:** `--tier {fast,standard,accurate}` (default `standard`), `--functional <libxc>`, `--basis <name>`. Tiers:
   - `fast`: r²SCAN / def2-SVP — screening, large systems
   - `standard`: ωB97X-V / def2-TZVP — production default (VV10 dispersion, no add-on)
   - `accurate`: ωB97M-V / def2-QZVPP — benchmark-quality

   `--functional`/`--basis` override the tier defaults. Anions (charge < 0) auto-promote to a diffuse basis (def2-tzvp → def2-tzvpd). For D3/D4-corrected functionals (e.g. `--functional wb97x-d3bj`) install the optional `pyscf-dispersion` add-on; default tiers use VV10 and don't need it.
5. **HF-only argument:** `--basis <name>` (default `def2-tzvp`).
6. **Read the returned JSON** and report:
   - **Total electronic energy** (eV, Hartree, kcal/mol)
   - **HOMO / LUMO / gap** from `code_specific` (every backend populates these)
   - For `mopac`: also heat of formation (`code_specific.heat_of_formation_kcal_mol`), dipole, IP
   - For `dft`/`hf`: also functional, basis, tier, dipole (Debye), SCF cycles
   - Solvent (or "gas phase"), charge, multiplicity, and the path to the saved JSON

## Examples
```bash
# Env: anl_env
python skills/single-point-energy/scripts/single-point-energy.py --method xtb --solvent water water.xyz
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` is required for all script calls.
- `xtb` (GFN2-xTB) and `mopac` (PM7) are semi-empirical; `dft` and `hf` run via PySCF.
- Solvent treatment is implicit only.
- **Energy zeros differ across backends** — only same-method energies are directly comparable.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; do not volunteer accepted/measured/reference values or editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.
- Errors: `xtb`/`mopac` not installed → `conda install -c conda-forge xtb mopac`; `pyscf` not installed → `pip install pyscf` (required for `--method dft` or `--method hf`); malformed `.xyz` → report which line failed.

## References
- Bannwarth, C.; Ehlert, S.; Grimme, S. "GFN2-xTB", *J. Chem. Theory Comput.* **2019**, 15 (3), 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart, J. J. P. "Optimization of parameters for semiempirical methods VI (PM7)", *J. Mol. Model.* **2013**, 19 (1), 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun, Q.; et al. "Recent developments in the PySCF program package", *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- Mardirossian, N.; Head-Gordon, M. "ωB97X-V", *Phys. Chem. Chem. Phys.* **2014**, 16, 9904-9924. https://doi.org/10.1039/C3CP54374A
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the unit conversions this skill reports — Hartree↔eV (1 Eh = 27.211386245981 eV), Hartree→kcal/mol (627.5094740629), and ea₀→Debye (2.541746471) for the dipole.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
