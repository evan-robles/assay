---
description: Turn a plain molecule name into a 3D xyz geometry by resolving the name to a SMILES and then building coordinates, with verifiable structure provenance.
---

# Name to 3D Structure

This workflow guides you through turning a plain molecule **name** (common,
trade, or systematic IUPAC) into a 3D `.xyz` geometry ready for downstream
chemkit calculations.

**Scientific problem:** Most chemkit skills (single-point, optimization,
frequency, …) require a 3D `.xyz` as input, but users frequently start from only
a molecule *name*. The [build-from-smiles](../skills/build-from-smiles/SKILL.md)
skill is deliberately **SMILES-only** — it does not resolve names — so name →
structure is a two-step procedure: first resolve the name to a SMILES from a
citable online source, then embed 3D coordinates from that SMILES. Keeping the
two steps explicit means the structure's provenance (which database answered, the
SMILES, an ACS citation) is captured and reportable, rather than hidden inside an
opaque one-shot "build from a name" call. A correct result is a 3D geometry whose
identity (formula, atom count, stereochemistry) matches the named molecule, with
the resolving source recorded.

**Inputs:** a molecule name (e.g. `caffeine`, `L-alanine`, `3,5-dibromophenol`);
optionally a net charge and multiplicity for ions/radicals, and a QM refinement
method if a better-than-force-field geometry is wanted.

**Outputs:** the resolved SMILES with its `smiles_source` provenance (source
label, `smiles_kind`, identifier such as a PubChem CID, ACS citation) as JSON; a
3D `.xyz` geometry (force-field quality by default, optionally QM-relaxed); and
the build result JSON (atom count, exact `obabel` command, any warnings).

## Prerequisites
- **External binaries:** Open Babel (`obabel`) for 3D embedding; for the optional
  QM refinement, the corresponding backend (`xtb`, `mopac`, or PySCF for
  `dft`/`hf`) must be on PATH — mirrors each skill's own requirements.
- **Network access:** required for step 1 (name resolution queries PubChem →
  OPSIN → NIST WebBook). Step 2 (build) needs no network.
- **Environment:** `# Env: anl_env` for both skills.
- **Required skills:**
  [name-to-smiles](../skills/name-to-smiles/SKILL.md) (engine subcommand
  `resolve`) and
  [build-from-smiles](../skills/build-from-smiles/SKILL.md) (engine subcommand
  `build`).
- **Required inputs and assumed state:** a molecule name. For an ion or radical,
  know the intended charge/multiplicity — Open Babel does **not** infer charge, so
  it must be passed explicitly to any QM step.

## Methodology

### Step 1 — Resolve the name to a SMILES
Use [name-to-smiles](../skills/name-to-smiles/SKILL.md) to look up the SMILES,
trying PubChem → OPSIN → NIST WebBook in order and recording the first hit with
an ACS-format citation.

```bash
# Env: anl_env
python skills/name-to-smiles/scripts/name-to-smiles.py "caffeine" \
    --out caffeine_smiles.json --stdout path
```

Read `caffeine_smiles.json` and capture: the resolved `smiles` (this is the input
to Step 2), `smiles_source.source` (which database answered),
`smiles_source.smiles_kind` (`isomeric` / `connectivity` / `unspecified`),
`smiles_source.identifier` (e.g. a PubChem CID), and `smiles_source.citation`
(ACS). Persist this JSON — Step 2 consumes the `smiles` field from it. Report the
source and citation to the user; a resolved SMILES is never presented without its
provenance.

### Step 2 — Build the 3D geometry from that SMILES
Feed the resolved SMILES to
[build-from-smiles](../skills/build-from-smiles/SKILL.md) to embed 3D coordinates
via Open Babel `--gen3d`.

```bash
# Env: anl_env
# Use the SMILES read from caffeine_smiles.json (here: caffeine's isomeric SMILES).
python skills/build-from-smiles/scripts/build-from-smiles.py \
    'CN1C=NC2=C1C(=O)N(C(=O)N2C)C' \
    --out-xyz caffeine.xyz --out caffeine_build.json --stdout path
```

The headline deliverable is `caffeine.xyz`. Read `caffeine_build.json` for the
atom count (`n_atoms`), the input SMILES (`smiles_input`), the exact `obabel`
command (`build.command`), and any `warnings` (relay verbatim).

