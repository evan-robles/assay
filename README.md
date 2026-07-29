# ASSAY

**A**gentic **S**imulation **S**uite for **A**utomated chemistr**Y**

Twenty computational-chemistry skills — single-point energies, geometry
optimization, frequencies, pKa, redox potentials, reaction profiles, and more —
runnable from the shell or driven by an AI agent over the Model Context Protocol.
Backends: **xtb** (GFN2), **MOPAC** (PM7), and **PySCF** (DFT / HF), with implicit
solvation.

Every result is validated by an integrity gate and reported with its full method
provenance, so a number is never handed back without the level of theory, geometry
source, and convergence state that produced it.

## Quick start

```bash
pixi install                                          # get pixi at pixi.sh
pixi run assay single-point-energy --method xtb water.xyz
pixi run assay-mcp                                    # or serve to an agent
```

Conda works too: `conda env create -f environment.yml && conda activate chemkit`,
then `assay ...` without the `pixi run` prefix.

`assay <skill> --help` lists a skill's arguments; `assay --list-skills` lists all.
No geometry? `assay build-from-smiles 'O' --out-xyz water.xyz`.

## Skills

Each skill is also an MCP tool of the same name. Every task takes
`--method {xtb, mopac, dft, hf}` unless noted.

| Skill | What it does |
|-------|--------------|
| `single-point-energy` | Total electronic energy + frontier properties at a fixed geometry |
| `geometry-optimize` | Relax to a local minimum |
| `vibrational-analysis` | Frequencies, ZPE, thermochemistry; minimum-vs-TS check |
| `binding-energy` | Interaction energy of a complex vs. its fragments |
| `redox-potential` | Oxidation/reduction potential vs. SHE / Ag/AgCl / Fc⁺/Fc |
| `conformer-search` | Sample low-energy conformers; ranked ensemble |
| `frontier-orbitals` | HOMO/LUMO energies, gap, Koopmans descriptors |
| `electrostatics` | Dipole moment + atomic partial charges |
| `solvation` | Electronic solvation free energy in an implicit solvent |
| `logp-partition` | Octanol–water logP from a solvation-free-energy cycle |
| `reaction-profile` | Activation/reaction ΔG, IRC check, annotated diagram |
| `pka-acidity` | Aqueous pKa via a thermodynamic cycle |
| `build-from-smiles` | SMILES → 3D `.xyz` (optional QM refine) |
| `name-to-smiles` | Molecule name → SMILES, with source citation |
| `fukui-reactivity` | Per-atom Fukui functions + Morell dual descriptor |
| `transition-state` | Locate a first-order saddle; one-imaginary-mode check |
| `intrinsic-reaction-coordinate` | Walk down from a TS; forward/reverse paths |
| `reaction-energy` | ΔE/ΔH/ΔG of a balanced reaction |
| `conformational-analysis` | Relaxed dihedral scan → rotation barrier |
| `visualize-orbitals` | Molden + optional cube files for MO isosurfaces |

### Examples

```bash
assay single-point-energy --method dft --tier standard mol.xyz
assay geometry-optimize --method mopac mol.xyz
assay binding-energy --method xtb --monomer A.xyz --monomer B.xyz complex.xyz
assay redox-potential --method xtb --ox-charge 0 --red-charge -1 --ref SHE --solvent water mol.xyz
assay pka-acidity --method xtb --mode reference --ha HA.xyz --a-minus A_minus.xyz
assay reaction-profile --method xtb --reactant r.xyz --product p.xyz --ts-guess ts.xyz
assay build-from-smiles 'CCO'
```

Each run writes one JSON result (headline value, method provenance, warnings,
integrity verdict) and streams a live `.out` log you can `tail -f`.

## Methods

| `--method` | Level of theory | Solvation |
|------------|-----------------|-----------|
| `xtb` | GFN2-xTB (semiempirical, fast) | ALPB |
| `mopac` | PM7 (semiempirical) | COSMO |
| `dft` / `hf` | PySCF | PCM |

DFT tiers via `--tier`: `fast` (r2SCAN/def2-SVP), `standard` (B3LYP/def2-TZVP),
`accurate` (ωB97M-V/def2-QZVPP). Override with `--functional` / `--basis`. Common
flags: `--charge`, `--mult`, `--solvent` (gas phase if omitted), `--out`.

