---
name: build-from-smiles
description: Generate a 3D xyz geometry from a SMILES string or a plain molecule name, resolving names online and optionally refining with QM.
category: chemistry
---

# Build 3D Molecule from SMILES or Name

## Goal
Convert a SMILES string into a 3D `.xyz` geometry via Open Babel's `--gen3d` coordinate generator, optionally refining with xtb / MOPAC / DFT / HF. A bare molecule name is resolved to a SMILES online before building, with the answering source and an ACS-format citation reported. This is the on-ramp for every other chemkit skill — it produces the `.xyz` that single-point, optimization, and frequency skills require.

## Instructions
```bash
# Env: anl_env
python skills/build-from-smiles/scripts/build-from-smiles.py [args]
```

Arguments:
- A SMILES string **or** a plain molecule name (required, positional). Detection is automatic: if Open Babel can parse the input as SMILES it is used directly with no network call; otherwise it is treated as a name and resolved.
- `--out-xyz <path>` — destination xyz (default: sanitized input + `.xyz` in cwd).
- `--out <path>` — result JSON path.
- `--name <str>` — title comment for the xyz (default: the input string).
- `--opt {xtb,mopac,dft,hf}` — optional QM refinement after the obabel build. The QM-relaxed xyz becomes the canonical output; the obabel-only file is kept as `xyz_path_obabel`.
- `--solvent <name>` — implicit solvent for the optional QM step.
- `--charge N` — net charge forwarded to the QM step (default 0). obabel does not infer charge, so set this explicitly for ions (e.g. `-1` for a carboxylate).
- `--mult N` — spin multiplicity forwarded to the QM step (default 1).
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`.
- HF-only: `--basis <name>`.

If the molecule (SMILES or name) is missing → stop and ask.

**Name resolution**: when the input is not a SMILES, the name is resolved over the network by trying sources in order and reporting the first hit — PubChem (PUG REST, name → CID → isomeric SMILES), then OPSIN (systematic IUPAC name → SMILES), then NIST WebBook (name → InChI → SMILES via Open Babel; stereochemistry labeled `unspecified` for this source). PubChem and NIST are lookup databases (any common/trade name); OPSIN is a systematic-name parser. Some short names are also valid SMILES (`C` = methane, `N` = ammonia) and resolve as SMILES — supply an explicit SMILES if you mean the named species.

Then read the JSON and report: the xyz file path (headline deliverable) and the atom count; **if the input was a name**, the resolved SMILES, which source answered (`smiles_source.source`), and the ACS citation (`smiles_source.citation`) — always surface the provenance; if `--opt` was used, the QM energy, convergence flag, and the charge/multiplicity used; and the next-step recipe pointing at [single-point-energy](../single-point-energy/SKILL.md) or [geometry-optimize](../geometry-optimize/SKILL.md). The JSON records the exact `obabel` command under `build.command`.

Recommendations: skip `--opt` if a downstream skill does its own optimization; use `--opt xtb` as the standard "good enough" clean-up; for floppy molecules build once here then hand off to [conformer-search](../conformer-search/SKILL.md); set `--charge`/`--mult` explicitly for ions and radicals.

## Examples
```bash
# Env: anl_env
python skills/build-from-smiles/scripts/build-from-smiles.py 'caffeine' --opt xtb
```

```bash
# Env: anl_env
python skills/build-from-smiles/scripts/build-from-smiles.py 'CC(=O)[O-]' --opt xtb --charge -1
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required.
- **Backend**: Open Babel `--gen3d` is a force-field build — a single conformer with force-field-quality geometry. The optional QM step uses chemkit's normal `opt` machinery (same convergence criteria, backends, and basis/solvent caveats).
- **Charge inference**: obabel writes only the geometry and does not infer charge; the QM step needs `--charge`/`--mult` set explicitly for ions/radicals.
- **Name resolution requires internet**: PubChem → OPSIN → NIST are queried over the network; NIST-sourced structures have `unspecified` stereochemistry.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison; report only computed values; compare to experiment only if the user explicitly asks. (The PubChem/OPSIN/NIST source and ACS citation are input provenance for the resolved structure, not auto-reported comparison data, and are always reported.)
- **Install/availability**: `conda install -c conda-forge openbabel`. "obabel failed to build 3D coordinates" → check SMILES syntax (case-sensitive). "Could not resolve '<name>'" → check spelling, try an IUPAC name, supply a SMILES or xyz, or check network connectivity.

## References
- O'Boyle et al. *J. Cheminform.* **2011**, 3, 33. https://doi.org/10.1186/1758-2946-3-33
- Lowe et al. *J. Chem. Inf. Model.* **2011**, 51, 739. https://doi.org/10.1021/ci100384d
- Kim et al. *Nucleic Acids Res.* **2023**, 51, D1373. https://doi.org/10.1093/nar/gkac956
- Bannwarth, Ehlert, Grimme. *J. Chem. Theory Comput.* **2019**, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
