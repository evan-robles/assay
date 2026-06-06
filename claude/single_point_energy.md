---
description: Single-Point Energy — When the user wants the energy of a molecule at a fixed geometry without optimizing (e.g. "single point", "sp", "evaluate this geometry", "what's the energy of this structure"). Do NOT use when the user wants the relaxed/minimum-energy structure — that's geometry_optimize.
---

# Single-Point Energy

Compute the absolute electronic total energy of a molecule with xtb (GFN2) or MOPAC (PM7).

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required)
- A method: `xtb` or `mopac` (required — if missing, use **AskUserQuestion**)
- Optional: `--solvent <name>` (water, methanol, dmso, ...), `--charge N`, `--mult N`

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion (header "Method", options `xtb` / `mopac`).
2. Run `chemkit sp --method <METHOD> [--solvent <S>] [--charge <Q>] [--mult <M>] <XYZ>`.
3. Read the printed JSON. Copy the JSON result to `<basename>_sp_<method>.json` in the cwd.
4. Report to the user:
   - **Total electronic energy** (eV, Hartree, kcal/mol)
   - **HOMO / LUMO / gap** from `code_specific` (both xtb and MOPAC populate `homo_eV`, `lumo_eV`, `homo_lumo_gap_eV`)
   - For MOPAC: also heat of formation, dipole, IP
   - Solvent (or "gas phase"), charge, multiplicity
   - Path to the JSON output
   - The note that xtb and MOPAC energy zeros differ — only same-method differences are comparable.
5. If `code_specific.heat_of_formation_kcal_mol` is also in the JSON, surface it.

## Errors
- xtb / mopac not installed → install via `conda install -c conda-forge xtb mopac`.
- Malformed `.xyz` → report which line failed.
