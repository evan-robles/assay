# chem-skills / chemkit

ASE-based computational chemistry suite — xtb (GFN2) and MOPAC (PM7), with optional COSMO/ALPB solvation.

## Layout

Each skill is a **self-contained folder** under `skills/`, named after the skill.
A folder is just three files — the chemistry engine is **inlined into the single
`<name>.py` script**, so there is no shared package and no `_engine/` directory:

```
~/chem-skills/
├── skills/
│   ├── single_point_energy/
│   │   ├── SKILL.md                 # the skill doc (frontmatter + usage)
│   │   ├── single_point_energy.py   # ONE self-contained script (engine inlined)
│   │   └── requirements.txt         # pip deps + external-binary notes
│   ├── geometry_optimize/  ...
│   └── (18 skill folders total)
├── tools/build_skill_folders.py     # generator (regenerates the scripts from a source tree)
└── tests/                           # regression suite (drives the skill scripts)
```

Each `<name>.py` embeds exactly the engine modules that skill needs (shared
infra + its task closure + the PySCF backend where applicable) as readable
source, registered under their real module names by a small bootstrap at the top
of the file so each module keeps its own namespace. Because the engine is inside
the script, you can copy a single skill's `.py` elsewhere and run it with nothing
else present.

## Install & run a skill

```bash
# Per skill — install its Python deps (external binaries listed in the file)
cd skills/single_point_energy
pip install -r requirements.txt

# Run it (each skill script mirrors the chemist arguments for that task)
python single_point_energy.py --method xtb --solvent water mol.xyz
python single_point_energy.py --help
```

External binaries are NOT pip-installable — install separately, e.g.:
```bash
conda install -c conda-forge xtb mopac openbabel ase
```
(`xtb` for `--method xtb`; `mopac` for `--method mopac` / PM7 post-opt;
`openbabel` provides `obabel`/`obenergy` for SMILES→3D, name lookup, and
conformer search.) Install `pyscf` (in the skill's requirements where relevant)
for `--method dft`/`--method hf`, and `sella` for transition-state searches on
the xtb/dft/hf backends (MOPAC has a native TS optimizer).

## Quick examples

```bash
python skills/single_point_energy/single_point_energy.py --method xtb --solvent water mol.xyz
python skills/geometry_optimize/geometry_optimize.py     --method mopac --charge 0 mol.xyz
python skills/vibrational_analysis/vibrational_analysis.py --method xtb --symmetry 2 mol_opt.xyz
python skills/binding_energy/binding_energy.py --method xtb --monomer A.xyz --monomer B.xyz complex.xyz
python skills/redox_potential/redox_potential.py --method xtb --ox-charge 0 --red-charge -1 --solvent water mol.xyz
python skills/conformer_search/conformer_search.py --method xtb mol.xyz
python skills/build_from_smiles/build_from_smiles.py ethanol   # name or SMILES → 3D xyz
```

All tasks write a single JSON file with a common header:
`{task, method, program, input_file, n_atoms, atoms, charge, multiplicity, solvent, cli_invocation, ...}`

## How the agentic skills work

Each skill folder pairs a runnable Python script with a `SKILL.md` that turns it
into something an agent can drive directly. The `SKILL.md` is a Markdown skill
file with YAML frontmatter so it shows up as a slash command
(`/single_point_energy`, `/geometry_optimize`, `/vibrational_analysis`,
`/binding_energy`, `/redox_potential`, `/conformer_search`,
`/conformational_analysis`, ...).

Each skill follows the same pipeline:

1. **Trigger** — the frontmatter `description:` is what the agent matches against
   the user's request (e.g. "binding energy", "what's the energy of this
   structure"); it also states what the skill should *not* be used for, to
   disambiguate from neighboring skills (e.g. `single_point_energy` vs.
   `geometry_optimize`).
2. **Parse arguments** — the skill spells out which flags `$ARGUMENTS` should
   contain (an `.xyz` path is always required) and which are optional
   (`--method`, `--solvent`, `--charge`, `--mult`, task-specific flags like
   `--postopt` for conformer search). If something required is missing, the
   skill tells the agent to stop and either ask directly or use
   **AskUserQuestion** (e.g. method selection for `single_point_energy`).
3. **Invoke the script** — the skill gives the literal
   `python <skill>.py ...` invocation to run as a subprocess.
4. **Read the JSON** — every skill prints one JSON result with the
   common header described above plus task-specific fields. The skill tells
   the agent to copy this to `<basename>_<task>_<method>.json` next to the
   user's input (and, for tasks that produce structures, to copy the
   accompanying `.xyz` files too) so results persist outside the tmp work
   directory.
5. **Report** — the skill enumerates exactly which fields to surface and how
   (units, which `code_specific` keys matter, caveats to mention — e.g. that
   xtb/MOPAC energy zeros aren't comparable, or that a single surviving
   conformer after post-opt is the converged answer, not a bug).

This keeps the heavy lifting (geometry I/O, calculator setup, parsing program
output into a stable schema) inlined in each skill's single `<name>.py`, while
the `SKILL.md` encodes the *judgment calls* — when to ask the user for
clarification, what's worth flagging as a caveat, and how to translate raw JSON
into something a chemist would actually want to read.

> Regenerating the scripts: `tools/build_skill_folders.py` inlines each skill's
> engine into its `<name>.py` from a chemkit source tree (rewriting imports to
> folder-local `_engine.*` names embedded in the file). The scripts are the
> source of truth now; the generator is kept for reproducibility and restores
> `src/chemkit` from git history automatically (override with `CHEMKIT_SRC=` or
> `CHEMKIT_SRC_REF=`).

## Notes / caveats

- **PM7 transition-metal parameters are spotty** — the schema flags this in `warnings` when relevant.
- **Redox potentials and conformer search are screening-grade**, not publication-grade. The skill output warns about this.
