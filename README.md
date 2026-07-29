# ASSAY

**A**gentic **S**imulation **S**uite for **A**utomated chemistr**Y**

*Self-contained, guardrailed chemistry skills over the Model Context Protocol*

A computational chemistry suite powered by **xtb** (GFN2), **MOPAC** (PM7), and
**PySCF** (DFT / HF), with optional implicit solvation (ALPB / COSMO / PCM). ASE
provides the geometry-I/O and calculator-driver layer; the quantum chemistry runs
in those backends. Twenty task-focused, self-contained skills share one physics
library (`assay_core`) and are exposed over the open Model Context Protocol by a
thin discovery-driven server.

## Layout

**Everything is a skill.** Each of the 20 skills is *self-contained*: it owns its
whole workflow in `scripts/run.py` and depends only on the shared physics library
`assay_core`. A thin **MCP server** discovers the skills and runs each skill's
`run.py` directly — the dependency arrow points from the server *to* the skills,
not the other way around. The physics lives once (in `assay_core`), never
duplicated into the skills.

```
~/chem-skills/
├── rules/
│   ├── skill-standards.md            # how to author one atomic skill
│   ├── research-standards.md         # how to find/verify/cite literature (binding)
│   └── workflow-standards.md         # how to compose skills into a vetted workflow
├── assay_core/            # the shared physics LIBRARY (installed once, on PYTHONPATH)
│   ├── calculators.py integrity.py schema.py io.py resolve.py constants.py
│   ├── argkit.py          # shared arg spine: normalizers, gates, run_cli() entrypoint
│   ├── runlog.py          # live .out logging + subprocess orchestration
│   ├── ledger.py          # input_configs.yaml parameter persistence
│   ├── discovery.py       # walks skills/ + reads each SKILL_NAME/SUBCOMMAND manifest
│   ├── cli.py             # `assay_core.cli` engine CLI (+ describe_parser introspection)
│   ├── tasks/             # thin shims re-exporting each skill's run() (single copy of physics)
│   └── backends/pyscf/    # DFT/HF backend
├── mcp_server/
│   └── server.py          # thin FastMCP dispatcher — DISCOVERS skills, runs their run.py
├── skills/
│   ├── single_point_energy/                   # underscore package dir (importable)
│   │   ├── SKILL.md                           # frontmatter (kebab `name:`) + sections
│   │   ├── scripts/run.py                     # SELF-CONTAINED: run() + build_parser() + run_cli
│   │   ├── requirements.txt                   # real deps (assay_core, ase, numpy, pyscf)
│   │   └── examples/<calc-name>/              # README.md + generated .json/.xyz/.png
│   └── (20 skill folders total)
├── assay.toml            # declarative config (conda env, server entry, skills dir)
├── configure_mcp.py      # generates mcp_config.json wiring from assay.toml
├── workflows/            # multi-step procedures chaining skills (rules/workflow-standards.md)
├── tools/lint_skills.py  # SKILL.md + spine + registry-sync lints
└── tests/                # regression suite
```

Each skill's `scripts/run.py` exposes the inverted-architecture **contract**:

- a **typed `run()`** — the workflow (keyword-only scientific args);
- a **`build_parser()`** composing the shared `assay_core.argkit` option builders
  (so choices/normalizers/gates are identical across every skill);
- a **discovery manifest** — module-level `SKILL_NAME` (kebab display name) and
  `SUBCOMMAND` (engine subcommand);
- a `__main__` that routes through `argkit.run_cli(...)`, the single spine that
  applies the level-of-theory gate, the integrity gate, the live `.out` log, and
  `input_configs.yaml` persistence — so a skill *cannot* bypass a guardrail.

It is runnable stand-alone —
`python skills/single_point_energy/scripts/run.py --method xtb mol.xyz` — with
every guardrail intact; the MCP server and the `assay` CLI run this same file.
The server builds each typed MCP tool by introspecting the skill's own
`build_parser()`, and its tool registry is *discovered*, never hand-maintained
(`tools/lint_skills.py --registry` enforces that discovery, the server, and the
method-gate hook stay in sync).

