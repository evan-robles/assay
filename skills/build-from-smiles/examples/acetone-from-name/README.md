# Example: Build Acetone 3D Structure from Name (Open Babel)

## Goal
Resolve the molecule name "acetone" to a structure and generate a force-field-quality 3D starting geometry. No quantum-mechanical property is computed.

## Calculation run
- **Skill:** build-from-smiles
- **Method:** Open Babel `--gen3d` (force-field 3D embedding); program: openbabel — no QM method
- **Basis/functional:** not applicable
- **Charge/multiplicity:** 0 / 1
- **Solvent:** not applicable
- **Input:** molecule name "acetone"

```bash
# Env: anl_env
python skills/build-from-smiles/scripts/build-from-smiles.py acetone --out-xyz acetone.xyz --out acetone_build.json
```

Generated files: [`acetone_build.json`](acetone_build.json), [`acetone.xyz`](acetone.xyz)

## Result (this calculation)

| Property | Value | Notes |
|---|---|---|
| Resolved SMILES | CC(=O)C | via PubChem (CID 180) |
| Molecular formula | C3H6O | 10 atoms |
| 3D structure | built | Open Babel `--gen3d`, force-field quality |

The resolved-SMILES source and an ACS-format citation are recorded in the JSON under `smiles_source` (input provenance).

## Literature comparison

| Check | Result | Verdict |
|---|---|---|
| Identity (formula + SMILES) | C3H6O, CC(=O)C | Correctly correspond to acetone (PubChem CID 180). |

This skill produces a starting geometry, not a measured property, so there is no experimental value to validate against. The geometry is force-field quality and should be refined with the geometry-optimize skill before computing energetics.

## References
- S. Kim et al. "PubChem 2023 update." *Nucleic Acids Res.* **2023**, *51*, D1373. https://doi.org/10.1093/nar/gkac956 (name→structure source).
- N. M. O'Boyle et al. "Open Babel: An open chemical toolbox." *J. Cheminform.* **2011**, *3*, 33. https://doi.org/10.1186/1758-2946-3-33

## 3D Structures
- [acetone.xyz](acetone.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