> PM7 transition-metal coverage is limited, and redox/conformer search are
> screening-grade — both are flagged in the result `warnings` when relevant.

## Use with an agent

Start the server and point any MCP-capable client at it:

```bash
assay-mcp
```

```json
{ "mcpServers": { "assay": { "command": "assay-mcp" } } }
```

For Claude Code: `claude mcp add assay --scope user -- assay-mcp`. On connection
the server injects ASSAY's operating rules as MCP instructions, so the agent is
oriented on the skills and integrity rules with no per-session setup.

Each tool advertises its skill's exact typed arguments (e.g. `redox-potential`
exposes `ox_charge`/`red_charge`/`ref`), so an agent can't invent a flag or call a
skill with fields it doesn't accept. See
[`mcp_server/README.md`](mcp_server/README.md) for uvx, the OpenAI Agents SDK, and
run-from-checkout configs.

### Running on Aurora

Any skill can run on a remote compute node in the same tool call — it ssh's to the
node, streams the result back, and stamps the result with the `remote_host` it ran
on. Hold an allocation, then choose per call or session-wide:

```bash
qsub -l select=1 tools/aurora_nodeholder.pbs        # publishes .sweep_nodes
```

- **Per call:** every MCP tool has a `run_on` parameter (`local` default, or
  `aurora`); the host is read from `assay.toml [remote]` or `.sweep_nodes`.
- **Session-wide:** `python configure_mcp.py --remote --install`.
- **Long DFT** (beyond the ~1 h tool timeout): submit an async PBS job with
  `tools/aurora_submit.py submit --skill sp --skill-args --method dft ...`.

Keep `name-to-smiles` / `build-from-smiles` local — compute nodes have no
outbound internet.

## Installation

The `xtb`, `mopac`, and `openbabel` backends are conda-forge only (not on PyPI).

```bash
pixi install                                  # everything, all platforms
conda env create -f environment.yml           # or conda
conda install -c conda-forge xtb xtb-python mopac openbabel rdkit && pip install -e .  # or pip + conda backends
```

**Windows:** all backends work except PySCF (no Windows build), so `--method
dft`/`hf` require Linux/macOS or WSL.

## Architecture

Everything is a skill. Each `skills/<name>/scripts/run.py` owns its whole workflow
and depends only on the shared physics library `assay_core`. A skill runs the same
way everywhere — its `run.py` directly, the `assay` CLI, or an MCP tool — always
through one spine (`argkit.run_cli`) that applies the level-of-theory gate, the
integrity gate, and the live `.out` log, so no path can skip a guardrail. The MCP
server discovers skills from disk and builds each tool's typed schema from the
skill's own argument parser; there is no hand-maintained tool table.

```
chem-skills/
├── assay_core/     # shared physics library (calculators, integrity, schema, backends)
├── skills/         # 20 self-contained skills — each a scripts/run.py + SKILL.md
├── mcp_server/     # thin MCP server that discovers and runs the skills
├── rules/          # standards for skills, research/citations, and workflows
├── tools/          # lint_skills.py, aurora_submit.py, and other dev/ops helpers
└── tests/          # regression suite
```

Each skill pairs the runnable `run.py` with a `SKILL.md` that tells an agent how
to drive it: what it's for, which arguments are required, when to ask the user,
and which result fields to report. Authoring a skill is governed by
[`rules/skill-standards.md`](rules/skill-standards.md); `tools/lint_skills.py
--all` enforces the contract.

## Research integrity

Any literature lookup, citation, or comparison to a published value is governed by
[`rules/research-standards.md`](rules/research-standards.md), which is binding. It
requires a hard verification gate — every DOI/URL is link-checked and its metadata
matched to the citation before it's shown — plus honest experimental-vs-computed
provenance and ACS-formatted citations. Unverifiable sources get an honest "not
found," never a guess. Each skill's `examples/` folder validates against a
published value under this gate.

The three standards ([`skill`](rules/skill-standards.md),
[`research`](rules/research-standards.md),
[`workflow`](rules/workflow-standards.md)) load automatically for the matching
task.
