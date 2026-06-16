# Example: Resolve "caffeine" to a SMILES (PubChem → OPSIN → NIST)

## Goal
Resolve the common name "caffeine" to a SMILES string and report which source
answered, with an ACS-format citation. No structure is built and no property is
computed — this is a pure name → SMILES lookup.

## Calculation run
- **Skill:** name-to-smiles
- **Method:** online lookup, PubChem → OPSIN → NIST WebBook (first hit wins)
- **Basis/functional:** not applicable (no computation)
- **Charge/multiplicity:** not applicable
- **Solvent:** not applicable
- **Input:** molecule name "caffeine"

```bash
# Env: anl_env
python skills/name-to-smiles/scripts/name-to-smiles.py "caffeine" --out caffeine_smiles.json --stdout path
```

Generated file: [`caffeine_smiles.json`](caffeine_smiles.json)

## Result (this run)

| Field | Value |
|---|---|
| Resolved SMILES | `CN1C=NC2=C1C(=O)N(C(=O)N2C)C` |
| Source | PubChem (first source in the chain to answer) |
| Identifier | CID 2519 |
| SMILES kind | isomeric |
| Warnings | none (PubChem answered; no fallback) |

The answering source and an ACS-format citation are recorded in the JSON under
`smiles_source` (input provenance).

## Validation (vs. the authoritative PubChem record)

This skill returns a looked-up identifier, not a measured quantity, so the
correctness check is whether the resolved SMILES matches the authoritative
source record. Verified live against the PubChem PUG REST API for CID 2519
(accessed 2026-06-16):

| Check | Resolver output | PubChem CID 2519 record | Verdict |
|---|---|---|---|
| SMILES | `CN1C=NC2=C1C(=O)N(C(=O)N2C)C` | `CN1C=NC2=C1C(=O)N(C(=O)N2C)C` | Exact match |
| Molecular formula | — | `C8H10N4O2` | Consistent with caffeine |
| IUPAC name | — | `1,3,7-trimethylpurine-2,6-dione` | Confirms caffeine |

`[verified: PubChem PUG REST 200 via curl, SMILES/formula/IUPAC read from CID 2519, 2026-06-16]`

The resolver's own provenance points at the same record (CID 2519), so the
returned SMILES is correctly attributed to its source.

## References
- S. Kim et al. "PubChem 2023 update." *Nucleic Acids Res.* **2023**, *51*, D1373. https://doi.org/10.1093/nar/gkac956 (name → structure source).
- National Center for Biotechnology Information. PubChem Compound Summary for CID 2519, Caffeine. https://pubchem.ncbi.nlm.nih.gov/compound/2519 (accessed 2026-06-16).

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
