---
name: single-point-energy
description: Computes the total electronic energy and frontier-orbital properties of a molecule at a fixed geometry without optimizing it.
category: chemistry
---

# Single-Point Energy

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
Evaluate the total electronic energy $E$ at a single fixed geometry, plus frontier-orbital data (HOMO, LUMO, gap). No relaxation — for the relaxed minimum use [geometry-optimize](../geometry-optimize/SKILL.md).

## Instructions
A thin MCP-client script dispatches to the engine's `sp` subcommand.

```bash
# Env: anl_env
python skills/single-point-energy/scripts/single-point-energy.py --method <xtb|mopac|dft|hf> [args] input.xyz
```

1. **Input geometry** — an `.xyz` path is required; if missing, stop and ask.
2. **`--method`** (required; if missing, ask):
   - `xtb` — GFN2-xTB, fast semi-empirical
   - `mopac` — PM7, fast semi-empirical
   - `dft` — DFT via PySCF (tier presets or explicit functional/basis)
   - `hf` — Hartree-Fock via PySCF (basis only)
3. **All methods:** `--solvent <name>` (water, methanol, dmso, …), `--charge N`, `--mult N` (alias `--multiplicity`), `--out <path>` (result JSON; default `<stem>_sp_<method>.json` in the run cwd).
4. **DFT only:** `--tier {fast,standard,accurate}` (default `standard`), `--functional <libxc>`, `--basis <name>`. Tiers:
   - `fast`: r²SCAN / def2-SVP — screening, large systems
   - `standard`: B3LYP / def2-TZVP — production default (widely used hybrid GGA)
   - `accurate`: ωB97M-V / def2-QZVPP — benchmark-quality

   `--functional`/`--basis` override the tier. Anions (charge < 0) auto-promote to a diffuse basis (def2-tzvp → def2-tzvpd). D3/D4 functionals (e.g. `wb97x-d3bj`) need the `pyscf-dispersion` add-on; default tiers use VV10 and don't. **`--density-fit`** enables RI density fitting (~3-10x faster SCF, ~0.1-0.8 mEh error); OFF by default — chemkit uses exact integrals (plain RKS/UKS, matching hand-run PySCF).
5. **HF only:** `--basis <name>` (default `def2-tzvp`).
6. **Read the JSON** and report:
   - Total electronic energy (eV, Hartree, kcal/mol)
   - HOMO / LUMO / gap from `code_specific` (all backends populate these)
   - `mopac`: also heat of formation (`code_specific.heat_of_formation_kcal_mol`), dipole, IP
   - `dft`/`hf`: also functional, basis, tier, dipole (Debye), SCF cycles
   - Solvent (or "gas phase"), charge, multiplicity, and the saved JSON path (`--out`, default `<stem>_sp_<method>.json`)
   - Every warning from the result JSON, reproduced verbatim — none dropped, summarized, or paraphrased; if there are no warnings, say so


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

> **Skill name / discovery.** This skill's engine subcommand is `sp`; the names `single-point-energy`, `single-point`, `singlepoint` are accepted aliases — any of them work. Do **not** invent flags: gas phase is the default (or `--solvent none`); there is no `--phase`/`--environment` flag, and the geometry is the positional argument, not `--geometry`/`--xyz`/`--input`. If unsure of the exact name or flags, run `chemkit --list-skills` or `chemkit single-point-energy --help-json` (or `--help`) to discover them instead of guessing.

## Examples
```bash
# Env: anl_env
python skills/single-point-energy/scripts/single-point-energy.py --method xtb --solvent water water.xyz
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required for all script calls.
- `xtb` (GFN2-xTB) and `mopac` (PM7) are semi-empirical; `dft`/`hf` run via PySCF.
- Solvent treatment is implicit only.
- **Energy zeros differ across backends** — only same-method energies are comparable.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; do not volunteer accepted/measured/reference values or editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks.
- Errors: `xtb`/`mopac` missing → `conda install -c conda-forge xtb mopac`; `pyscf` missing → `pip install pyscf` (needed for `--method dft`/`hf`); malformed `.xyz` → report which line failed.

## References
- Bannwarth, C.; Ehlert, S.; Grimme, S. "GFN2-xTB", *J. Chem. Theory Comput.* **2019**, 15 (3), 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart, J. J. P. "Optimization of parameters for semiempirical methods VI (PM7)", *J. Mol. Model.* **2013**, 19 (1), 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun, Q.; et al. "Recent developments in the PySCF program package", *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- Mardirossian, N.; Head-Gordon, M. "ωB97X-V", *Phys. Chem. Chem. Phys.* **2014**, 16, 9904-9924. https://doi.org/10.1039/C3CP54374A
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the unit conversions this skill reports — Hartree↔eV (1 Eh = 27.211386245981 eV), Hartree→kcal/mol (627.5094740629), and ea₀→Debye (2.541746471) for the dipole.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
