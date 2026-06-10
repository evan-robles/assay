# chem-skills / chemkit

ASE-based computational chemistry suite — xtb (GFN2) and MOPAC (PM7), with optional COSMO/ALPB solvation.

## Layout

```
~/chem-skills/
├── src/chemkit/          # the Python package
│   ├── calculators.py    # ASE calculator factory (xtb-python or CLI; MOPAC)
│   ├── io.py             # geometry I/O + result writer
│   ├── schema.py         # shared JSON schema
│   ├── cli.py            # `chemkit` command-line entry
│   └── tasks/
│       ├── sp.py         # single-point
│       ├── opt.py        # geometry optimization (BFGS)
│       ├── freq.py       # vibrations + IdealGasThermo
│       ├── binding.py    # ΔE_bind = E(complex) - Σ E(monomers)
│       ├── redox.py      # E° via charge-state Δ on same geometry
│       └── confsearch.py # CREST wrapper
├── bin/chemkit           # bash shim (works without pip install)
├── skills/               # slash-command skill wrappers (symlinked into ~/.claude/commands/)
└── tests/
```

## Install

```bash
# 1. Make sure xtb / MOPAC are installed
conda install -c conda-forge xtb mopac ase
pip install xtb  # optional: faster xtb-python bindings

# 2. Either pip install the package
pip install -e ~/chem-skills

# OR use the bash shim
export PATH="$HOME/chem-skills/bin:$PATH"
```

## Quick examples

```bash
chemkit sp     --method xtb   --solvent water  mol.xyz
chemkit opt    --method mopac --charge 0       mol.xyz
chemkit freq   --method xtb   --symmetry 2     mol_opt.xyz
chemkit binding --method xtb --monomer A.xyz --monomer B.xyz complex.xyz
chemkit redox  --method xtb   --ox-charge 0 --red-charge -1 --solvent water mol.xyz
chemkit confsearch --method xtb mol.xyz
```

All tasks write a single JSON file with a common header:
`{task, method, program, input_file, n_atoms, atoms, charge, multiplicity, solvent, cli_invocation, ...}`

## How the agentic skills work

`chemkit` itself is just a CLI — the `skills/*.md` files are what turn it into
something an agent can drive directly. Each one is a Markdown skill file
with YAML frontmatter, symlinked into `~/.claude/commands/` so it shows up as
a slash command (`/single_point_energy`, `/geometry_optimize`,
`/vibrational_analysis`, `/binding_energy`, `/redox_potential`,
`/conformer_search`, `/conformational_analysis`).

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
3. **Invoke the CLI** — the skill gives the literal `chemkit <task> ...`
   invocation to run as a subprocess.
4. **Read the JSON** — every `chemkit` task prints one JSON result with the
   common header described above plus task-specific fields. The skill tells
   the agent to copy this to `<basename>_<task>_<method>.json` next to the
   user's input (and, for tasks that produce structures, to copy the
   accompanying `.xyz` files too) so results persist outside the tmp work
   directory.
5. **Report** — the skill enumerates exactly which fields to surface and how
   (units, which `code_specific` keys matter, caveats to mention — e.g. that
   xtb/MOPAC energy zeros aren't comparable, or that a single surviving
   conformer after post-opt is the converged answer, not a bug).

This keeps the heavy lifting (geometry I/O, calculator setup, parsing
program output into a stable schema) inside `chemkit`, while the skill files
encode the *judgment calls* — when to ask the user for clarification, what's
worth flagging as a caveat, and how to translate raw JSON into something a
chemist would actually want to read.

## Notes / caveats

- **PM7 transition-metal parameters are spotty** — the schema flags this in `warnings` when relevant.
- **Redox potentials and conformer search are screening-grade**, not publication-grade. The skill output warns about this.
