---
name: build-from-smiles
description: Generate a 3D xyz geometry from a SMILES string using Open Babel, optionally refining with QM.
category: chemistry
---

# Build 3D Molecule from SMILES

> [!IMPORTANT]
> **At launch, immediately give the user the live `.out` log path and offer
> `tail -f`** — do not wait for the run to finish. If a QM refinement (`--opt`)
> is requested, confirm that method with the user rather than guessing.
> (calculation-reporting-standards non-negotiables #9 and #10.)

> [!IMPORTANT]
> **SMILES-only.** This skill builds from a **SMILES string** only. A plain
> molecule name (e.g. `aspirin`) — or any string Open Babel cannot parse as
> SMILES — is **rejected** with an error. To build from a name, resolve it to a
> SMILES first with [name-to-smiles](../name-to-smiles/SKILL.md) (which reports
> the answering source and an ACS citation), then pass the resolved SMILES here.
> The [name-to-3d-structure](../../workflows/name-to-3d-structure.md) workflow
> chains the two steps.

## Goal
Convert a SMILES string into a 3D `.xyz` geometry via Open Babel's `--gen3d`
coordinate generator, optionally refining with xtb / MOPAC / DFT / HF. This is
the on-ramp for every other chemkit skill — it produces the `.xyz` that
single-point, optimization, and frequency skills require.

## Instructions
```bash
# Env: anl_env
python skills/build-from-smiles/scripts/build-from-smiles.py [args]
```

Arguments:
- A SMILES string (required, positional). It must parse as SMILES: Open Babel is used as the validity gate, and a non-SMILES input (e.g. a molecule name) is rejected up front with no network call — the error points at the name-to-smiles skill.
- `--out-xyz <path>` — destination xyz (default: sanitized input + `.xyz` in cwd).
- `--out <path>` — result JSON path.
- `--name <str>` — title comment for the xyz (default: the input string).
- `--opt {xtb,mopac,dft,hf}` — optional QM refinement after the obabel build. The QM-relaxed xyz becomes the canonical output; the obabel-only file is kept as `xyz_path_obabel`.
- `--solvent <name>` — implicit solvent for the optional QM step.
- `--charge N` — net charge forwarded to the QM step (default 0). obabel does not infer charge, so set this explicitly for ions (e.g. `-1` for a carboxylate).
- `--mult N` — spin multiplicity forwarded to the QM step (default 1).
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. **`--density-fit`** enables RI density fitting (~3-10x faster SCF, ~0.1-0.8 mEh error); OFF by default — chemkit uses exact integrals (plain RKS/UKS, matching hand-run PySCF).
- HF-only: `--basis <name>`.

If the SMILES is missing → stop and ask. If the input does not parse as SMILES (e.g. it is a molecule name), the build is rejected — resolve the name with [name-to-smiles](../name-to-smiles/SKILL.md) first, then pass the resolved SMILES here.

**Note on ambiguous short strings**: some short strings parse as *unintended* SMILES rather than the element/name a user might mean — e.g. `Co` parses as `C[O]` (carbon + oxygen), not cobalt, and `no` parses as `N=O`. This is inherent to SMILES syntax. `C` (methane), `O` (water), and `N` (ammonia) are the intended single-atom SMILES. When the user means an element symbol or a name, resolve it via [name-to-smiles](../name-to-smiles/SKILL.md) and pass the returned SMILES.

Then read the JSON and report: the xyz file path (headline deliverable) and the atom count; the input SMILES (`smiles_input`); if `--opt` was used, the QM energy, convergence flag, and the charge/multiplicity used; every warning from the result JSON, reproduced verbatim — none dropped, summarized, or paraphrased; if there are no warnings, say so; and the next-step recipe pointing at [single-point-energy](../single-point-energy/SKILL.md) or [geometry-optimize](../geometry-optimize/SKILL.md). The JSON records the exact `obabel` command under `build.command`.

Recommendations: skip `--opt` if a downstream skill does its own optimization; use `--opt xtb` as the standard "good enough" clean-up; for floppy molecules build once here then hand off to [conformer-search](../conformer-search/SKILL.md); set `--charge`/`--mult` explicitly for ions and radicals.


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

> **Skill name / discovery.** This skill's engine subcommand is `build`; the name `build-from-smiles` is an accepted alias. Do **not** invent flags: the SMILES is the positional argument, not `--smiles`/`--input`. If unsure of the exact name or flags, run `chemkit --list-skills` or `chemkit build-from-smiles --help-json` (or `--help`) to discover them instead of guessing.

## Examples
```bash
# Env: anl_env
# Acetone from its SMILES, refined with xtb.
python skills/build-from-smiles/scripts/build-from-smiles.py 'CC(=O)C' --opt xtb
```

```bash
# Env: anl_env
# Acetate anion — set the charge explicitly (obabel does not infer it).
python skills/build-from-smiles/scripts/build-from-smiles.py 'CC(=O)[O-]' --opt xtb --charge -1
```

To build from a molecule **name**, resolve it first, then build from the SMILES:
```bash
# Env: anl_env
# Step 1: name -> SMILES (records source + ACS citation).
python skills/name-to-smiles/scripts/name-to-smiles.py 'caffeine' --out caffeine_smiles.json
# Step 2: build from the resolved SMILES (read it from the JSON above).
python skills/build-from-smiles/scripts/build-from-smiles.py 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C' --opt xtb
```
See the [name-to-3d-structure](../../workflows/name-to-3d-structure.md) workflow for the full two-step recipe.

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required.
- **Input is SMILES-only**: the positional input must parse as a SMILES. A molecule name (or any unparseable string) is rejected with an error pointing at [name-to-smiles](../name-to-smiles/SKILL.md). No network call is made by this skill.
- **Backend**: Open Babel `--gen3d` is a force-field build — a single conformer with force-field-quality geometry. The optional QM step uses chemkit's normal `opt` machinery (same convergence criteria, backends, and basis/solvent caveats).
- **Charge inference**: obabel writes only the geometry and does not infer charge; the QM step needs `--charge`/`--mult` set explicitly for ions/radicals.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison; report only computed values; compare to experiment only if the user explicitly asks.
- **Install/availability**: `conda install -c conda-forge openbabel`. "obabel failed to build 3D coordinates" → check SMILES syntax (case-sensitive). "'<input>' is not a valid SMILES string" → the input is not parseable SMILES; if it is a molecule name, resolve it with name-to-smiles first, or supply a valid SMILES / xyz.

## References
- O'Boyle et al. *J. Cheminform.* **2011**, 3, 33. https://doi.org/10.1186/1758-2946-3-33
- Bannwarth, Ehlert, Grimme. *J. Chem. Theory Comput.* **2019**, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
