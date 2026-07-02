# ASSAY

**A**gentic **S**imulation **S**uite for **A**utomated chemistr**Y**

A computational chemistry suite powered by **xtb** (GFN2), **MOPAC** (PM7), and
**PySCF** (DFT / HF), with optional implicit solvation (ALPB / COSMO / PCM). ASE
provides the geometry-I/O and calculator-driver layer; the quantum chemistry runs
in those backends. Nineteen task-focused skills sit behind a single unified
engine exposed over the open Model Context Protocol.

> **Note:** the project is named ASSAY (formerly `chemkit`). The internal package
> and command names still use `chemkit` during the transition.

## Layout

A single unified chemistry engine lives behind an **MCP server**; each skill is a
thin client that calls it over the open Model Context Protocol. The engine is not
duplicated into the skills — each skill is a compact wrapper (~18 lines).

```
~/chem-skills/
├── rules/
│   ├── skill-standards.md            # how to author one atomic skill
│   ├── research-standards.md         # how to find/verify/cite literature (binding)
│   └── workflow-standards.md         # how to compose skills into a vetted workflow
├── mcp_server/
│   ├── server.py          # MCP server (FastMCP, stdio) — exposes 19 tools
│   ├── chemkit_engine/   # the ONE chemistry engine (cli, calculators, tasks, backends, schema)
│   ├── requirements.txt   # engine + server deps
│   └── README.md          # how to wire the server into any MCP client
├── skills/
│   ├── _mcp_client.py                         # shared thin MCP client
│   ├── single-point-energy/                   # kebab-case skill name
│   │   ├── SKILL.md                           # the skill doc (frontmatter + sections)
│   │   ├── scripts/single-point-energy.py     # ~18-line thin client -> MCP tool
│   │   ├── requirements.txt                   # just the `mcp` client SDK
│   │   └── examples/<calc-name>/              # README.md + generated .json/.xyz/.png
│   └── (19 skill folders total)
├── tools/build_skill_folders.py   # regenerates the thin clients
└── tests/                         # regression suite (drives the thin clients)
```

Skill folders are **kebab-case** and conform to `rules/skill-standards.md`
(frontmatter with `name`/`description`/`category`, Goal/Instructions/Examples/
Constraints/References sections, a `scripts/` client, and a validated `examples/`
folder). The MCP server speaks the open protocol, so **any** MCP-capable client
can drive it (not just one vendor). See `mcp_server/README.md` for a generic
client config.

## The `rules/` standards

Three model-readable standards govern how work is produced in this repo. Agents
load them automatically (`trigger: model_decision`) for the matching task.

| Rule | Governs | When it applies |
|------|---------|-----------------|
| [`skill-standards.md`](rules/skill-standards.md) | Authoring one atomic skill | Creating/editing a `skills/<name>/` folder |
| [`research-standards.md`](rules/research-standards.md) | Literature search, citations, fetched data | **Any** literature lookup, cited value, or validation-against-published-numbers |
| [`workflow-standards.md`](rules/workflow-standards.md) | Composing skills into end-to-end procedures | Multi-step objectives chaining several skills/tools |

> [!IMPORTANT]
> **Research integrity is enforced, not assumed.** Whenever a task involves
> searching the literature, citing a paper, or reporting a measured/published
> value, [`rules/research-standards.md`](rules/research-standards.md) is binding.
> It requires a **hard verification gate** — every DOI/URL is hit with a live
> link check (curl / Crossref / DOI resolve) **and** its metadata is matched to
> the intended citation before anything is shown — plus honest
> experimental-vs-computational provenance and **ACS-formatted** citations.
> Fabricated, guessed, dead-linked, or misattributed references are prohibited.
> When a source can't be verified, the honest "not found" report is the answer.

## Skills catalog

All 19 skills (each is also an MCP tool of the same name, mapping to the engine
subcommand shown):

