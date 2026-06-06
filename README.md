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
├── claude/               # slash-command skill wrappers (symlinked into ~/.claude/commands/)
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

## Slash commands

The `claude/*.md` files are symlinked into `~/.claude/commands/` so they appear as `/single_point_energy`, `/geometry_optimize`, `/vibrational_analysis`, `/binding_energy`, `/redox_potential`, `/conformer_search`.

## Notes / caveats

- **Energy zeros differ between xtb and MOPAC.** xtb uses isolated atoms at infinity; MOPAC ENPART uses bare nuclei + free electrons. Only same-method differences are physically meaningful.
- **PM7 transition-metal parameters are spotty** — the schema flags this in `warnings` when relevant.
- **Redox potentials and conformer search are screening-grade**, not publication-grade. The skill output warns about this.
