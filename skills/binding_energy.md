---
description: Binding / Interaction Energy — When the user wants the binding/interaction energy between a complex and its fragments (e.g. "binding energy", "interaction energy", "ΔE_bind", "host-guest binding", "ligand binding", "dimerization energy", "complexation energy"). Inputs are the complex xyz plus the fragment xyz files.
---

# Binding / Interaction Energy

Compute ΔE_bind = E(complex) − Σ E(monomers).

## Arguments
`$ARGUMENTS` should include:
- The complex `.xyz` path (required)
- One or more `--monomer <path.xyz>` arguments (required, ≥2)
- A method: `xtb` or `mopac` (required)
- Optional: `--solvent`, `--charge`, `--mult` (apply to the complex),
  `--monomer-charge N` / `--monomer-mult N` (repeat per monomer)

## Steps
1. Parse args. If method missing, AskUserQuestion. Monomer paths required — stop and ask if missing.
2. Run `chemkit binding --method <M> --monomer <m1> --monomer <m2> [...] <COMPLEX>`.
3. Read JSON, copy to `<basename>_binding_<method>.json`.
4. Report:
   - **Binding energy** in eV, kcal/mol, Hartree (negative = stable complex)
   - E(complex), E(monomer1), E(monomer2), ...
   - Warning: no BSSE correction; geometries used as-supplied.

## Recommendation
Run `/geometry_optimize` on the complex and each monomer separately before calling this — otherwise the "binding energy" is contaminated by deformation energy.