| Skill / tool | Engine | What it does |
|--------------|--------|--------------|
| `single-point-energy` | `sp` | Total electronic energy + frontier properties at a fixed geometry |
| `geometry-optimize` | `opt` | Relax to a local minimum (equilibrium geometry) |
| `vibrational-analysis` | `freq` | Frequencies, ZPE, thermochemistry; minimum-vs-TS check |
| `binding-energy` | `binding` | Interaction energy of a complex vs. its fragments |
| `redox-potential` | `redox` | One-/multi-electron oxidation or reduction potential vs. SHE / Ag/AgCl / Fc⁺/Fc |
| `conformer-search` | `confsearch` | Sample low-energy conformers; ranked ensemble (Open Babel confab) |
| `frontier-orbitals` | `frontier` | HOMO/LUMO energies, gap, Koopmans descriptors |
| `electrostatics` | `electrostatics` | Dipole moment + atomic partial charges |
| `solvation` | `solvation` | Electronic solvation free energy in an implicit solvent |
| `logp-partition` | `logp` | Octanol–water logP from a solvation-free-energy cycle |
| `reaction-profile` | `profile` | End-to-end: activation/reaction ΔG, IRC verdict, annotated diagram |
| `pka-acidity` | `pka` | Aqueous pKa via a thermodynamic cycle (absolute or reference-anchored) |
| `build-from-smiles` | `build` | SMILES or molecule name → 3D `.xyz` (online name lookup, optional QM refine) |
| `fukui-reactivity` | `fukui` | Per-atom electrophilic/nucleophilic/radical Fukui + Morell dual descriptor |
| `transition-state` | `ts` | Locate a first-order saddle; freq check for exactly one imaginary mode |
| `intrinsic-reaction-coordinate` | `irc` | Walk down from a TS both ways; forward/reverse path trajectories |
| `reaction-energy` | `rxn-energy` | ΔE/ΔH/ΔG of a balanced reaction at one consistent level of theory |
| `conformational-analysis` | `scan` | Relaxed dihedral scan → rotation barrier + energy-vs-angle PNG |
| `visualize-orbitals` | `orbitals` | Molden (always) + optional cube files for MO isosurfaces |

## Methods / backends

Every task takes `--method {xtb, mopac, dft, hf}`:

- **`xtb`** — GFN2-xTB semiempirical; fast, ALPB implicit solvation.
- **`mopac`** — PM7 semiempirical; COSMO implicit solvation. *(PM7
  transition-metal parameters have limited coverage; this is flagged in the
  schema `warnings` when relevant.)*
- **`dft`** / **`hf`** — PySCF. DFT supports **tier presets** via `--tier`:
  - `fast` → r2SCAN / def2-SVP
  - `standard` → B3LYP / def2-TZVP
  - `accurate` → ωB97M-V / def2-QZVPP

  Override directly with `--functional <libxc name>` and `--basis <basis>`
  (e.g. `--functional pbe0 --basis def2-tzvp`). PCM implicit solvation.

Common flags across tasks: `--charge`, `--mult/--multiplicity`, `--solvent`
(gas phase if omitted), `--out`. `sella` enables transition-state searches on
the xtb/dft/hf backends (MOPAC has a native TS optimizer).

## Installation

The recommended path installs all backends and Python dependencies from the
checkout in a single step:

```bash
conda env create -f environment.yml
conda activate chemkit
```

Alternatively, if you manage Python dependencies with pip, install the
conda-forge binaries first (none are pip-installable), then the package:

```bash
conda install -c conda-forge xtb xtb-python mopac openbabel rdkit
pip install chemkit-mcp
```

**Dependencies.** The conda-forge binaries are required per backend: `xtb` and
`xtb-python` for `--method xtb`; `mopac` for `--method mopac`; `openbabel` for
SMILES-to-3D conversion, name lookup, and conformer search; `rdkit` for structure
handling. All remaining dependencies are installed automatically by pip: `pyscf`
(`--method dft` / `--method hf`), `matplotlib`, `sella` (transition-state
searches on the xtb/dft/hf backends), `mcp`, `ase`, `numpy`, and `openai`.

## Usage

Run a skill directly from the shell; the thin client starts and communicates with
the engine automatically:

```bash
python skills/single-point-energy/scripts/single-point-energy.py --method xtb --solvent water mol.xyz
python skills/single-point-energy/scripts/single-point-energy.py --help
```

Or start the MCP server and connect any MCP-capable client:

```bash
chemkit-mcp
```

```json
{ "mcpServers": { "chemkit": { "command": "chemkit-mcp" } } }
```

See `mcp_server/README.md` for uvx, OpenAI Agents SDK, and run-from-checkout
configurations. Set `CHEMKIT_MCP=/abs/path/to/mcp_server/server.py` to point the
thin clients at a specific server.

## Example commands