### Step 2b (optional) — QM-refine the geometry
For a better-than-force-field starting structure, add `--opt` to the build. Confirm
the method with the user rather than guessing (calculation-reporting-standards
non-negotiable #10). Set `--charge`/`--mult` explicitly for ions/radicals.

```bash
# Env: anl_env
python skills/build-from-smiles/scripts/build-from-smiles.py \
    'CC(=O)[O-]' --charge -1 --opt xtb \
    --out-xyz acetate.xyz --out acetate_build.json --stdout path
```

The QM-relaxed `.xyz` becomes the canonical output (`xyz_path`); the obabel-only
file is kept as `xyz_path_obabel`. Report the QM energy, convergence flag, and the
charge/multiplicity used.

## Decision logic
- **Resolution fails (all sources miss):** `name-to-smiles` exits nonzero with a
  `LookupError` ("Could not resolve …"). **Stop and report the gap** — do not
  fabricate a SMILES or a structure. Offer to broaden the search (try an IUPAC
  name), or ask the user to supply a SMILES/`.xyz` directly. An adversarial or
  non-existent "molecule" (a name that isn't a real compound) must end here, not
  produce a guessed geometry.
- **Ambiguous name / short string:** some short strings are themselves valid
  SMILES (`C` = methane, `O` = water) and some parse as *unintended* SMILES
  (`Co` → `C[O]`, not cobalt). If the user clearly means a named species, pass the
  name to Step 1 and use the returned SMILES; do not let Step 2 silently accept a
  short ambiguous token.
- **Stereochemistry is `unspecified` (NIST answered):** the SMILES lacks stereo
  (NIST is round-tripped through InChI). Surface the `smiles_kind: unspecified`
  warning; for a molecule with stereocenters, note the built geometry's
  configuration is not guaranteed and consider a PubChem/OPSIN-sourced SMILES.
- **A lower-priority source answered (provenance warning):** if PubChem timed out
  and OPSIN/NIST answered, `name-to-smiles` emits a provenance warning — relay it;
  the stereochemistry may differ from the preferred source.
- **Build rejects the input:** if Step 2 reports `"… is not a valid SMILES
  string"`, the value passed was a name or malformed SMILES, not the resolved
  SMILES — re-read `smiles` from Step 1's JSON and pass exactly that.
- **When to stop and ask (AskUserQuestion):** unknown/ambiguous charge or
  protonation state; a name with multiple plausible tautomers/isomers; or a
  requested `--opt` method the user did not specify.

## Acceptance criteria
Before reporting the structure as trustworthy:
- **Resolution provenance present:** `smiles_source.source` and
  `smiles_source.citation` are recorded and reported; the SMILES is not presented
  without them.
- **Identity check:** the built geometry's atom count / molecular formula is
  consistent with the named molecule (e.g. caffeine → C8H10N4O2, 24 atoms). A
  mismatch means the wrong compound was resolved — stop and investigate.
- **Stereochemistry:** for a chiral target, confirm `smiles_kind` is `isomeric`
  (PubChem) rather than `unspecified` (NIST) if configuration matters.
- **Build sanity:** `build-from-smiles` returned a non-empty `.xyz` with
  `n_atoms > 0` and the integrity block marks the result trustworthy; all
  `warnings` are relayed verbatim (or "no warnings" stated).
- **QM step (if run):** the optimization `converged` flag is true; if false, the
  non-converged geometry is flagged, not presented as final.
- **No literature comparison** is offered unless the user asks; the resolving
  source is *input provenance*, not a validation against a measured value. Any
  such comparison must follow `research-standards.md` in full.

## Reproducibility
- Record, per run: the input name; the resolved SMILES and its
  `smiles_source` (source, `smiles_kind`, identifier, ACS citation); the build
  method (`obabel --gen3d`) and the exact `build.command`; and, for the optional
  QM step, the method, solvent (or "gas phase"), charge, and multiplicity.
- The exact `cli_invocation` is emitted in each skill's result JSON header —
  report those two lines so the run can be repeated verbatim.
- Keep the two result JSONs (`*_smiles.json`, `*_build.json`) and the `.xyz`
  as the persisted artifacts between steps; do not assume a temp file survives.
  Each skill also streams a live `<subcommand>_<timestamp>.out` log to the
  caller's cwd — surface its path at launch.

## Limitations
- **Screening-grade geometry.** Open Babel `--gen3d` produces a single
  force-field conformer, not a Boltzmann ensemble or a QM minimum. For floppy
  molecules, follow with [conformer-search](../skills/conformer-search/SKILL.md);
  for energetics, refine with
  [geometry-optimize](../skills/geometry-optimize/SKILL.md) or the `--opt` step.
- **Resolution depends on external databases.** Coverage and stereochemistry vary
  by source; a name absent from PubChem/OPSIN/NIST cannot be resolved here.
- **Charge is not inferred.** obabel writes geometry only; ions/radicals need
  explicit `--charge`/`--mult` at the QM step.
- **Not publication-grade** as-is: the geometry is a starting point. Never present
  a force-field or single-conformer structure as a definitive equilibrium
  geometry.

## References
- N. M. O'Boyle; M. Banck; C. A. James; C. Morley; T. Vandermeersch; G. R. Hutchison. Open Babel: An Open Chemical Toolbox. *J. Cheminform.* **2011**, *3*, 33. https://doi.org/10.1186/1758-2946-3-33. [verified: DOI 200 via curl, 2026-07-09]
- S. Kim; et al. PubChem 2023 Update. *Nucleic Acids Res.* **2023**, *51*, D1373–D1380. https://doi.org/10.1093/nar/gkac956. [verified: Crossref title/journal/vol/pages match, 2026-07-09]
- D. M. Lowe; P. T. Corbett; P. Murray-Rust; R. C. Glen. Chemical Name to Structure: OPSIN, an Open Source Solution. *J. Chem. Inf. Model.* **2011**, *51*, 739–753. https://doi.org/10.1021/ci100384d. [verified: Crossref title/journal/vol/pages match, 2026-07-09]
- P. J. Linstrom; W. G. Mallard, Eds. *NIST Chemistry WebBook*, NIST Standard Reference Database Number 69. https://doi.org/10.18434/T4D303. [verified: DOI 200 via curl, 2026-07-09]
- C. Bannwarth; S. Ehlert; S. Grimme. GFN2-xTB — An Accurate and Broadly Parametrized Self-Consistent Tight-Binding Quantum Chemical Method. *J. Chem. Theory Comput.* **2019**, *15*, 1652–1671. https://doi.org/10.1021/acs.jctc.8b01176. [verified: Crossref title/journal/vol/pages match, 2026-07-09]