Skill folders are **underscore-named** (importable Python packages, so a
composite skill can import a sibling's `run()` in-process) with the **kebab**
display name in the SKILL.md frontmatter. They conform to
`rules/skill-standards.md` (frontmatter `name`/`description`/`category`,
Goal/Instructions/Examples/Constraints/References, and a validated `examples/`).
The MCP server speaks the open protocol, so **any** MCP-capable client can drive
it. See `mcp_server/README.md` for a generic client config.

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

All 20 skills (each is also an MCP tool of the same name, mapping to the engine
subcommand shown). Each MCP tool advertises **its own typed arguments** —
generated from the engine's argparse via `describe_subcommand()` — so an agent
sees exactly the fields a skill takes (e.g. `redox-potential` shows
`ox_charge`/`red_charge`/`ref`; `pka-acidity` shows `ha`/`a_minus`) rather than a
generic argument bag, and cannot invent a flag or inject one a skill does not
accept:

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
| `build-from-smiles` | `build` | SMILES → 3D `.xyz` (SMILES-only, optional QM refine; for a name use `name-to-smiles` first) |
| `name-to-smiles` | `resolve` | Molecule name → SMILES from online sources, with source attribution and an ACS citation |
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

Run a skill directly from the shell — either the self-contained `run.py`, or the
`assay` CLI front door (both run the same skill, with every guardrail):

```bash
# self-contained skill script
python skills/single_point_energy/scripts/run.py --method xtb --solvent water mol.xyz

# equivalent via the `assay` front door (accepts the kebab name or its subcommand)
assay single-point-energy --method xtb --solvent water mol.xyz
assay sp --help
```

Or start the MCP server and connect any MCP-capable client:

```bash
assay-mcp
```

```json
{ "mcpServers": { "assay": { "command": "assay-mcp" } } }
```

On connection the server injects its operating rules into the client as MCP
server instructions (Claude Code renders these as a context block automatically),
so every session is oriented on ASSAY's skills and integrity rules with no
per-session setup. The text lives in `mcp_server/INSTRUCTIONS.md` and ships with
the package.

### Claude Code

Register the server once; it then connects automatically in every session and
loads the ASSAY context:

```bash
claude mcp add assay --scope user -- assay-mcp
```

Use `--scope user` to make it available in all projects, or `--scope project`
(writes `.mcp.json` into the repo, shareable with collaborators) to scope it to
this project. Verify with `/mcp` inside Claude Code.

See `mcp_server/README.md` for uvx, OpenAI Agents SDK, and run-from-checkout
configurations, or run `python configure_mcp.py --install` to generate the wiring
from `assay.toml` and merge it into `./.mcp.json`.

### Running on Aurora (remote compute nodes)

Any skill can run on a remote compute node transparently to the agent: the run
ssh's to the node and streams the result JSON back **synchronously**, in the same
tool call. The result carries a `remote_host` field naming the node it ran on.
First hold an allocation:

```bash
# publishes the compute-node hostname(s) to .sweep_nodes
qsub -l select=1 tools/aurora_nodeholder.pbs
```

**Per call (the agent chooses):** every skill's MCP tool has a `run_on` parameter
— `local` (default) or `aurora`. The agent sets `run_on="aurora"` on the calls it
wants remote; the host is resolved from `assay.toml [remote]` (its `host`, else
the first line of `.sweep_nodes`). No wiring regen needed. Requesting `aurora`
with no node available returns an error, never a silent local run.

**Session-wide (every call remote):** bake the host into the server env instead:

```bash
python configure_mcp.py --remote --install          # auto-picks from .sweep_nodes
# …or pin a host explicitly:
python configure_mcp.py --remote-host x4712c0s1b0n0 --install
```

**Long jobs (async):** for DFT that can't finish in the tool timeout, submit a
real PBS job and collect later:

```bash
python tools/aurora_submit.py submit --skill sp --skill-args --method dft --tier standard benzene.xyz
python tools/aurora_submit.py status  <jobid>
python tools/aurora_submit.py collect <jobid>
```

This injects `CHEMKIT_REMOTE_HOST` into the server env; the `runlog` layer does
the ssh (assuming a shared filesystem, true on Aurora). It fits calcs that finish
within the tool timeout (~1 h) — xtb / PM7 / small DFT. For long DFT use the
**async** submit/collect flow in `tools/aurora_submit.py` instead. Keep
`name-to-smiles` / `build-from-smiles` local (compute nodes have no outbound
internet for the PubChem/OPSIN lookups).

## Example commands

```bash
# via the `assay` front door (accepts the kebab skill name or its subcommand);
# equivalently `python skills/<pkg>/scripts/run.py <args>`.
assay single-point-energy --method xtb --solvent water mol.xyz
assay geometry-optimize   --method mopac --charge 0 mol.xyz
assay single-point-energy --method dft --tier standard mol.xyz
assay vibrational-analysis --method xtb mol_opt.xyz
assay binding-energy --method xtb --monomer A.xyz --monomer B.xyz complex.xyz
assay redox-potential --method xtb --ox-charge 0 --red-charge -1 --ref SHE --solvent water mol.xyz
assay pka-acidity --method xtb --mode reference --ha HA.xyz --a-minus A_minus.xyz
assay reaction-profile --method xtb --reactant reactant.xyz --product product.xyz --ts-guess ts.xyz
assay conformer-search --method xtb mol.xyz
assay build-from-smiles 'CCO'   # SMILES → 3D xyz (use name-to-smiles for a name)
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
output into a stable schema) in the shared `assay_core` library, while each
skill's `SKILL.md` encodes the *judgment calls* — when to ask the user for
clarification, what's worth flagging as a caveat, and how to translate raw JSON
into something a chemist would actually want to read.

> Skill contract: each `skills/<pkg>/scripts/run.py` exposes a typed `run()`, a
> `build_parser()`, the `SKILL_NAME`/`SUBCOMMAND` discovery manifest, and a
> `__main__` routing through `assay_core.argkit.run_cli`. `tools/lint_skills.py
> --all` enforces this (SKILL.md + spine + registry-sync); the server and the
> `assay` CLI discover skills from disk, so there is no generator to re-run.

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
