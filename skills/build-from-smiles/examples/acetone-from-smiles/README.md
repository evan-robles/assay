# Example: Build Acetone 3D Structure from SMILES (Open Babel)

## Goal
Generate a force-field-quality 3D starting geometry for acetone from its SMILES
string `CC(=O)C`. No quantum-mechanical property is computed — this produces the
starting `.xyz` that downstream chemkit skills consume.

## Calculation run
- **Skill:** build-from-smiles (SMILES-only)
- **Method:** Open Babel `--gen3d` (force-field 3D embedding); program: openbabel — no QM method
- **Basis/functional:** not applicable
- **Charge/multiplicity:** 0 / 1 (obabel writes geometry only; charge is not inferred)
- **Solvent:** not applicable
- **Input:** SMILES `CC(=O)C`

```bash
# Env: anl_env
python skills/build-from-smiles/scripts/build-from-smiles.py 'CC(=O)C' \
    --out-xyz acetone.xyz --out acetone_build.json --name acetone
```

Generated files: [`acetone_build.json`](acetone_build.json), [`acetone.xyz`](acetone.xyz)

## Result (this calculation)

| Property | Value | Notes |
|---|---|---|
| Input SMILES | `CC(=O)C` | passed directly; parsed by Open Babel |
| Atom count | 10 | 3 C + 6 H + 1 O |
| Molecular formula | C3H6O | consistent with acetone |
| 3D structure | built | Open Babel `--gen3d`, force-field quality |

The exact `obabel` command is recorded in the JSON under `build.command`, and the
input SMILES under `smiles_input`. No `warnings` were reported and the result is
marked trustworthy.

## Identity check

| Check | Result | Verdict |
|---|---|---|
| Formula from built geometry | C3H6O, 10 atoms | Correctly corresponds to acetone (`CC(=O)C`). |

This skill produces a starting geometry, not a measured property, so there is no
experimental value to validate against. The geometry is force-field quality and
should be refined with the [geometry-optimize](../../../geometry-optimize/SKILL.md)
skill (or built here with `--opt xtb`) before computing energetics.

## Building from a name instead

`build-from-smiles` is SMILES-only. To build acetone from its **name**, resolve
the name to a SMILES first with the
[name-to-smiles](../../../name-to-smiles/SKILL.md) skill, then pass the resolved
SMILES here — see the
[name-to-3d-structure](../../../../workflows/name-to-3d-structure.md) workflow.

## References
- N. M. O'Boyle et al. "Open Babel: An open chemical toolbox." *J. Cheminform.* **2011**, *3*, 33. https://doi.org/10.1186/1758-2946-3-33 (3D structure generation).

## 3D Structures
- [acetone.xyz](acetone.xyz)
