---
name: name-to-smiles
description: Resolve a plain molecule name to a SMILES string from online sources, reporting which source answered and an ACS-format citation.
category: chemistry
---

# Resolve a Molecule Name to SMILES

> [!IMPORTANT]
> This skill performs a **lookup**, not a measurement or calculation. The SMILES
> it returns is sourced from an online database/parser; the result always records
> **which source answered** and an **ACS-format citation** under `smiles_source`
> (input provenance). Report that provenance to the user — never present a
> resolved SMILES without its source.

## Goal
Resolve a plain molecule **name** (common, trade, or systematic IUPAC) to a
**SMILES** string by querying online sources in priority order —
**PubChem → OPSIN → NIST WebBook** — and report the resolved SMILES, the
stereochemistry flavor, the answering source, and an ACS-format citation. This is
the pure lookup half of [build-from-smiles](../build-from-smiles/SKILL.md): it
does **not** generate a 3D geometry or run any QM. Use it when you want the
SMILES/identity and its provenance without building an `.xyz`.

## Instructions
```bash
# Env: anl_env
python skills/name-to-smiles/scripts/name-to-smiles.py "<molecule name>" [--out result.json]
```

Argument:
- A plain molecule **name** (required, positional), e.g. `caffeine`, `L-alanine`,
  or a systematic IUPAC name. If the input parses as a SMILES it is treated as a
  SMILES (no lookup) — supply an explicit name if you mean the named species.
- `--out <path>` — result JSON path (default: `<sanitized-name>_smiles.json`).

**Resolution order** (first source that returns a usable structure wins):
1. **PubChem** (PUG REST) — name → CID → isomeric SMILES. A lookup database; any
   common/trade name.
2. **OPSIN** (EBI) — systematic IUPAC name → SMILES. A parser, not a database.
3. **NIST WebBook** — name → InChI → SMILES (via Open Babel). Stereochemistry is
   labeled `unspecified` for this source.

Then read the JSON and **report**: the resolved `smiles` (headline), the
`smiles_source.source` (which source answered), the `smiles_source.smiles_kind`
(`isomeric` / `connectivity` / `unspecified`), the `smiles_source.identifier`
(e.g. a PubChem CID), and the `smiles_source.citation` (ACS). If a lower-priority
source answered because a higher one timed out, a provenance **warning** is
surfaced in `warnings` — pass it on (the stereochemistry may differ from the
preferred source).

Next step: hand the SMILES to [build-from-smiles](../build-from-smiles/SKILL.md)
to generate a 3D `.xyz` for downstream calculations.

> **Result reading (token-efficient, required):** run with `--out <path>
> --stdout path` so stdout is a one-line pointer, then read back only the fields
> you need with `jq` (always include `warnings`). See
> [RESULT-READING.md](../RESULT-READING.md).

## Examples
```bash
# Env: anl_env
python skills/name-to-smiles/scripts/name-to-smiles.py "caffeine" --out caffeine_smiles.json --stdout path
```

```bash
# Env: anl_env
python skills/name-to-smiles/scripts/name-to-smiles.py "L-alanine" --out alanine_smiles.json
```

See [`examples/`](examples/) for a validated example checked against the
authoritative PubChem record.

## Constraints
- **Environment**: `# Env: anl_env` required.
- **Network required**: PubChem → OPSIN → NIST are queried over the network; with
  no connectivity the lookup fails (`LookupError`, surfaced as a JSON error).
- **Lookup, not computation**: returns a SMILES and its provenance only — no 3D
  geometry, formula, InChI, or canonicalization. Use
  [build-from-smiles](../build-from-smiles/SKILL.md) for an `.xyz`.
- **Stereochemistry caveat**: NIST-sourced structures are round-tripped through
  InChI and carry `smiles_kind: unspecified` stereochemistry. PubChem returns
  isomeric SMILES.
- **Reproducibility**: the first successful resolution per name is cached
  (`CHEMKIT_RESOLVE_CACHE`) so repeat runs reuse the same source rather than
  silently falling through to a different one.
- **Provenance is always reported** — the source label and ACS citation are input
  provenance for the resolved structure, not auto-fetched comparison data.

## References
- S. Kim et al. "PubChem 2023 update." *Nucleic Acids Res.* **2023**, *51*, D1373. https://doi.org/10.1093/nar/gkac956
- D. M. Lowe et al. "Chemical Name to Structure: OPSIN, an Open Source Solution." *J. Chem. Inf. Model.* **2011**, *51*, 739. https://doi.org/10.1021/ci100384d
- P. J. Linstrom; W. G. Mallard, Eds. *NIST Chemistry WebBook*, NIST Standard Reference Database Number 69. https://doi.org/10.18434/T4D303

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
