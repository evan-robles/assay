---
description: Single-Point Energy — When the user wants the energy of a molecule at a fixed geometry without optimizing (e.g. "single point", "sp", "evaluate this geometry", "what's the energy of this structure"). Do NOT use when the user wants the relaxed/minimum-energy structure — that's geometry_optimize.
---

# Single-Point Energy

Compute the total electronic energy of a molecule with one of four backends:
- `xtb` (GFN2-xTB) — fast semi-empirical
- `mopac` (PM7) — fast semi-empirical
- `dft` — ab initio DFT via PySCF (tier presets or explicit functional/basis)
- `hf` — Hartree-Fock via PySCF (basis only)

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- `--method {xtb,mopac,dft,hf}` (required — if missing, use **AskUserQuestion**)
- All methods: `--solvent <name>` (water, methanol, dmso, ...), `--charge N`, `--mult N`
- `dft` only: `--tier {fast,standard,accurate}` (default `standard`), `--functional <libxc>`, `--basis <name>`
- `hf` only: `--basis <name>` (default `def2-tzvp`)

## DFT tiers
- `fast`: r²SCAN / def2-SVP — screening, large systems
- `standard`: ωB97X-V / def2-TZVP — production default (VV10 dispersion, no add-on)
- `accurate`: ωB97M-V / def2-QZVPP — benchmark-quality

`--functional`/`--basis` override the tier defaults. Anions (charge < 0) auto-promote to diffuse basis (def2-tzvp → def2-tzvpd, etc.). For D3/D4-corrected functionals (e.g. `--functional wb97x-d3bj`) install the optional `pyscf-dispersion` add-on; the default tiers use VV10 and don't need it.

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion (header "Method", options `xtb` / `mopac` / `dft` / `hf`).
2. Run `chemkit sp --method <METHOD> [--tier <T>] [--functional <F>] [--basis <B>] [--solvent <S>] [--charge <Q>] [--mult <M>] <XYZ>`.
3. Read the printed JSON. Copy the JSON result to `<basename>_sp_<method>.json` in the cwd.
4. Report to the user:
   - **Total electronic energy** (eV, Hartree, kcal/mol)
   - **HOMO / LUMO / gap** from `code_specific` (every backend populates these)
   - For `mopac`: also heat of formation, dipole, IP
   - For `dft`/`hf`: also functional, basis, tier, dipole (Debye), SCF cycles
   - Solvent (or "gas phase"), charge, multiplicity
   - Path to the JSON output
   - Note: energy zeros differ across backends — only same-method energies are directly comparable.
5. If `code_specific.heat_of_formation_kcal_mol` is also in the JSON, surface it.

## Errors
- xtb / mopac not installed → `conda install -c conda-forge xtb mopac`.
- pyscf not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).
- Malformed `.xyz` → report which line failed.