```bash
python skills/single-point-energy/scripts/single-point-energy.py --method xtb --solvent water mol.xyz
python skills/geometry-optimize/scripts/geometry-optimize.py      --method mopac --charge 0 mol.xyz
python skills/single-point-energy/scripts/single-point-energy.py  --method dft --tier standard mol.xyz
python skills/vibrational-analysis/scripts/vibrational-analysis.py --method xtb mol_opt.xyz
python skills/binding-energy/scripts/binding-energy.py --method xtb --monomer A.xyz --monomer B.xyz complex.xyz
python skills/redox-potential/scripts/redox-potential.py --method xtb --ref SHE --solvent water mol.xyz
python skills/pka-acidity/scripts/pka-acidity.py --method xtb --mode reference mol.xyz
python skills/reaction-profile/scripts/reaction-profile.py --method xtb reactant.xyz ts.xyz product.xyz
python skills/conformer-search/scripts/conformer-search.py --method xtb mol.xyz
python skills/build-from-smiles/scripts/build-from-smiles.py ethanol   # name or SMILES → 3D xyz
```

All tasks write a single JSON file with a common header:
`{task, method, program, input_file, n_atoms, atoms, charge, multiplicity, solvent, cli_invocation, ...}`

## How the agentic skills work

Each skill folder pairs a runnable Python script with a `SKILL.md` that turns it
into something an agent can drive directly. The `SKILL.md` is a Markdown skill
file with YAML frontmatter so it shows up as a slash command
(`/single-point-energy`, `/geometry-optimize`, `/vibrational-analysis`,
`/binding-energy`, `/redox-potential`, `/conformer-search`,
`/conformational-analysis`, `/transition-state`, `/reaction-profile`, ...).

Each skill follows the same pipeline:

1. **Trigger** — the frontmatter `description:` is what the agent matches against
   the user's request (e.g. "binding energy", "what's the energy of this
   structure"); it also states what the skill should *not* be used for, to
   disambiguate from neighboring skills (e.g. `single-point-energy` vs. `geometry-optimize`).
2. **Parse arguments** — the skill spells out which flags `$ARGUMENTS` should
   contain (an `.xyz` path is always required) and which are optional
   (`--method`, `--solvent`, `--charge`, `--mult`, `--tier`, task-specific flags
   like `--ref` for redox or `--mode` for pKa). If something required is missing,
   the skill tells the agent to stop and either ask directly or use
   **AskUserQuestion** (e.g. method selection for `single-point-energy`).
3. **Invoke the script** — the skill gives the literal
   `python <skill>.py ...` invocation to run as a subprocess.
4. **Read the JSON** — every skill prints one JSON result with the
   common header above plus task-specific fields. The skill tells
   the agent to copy this to `<basename>_<task>_<method>.json` next to the
   user's input (and, for tasks that produce structures, to copy the
   accompanying `.xyz` files too) so results persist outside the tmp work
   directory.
5. **Report** — the skill enumerates exactly which fields to surface and how
   (units, which `code_specific` keys matter, caveats to mention — e.g. that
   xtb/MOPAC energy zeros aren't comparable, or that a single surviving
   conformer after post-opt is the converged answer, not a bug). When a value is
   reported from the literature, the report follows
   [`rules/research-standards.md`](rules/research-standards.md).

This keeps the heavy lifting (geometry I/O, calculator setup, parsing program
output into a stable schema) in the one engine behind the MCP server, while the
`SKILL.md` encodes the *judgment calls* — when to ask the user for
clarification, what's worth flagging as a caveat, and how to translate raw JSON
into something a chemist would actually want to read.

> Regenerating the thin clients: `tools/build_skill_folders.py` rewrites each
> `skills/<name>/scripts/<name>.py` as the standard ~18-line MCP client (and refreshes
> `requirements.txt`). The engine and tool list live in `mcp_server/`.

## Validation & examples

Each skill ships a validated `examples/<name>/` folder with its own `README.md`
that compares the computed result to a **published value**. Per
[`rules/skill-standards.md`](rules/skill-standards.md) and
[`rules/research-standards.md`](rules/research-standards.md), every such
comparison must cite a **genuine, verified** source — experimental values trace
to the measuring paper, database values are labeled as such, and citations are
ACS-formatted and link-checked. Fabricating or misattributing a literature
value is prohibited.

## Notes and caveats

- **PM7 transition-metal parameters have limited coverage** — the schema flags this in `warnings` when relevant.
- **Redox potentials and conformer search are screening-grade**, not publication-grade; the skill output states this.
- **Literature values must be verified** — see [`rules/research-standards.md`](rules/research-standards.md).
