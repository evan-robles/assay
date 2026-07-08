# DESIGN — Inverting chemkit/ASSAY to self-contained skills over a thin FastMCP dispatcher

> Status: **Proposal (ideation)** — no code changes yet.
> Author of proposal: design captured for Evan S. Robles.
> Decisions locked with the maintainer (see §0).

## 0. Locked decisions

1. **Primitives are skills too.** `single-point-energy`, `geometry-optimize`,
   `vibrational-analysis` are full self-contained skills. Composite skills import
   their `run()` **in-process**. This is the purest form of the requested
   inversion — *everything is a skill*.
2. **The server introspects each skill's typed `run()`** to auto-build a
   strongly-typed MCP tool (no regression to a raw `args:list[str]` interface).
3. Deliverable now = **this design doc**. No implementation.

---

## 1. Problem statement

Today the dependency arrow is backwards for our goal.

```
MCP tool (mcp_server/server.py)  ──dispatch──▶  chemkit_engine.cli  ──▶  tasks/*.py  ──▶  calculators / integrity / schema
        ▲
        │  (MCP protocol via skills/_mcp_client.py)
skills/<name>/scripts/<name>.py   ──18-line thin client──┘
```

- **All chemistry (~12,200 LOC) lives in `mcp_server/chemkit_engine/`.**
  `cli.py` (1508), `calculators.py` (675), `integrity.py` (735), `schema.py`,
  `constants.py`, `io.py`, `resolve.py`, `result_schema.py`, `backends/pyscf/…`,
  and 20 `tasks/*.py` (e.g. `freq` 937, `confsearch` 1054, `scan` 852,
  `orbitals` 638).
- **`server.py`** hand-maintains a `TOOLS` dict of 20 entries and, for each,
  registers a FastMCP tool that shells out to
  `python -m chemkit_engine.cli <subcommand>` in an isolated subprocess.
- **Every skill is a ~18-line stub** that calls `skills/_mcp_client.py`, which
  spawns/connects the server and calls that skill's own tool. Skills carry **no**
  chemistry; their `requirements.txt` is just `mcp`.

We want to **invert** this: skills become the self-contained runnable unit, and
the MCP server *calls the skills*.

### 1.1 Two couplings that constrain the solution

**(A) A large shared engine.** Every task imports `..calculators`,
`..integrity`, `..schema`. Copying the engine into each skill folder would fork
12k lines twenty ways. Not acceptable.

**(B) Composite skills already call primitive skills, in-process.** Verified call
graph:

| Composite | depends on |
|---|---|
| `redox-potential` | sp, opt, freq |
| `reaction-energy` | sp, opt, freq |
| `reaction-profile` | opt, freq, ts, irc |
| `vibrational-analysis` (freq) | opt, confsearch |
| `conformational-analysis` (scan) | confsearch, opt |
| `transition-state` | freq |
| `pka-acidity` | freq |
| `binding-energy`, `logp-partition`, `solvation` | sp |
| `fukui-reactivity` | electrostatics |

The current composites literally do:

```python
# tasks/redox.py
from . import sp as sp_task, opt as opt_task, freq as freq_task
...
st = sp_task.run(...)      # in-process call
st = opt_task.run(...)
st = freq_task.run(...)
```

So the inversion must preserve **in-process skill→skill composition**, not force
each substep back through the MCP.

---

## 2. Reference: how AtomisticSkills solves the same tension

(Read from `learningmatter-mit/AtomisticSkills` source, not just its README.)

- **`src/` at repo root is a shared library**, imported as
  `from src.utils.mlips.loader import load_wrapper`. Physics primitives live
  there **once**.
- **Skill scripts are self-contained orchestrators.** e.g.
  `chem-bond-dissociation/scripts/calculate_bde.py` owns its own `argparse`, its
  own workflow (RDKit fragmentation + ASE `FIRE` relaxation + BDE bookkeeping +
  JSON/xyz output) and pulls only the *primitive* (`load_wrapper` →
  `wrapper.create_calculator()`) from `src/`. **Workflow in the skill; shared
  physics in the library.**
- **MCP servers are separate & thin** (`src/mcp_server/<name>_server.py`), one
  per conda env, exposing typed primitive tools. The README's
  "Tools → Skills → Workflows" is a *composition* hierarchy, not a call-through:
  skills are not thin clients of the servers.
- **Wiring is declarative.** `mcp_config.json` maps each server →
  `<conda-env>/bin/python -m src.mcp_server.X_server` with
  `PYTHONPATH=<repo root>`. `configure_mcp.py` patches local conda paths and
  installs the config into the agent (`.mcp.json`, `~/.codex/config.toml`, …).
  No pip-install; the `src`-on-PYTHONPATH layout is the mechanism.

**Lesson for chemkit:** self-containment is achieved at the *workflow* layer (the
skill owns orchestration + docs + examples + I/O); a shared **engine library**
owns the physics primitives so they are never duplicated. Our tasks are *already*
shaped like AtomisticSkills' skill scripts — they just live in the wrong folder.

---

## 3. Target architecture

```
chem-skills/                       (repo root; on PYTHONPATH, like AtomisticSkills' src/)
├── assay_core/                    ← RENAMED from chemkit_engine — shared physics LIBRARY (installed once)
│   ├── calculators.py             (build_calculator, apply_calc_to_atoms, …)   UNCHANGED physics
│   ├── integrity.py  schema.py  constants.py  io.py  resolve.py  result_schema.py   UNCHANGED
│   ├── backends/pyscf/…           UNCHANGED
│   ├── argkit.py                  ← NEW: shared argparse normalizers/aliases/gate+stdout options
│   │                                 (lifted from cli.py: _norm_method/_norm_tier/did-you-mean/…)
│   ├── runlog.py                  ← NEW: live-.out logging + gate/exit reporting (lifted from server.py::_run_engine)
│   └── ledger.py                  ← NEW: input_configs.yaml writer (skill-standards §Parameter Persistence)
│      (NOTE: cli.py and tasks/ MOVE OUT of the library — see below)
│
├── skills/
│   ├── __init__.py                ← NEW: makes skills an importable package (for composite imports)
│   ├── single-point-energy/
│   │   ├── SKILL.md               (frontmatter + sections; drops "thin client" language)
│   │   ├── scripts/
│   │   │   └── run.py             ← SELF-CONTAINED: typed run() (was tasks/sp.py) + __main__ argparse,
│   │   │                             imports assay_core primitives, writes JSON + input_configs.yaml + live .out
│   │   ├── requirements.txt       ← REAL deps now (ase, numpy, pyscf, xtb/mopac) — not just `mcp`
│   │   └── examples/<calc>/README.md + generated artifacts
│   ├── redox-potential/
│   │   └── scripts/run.py         ← composite: imports sp/opt/freq SKILL run()s in-process (§5)
│   └── …  (20 skill folders)
│
├── mcp_server/
│   └── server.py                  ← THIN FastMCP dispatcher: discovers skills, builds typed tools,
│                                     runs skills/<name>/scripts/run.py as isolated subprocess (§4)
│
├── assay.toml                     ← env map + discovery config
├── mcp_config.json                ← generated wiring (env→python→server), per AtomisticSkills
├── configure_mcp.py               ← installer that patches local conda paths (adapted from reference)
├── pyproject.toml                 ← installs assay_core; exposes `assay` / `chemkit` CLI
└── tools/  tests/  rules/  benchmarks/
```

Inverted dependency arrow:

```
MCP tool (thin)  ──runs subprocess──▶  skills/<name>/scripts/run.py  ──imports──▶  assay_core (shared physics)
                                              ▲
composite skill run.py  ──imports run()──────┘   (redox imports sp/opt/freq skills, in-process)
```

### 3.1 The three layers

1. **`assay_core` — the library (installed once).** Stateless *primitives*:
   calculator construction, integrity gating, schema, unit constants, geometry
   I/O, name resolution, plus the newly-extracted shared `argkit` / `runlog` /
   `ledger` helpers. **No task orchestration, no CLI, no per-skill knowledge.**
2. **Skills — self-contained workflows.** Each `scripts/run.py` contains the
   orchestration that used to live in `tasks/<x>.py::run()`, plus a `__main__`
   argparse block so it is runnable **stand-alone**:
   `python skills/single-point-energy/scripts/run.py --method xtb mol.xyz`
   with zero MCP involvement. *That standalone runnability is the definition of
   self-contained.*
3. **MCP server — thin dispatcher.** Discovers skills, introspects each `run()`
   to build a typed tool, and executes the skill's `run.py` in an isolated
   subprocess. Holds **no** chemistry and **no** hand-maintained tool table.

---

## 4. The skill contract & typed-tool generation

Each skill's `scripts/run.py` MUST expose:

```python
# skills/single-point-energy/scripts/run.py
from assay_core import calculators, integrity, schema, argkit, runlog, ledger

SKILL = "single-point-energy"        # matches folder name
ENV   = "anl_env"                    # conda env (mirrors SKILL.md `# Env:`)

def run(input_path: str, *,
        method: str,                 # typed, keyword-only — SAME signature tasks already have
        charge: int = 0,
        multiplicity: int = 1,
        solvent: str | None = None,
        tier: str | None = None,
        functional: str | None = None,
        basis: str | None = None,
        density_fit: bool = False,
        solvent_model: str = "ddcosmo",
        gate_integrity: bool = True,
        allow_unconverged: bool = False,
        cli: str = "") -> dict:
    """The workflow. (This body is today's tasks/sp.py::run, moved here.)"""
    ...

def build_parser() -> argparse.ArgumentParser:
    """Own argparse, reusing assay_core.argkit for shared flags/normalizers."""
    ...

if __name__ == "__main__":
    raise SystemExit(argkit.main_from(build_parser(), run))   # stand-alone entrypoint
```

The `run()` signatures already exist in exactly this typed, keyword-only shape
(verified in `tasks/sp.py`, `tasks/opt.py`, …), so **introspection is a natural
fit**. The server does:

```python
# mcp_server/server.py  (≈80 lines total)
mcp = FastMCP("assay", log_level="WARNING")

for skill in discover_skills(SKILLS_DIR):          # each has: name, description, run_path, signature
    tool_fn = make_typed_tool(skill)               # builds params from run() signature (types/enums/defaults)
    mcp.tool(name=skill.name, description=skill.description)(tool_fn)
```

- **Description** = SKILL.md frontmatter `description` + derived arg spec (keep
  today's behavior — no `--help` round-trip needed by the agent).
- **Typed params** = introspected from `run()` (or from `build_parser()`), so the
  MCP SDK validates types/enums *before* the call — preserving today's strong
  typed-tool UX (`method`, `charge`, `solvent`, `tier`, …).
- **`make_typed_tool`** wraps `_run_skill_subprocess(skill.run_path, argv, cwd)`,
  which retains everything valuable from today's `_run_engine`:
  - isolated **subprocess per call** (PySCF/matplotlib/chdir globals must not leak),
  - **live `.out` log** with the `tail -f` path surfaced **at launch**
    (calculation-reporting-standards #9),
  - **integrity-gate exit-code handling** (preserve structured result on nonzero),
  - **`CHEMKIT_REMOTE_HOST` ssh path** for login/compute-node clusters,
  - **structured JSON error envelopes** + per-tool stderr log line.
  These move into `assay_core.runlog` so a *stand-alone* skill run also gets the
  live log and gate reporting (not just runs via the server).

---

## 5. Composite skills (the crux) — in-process sibling imports

Locked decision: primitives are skills, composites import their `run()`.

```python
# skills/redox-potential/scripts/run.py
from skills.single_point_energy.scripts.run import run as sp_run
from skills.geometry_optimize.scripts.run     import run as opt_run
from skills.vibrational_analysis.scripts.run  import run as freq_run

def run(input_path, *, method, oxidized_charge, reduced_charge, ...):
    st_ox = opt_run(input_path, method=method, charge=oxidized_charge, ...)
    st_red = opt_run(input_path, method=method, charge=reduced_charge, ...)
    g_ox = freq_run(...); g_red = freq_run(...)
    ...   # (this is today's tasks/redox.py, with `sp_task.run`→`sp_run`, relocated)
```

- Hyphen→underscore: skill dirs are kebab-case; Python packages need
  underscores. Handled by naming the *package* dirs with underscores and keeping
  a kebab-case display name in the manifest, **or** a tiny import shim
  (`assay_core.skills.load("single-point-energy")`). Recommendation: underscore
  package dirs + kebab `name:` in frontmatter (one mapping, no runtime magic).
- **The MCP still calls only the top-level skill.** Composition is in-process,
  exactly as today — no server↔skill cycle, no subprocess-per-substep explosion.
- Self-containment stays meaningful: a composite skill folder holds its whole
  workflow; its only external references are `assay_core` primitives + its
  **declared** sibling skills (list them in frontmatter `depends_on:` for
  discoverability and dependency linting).

---

## 6. What happens to `cli.py` (1508 lines)

- **Per-task argparse** blocks → move into each skill's `build_parser()`
  (this is what makes a skill independently runnable).
- **Shared normalizers/aliases/gate-option/stdout-option/did-you-mean** →
  `assay_core/argkit.py`, reused by every skill's parser (no duplication,
  identical forgiving-input UX: `_norm_method`, `_norm_tier`, `_norm_mode`,
  `_norm_redox_ref`, alias maps, fuzzy `_suggest_subcommand`).
- **`chemkit` / `assay` human CLI** → thin front door that dispatches to
  `skills/<name>/scripts/run.py` (same target as the MCP). Preserves
  `chemkit sp --method xtb mol.xyz`, `chemkit --list-skills`, alias resolution,
  and `--help` passthrough. `--list-skills` now enumerates discovered skills.

---

## 7. Migration plan (incremental, test-guarded, reversible)

The regression suite (`tests/test_regression.py`, `tests/test_cli_interface.py`)
drives the thin clients today → it becomes the safety net. Every phase keeps the
tree green and is independently revertible; the engine is **never** forked.

1. **Rename & carve the library.** `chemkit_engine` → `assay_core`. Temporarily
   keep `cli.py`/`tasks/` under `assay_core/_legacy/` so imports still resolve.
   Add `pyproject` install of `assay_core`. (Pure rename — tests pass.)
2. **Extract shared infra** into `assay_core`: `argkit.py` (from `cli.py`),
   `runlog.py` (from `server.py::_run_engine`), `ledger.py`
   (`input_configs.yaml`). No behavior change.
3. **Convert ONE primitive end-to-end** — `single-point-energy`: author
   self-contained `scripts/run.py` (moved `tasks/sp.py` body + `build_parser()` +
   `__main__`), make the server discover+run it generically, delete its thin
   client. Run `sp` regression + example.
4. **Convert the remaining primitives** one at a time: `geometry-optimize`,
   `vibrational-analysis`, `conformer-search`, `build-from-smiles`,
   `name-to-smiles`, `electrostatics`.
5. **Convert composites**, rewriting `from . import X as X_task` →
   `from skills.X.scripts.run import run as X_run`: `binding`, `logp`,
   `solvation`, `fukui`, then `redox`, `reaction-energy`, `pka`, `transition-state`,
   `scan`, `reaction-profile`, `irc`, `orbitals`, `frontier`. One skill per PR,
   guarded by that skill's regression + example.
6. **Thin the server**: delete the hand-maintained `TOOLS` dict once discovery is
   manifest/introspection-driven (~80 LOC server).
7. **Rewire the CLI** front door to dispatch to skills; remove `assay_core/_legacy`.
   Update `rules/skill-standards.md` (add the `run.py`/`run()`/manifest contract;
   drop "thin client"), update README layout.
8. **Config & install**: add `assay.toml` + `mcp_config.json` +
   `configure_mcp.py`-style installer (adapted from AtomisticSkills) so the
   env→python→server wiring is declarative and each skill's `# Env:` is honored.

---

## 8. Trade-offs & risks

- **Skill→skill import coupling.** Composites now depend on sibling skill
  packages. Mitigation: declare `depends_on:` in frontmatter + a lint
  (`tools/lint_skills.py`) that verifies the dependency DAG is acyclic and all
  declared deps exist. (The current graph *is* a DAG.)
- **PYTHONPATH-based `src` layout** (as in AtomisticSkills) vs. pip-installing
  each skill. Recommendation: pip-install `assay_core` (real library) but keep
  `skills/` on PYTHONPATH (they are workflow scripts, not a distributable
  package). This mirrors the reference while giving the engine a clean install.
- **Isolation.** Keep subprocess-per-tool-call at the MCP boundary (PySCF /
  matplotlib / chdir globals demand it). In-process composition *inside* a skill
  is fine because the whole skill runs in one subprocess.
- **Rename churn** (`chemkit → ASSAY`, `chemkit_engine → assay_core`). Do it once,
  in phase 1, with a codemod; the project is already mid-rename to ASSAY.
- **Description/arg-spec drift.** Eliminated: the server derives both the
  description and the typed params from the skill itself (one source of truth).

---

## 9. Why this satisfies the requirement

- **Skills are self-contained**: each folder holds SKILL.md + a `run.py` that is
  runnable stand-alone (own argparse, own I/O, own `input_configs.yaml`, own live
  log), depending only on the shared `assay_core` primitives and declared sibling
  skills — exactly the AtomisticSkills shape.
- **The MCP calls the skills** (arrow inverted): the FastMCP server is a thin,
  discovery-driven dispatcher that runs `skills/<name>/scripts/run.py`; it owns no
  chemistry.
- **No duplication**: the 12k-line engine becomes a single installed library, not
  twenty copies.
- **No regression in agent UX**: tools stay strongly typed (via `run()`
  introspection), keep the live-log/gate/remote/error-envelope guarantees, and
  keep the `chemkit`/`assay` human CLI.

---

## 10. Functionality-preservation matrix (do NOT drop any of these)

Splitting the single `cli.py::main()` chokepoint into 20 per-skill parsers is the
main risk in this refactor: each engine guardrail would otherwise get 20 chances
to drift or be forgotten. The audit below enumerates **every** engine
guardrail/feature, where it lives today, and its new home. See
[`PRESERVATION-CHECKLIST.md`](PRESERVATION-CHECKLIST.md) for the per-skill
sign-off grid the migration PRs must tick.

| # | Feature | Today | New home | Notes |
|---|---|---|---|---|
| 1 | **Typed tool interface** — agent fills `method`/`charge`/… and cannot invent flags | `server.py::_typed_args_to_argv` + typed tool params | server builds typed params from `run()` introspection | keeps invalid invocations structurally impossible (SDK validates before call) |
| 2 | **Level-of-theory gate** — refuse `dft`/`hf` with no `--tier/--functional/--basis` unless `--accept-defaults` | `cli.py::main()` ~L1370-1400 | `assay_core.argkit.run_cli()` (in-engine, every skill) | calc-reporting non-negotiable #10; runs for ANY harness |
| 3 | **`choices=` hard whitelists** (`--method`,`--tier`,`--ref`,`--mode`,`--solvent-model`,`--postopt`,`--geometry`) | per-subparser in `cli.py` | `argkit` shared option builders reused by each `build_parser()` | skills MUST import the shared builder, not re-list choices |
| 4 | **Forgiving normalizers** (`_norm_method/_norm_tier/_norm_mode/_norm_redox_ref`) synonyms→canonical BEFORE `choices` | `cli.py` `type=` callables | `argkit` (shared `type=`) | prevents brittle hard-errors on reasonable spellings |
| 5 | **Did-you-mean** on unknown subcommand/flag (`_suggest_subcommand`, `_alias_to_canonical`) | `cli.py` | `argkit` fuzzy matcher fed by the **discovery registry** | discovery must publish the canonical+alias name list |
| 6 | **`--help-json` / `--list-skills`** machine discovery | `cli.py` | server + `assay` CLI regenerate from skill manifests | one source of truth = discovered skills |
| 7 | **`--stdout {json,path,none}`** channel modes + one-line pointer (`_stdout_summary`) | `cli.py` | `argkit.run_cli()` / `runlog` | token-efficient result reading (RESULT-READING.md) |
| 8 | **Integrity gate** hard-abort (non-converged, wrong imag count, charge mismatch); still writes partial to `--out`, exits nonzero; `--allow-unconverged` downgrades | `integrity.py` + `cli.py` catch | `integrity.py` stays in `assay_core`; `argkit.run_cli()` does the catch/exit | evidence preserved, headline number flagged untrustworthy |
| 9 | **fd-1 → fd-2 `dup2` redirect** — stops MOPAC/PySCF banners corrupting result JSON | `cli.py` `os.dup2(2,1)` | `argkit.run_cli()` (wraps every skill run) | fd-level, not just `sys.stdout`, because child procs inherit fd 1 |
| 10 | **Live `.out` log + `tail -f` at launch**, `CHEMKIT_REMOTE_HOST` ssh, error envelopes, per-tool log line | `server.py::_run_engine` | `assay_core.runlog` (used by server AND stand-alone runs) | calc-reporting non-negotiable #9 |
| 11 | **PreToolUse method-gate hook** — deterministic, model/harness-independent, per-session ack signature, carry-over catch | `.claude/hooks/chemkit-method-gate.sh` | **kept as-is** (redundant with #2 by design) | its `METHOD_REQUIRED_SUBCMDS` list regenerated from manifests by a lint |
| 12 | **`input_configs.yaml` parameter persistence** (skill-standards) | (partly ad hoc today) | `assay_core.ledger`, called in `run_cli()` | every skill writes full effective params next to `--out` |
| 13 | **`--out` default naming** (`<stem>_<task>_<method>.json`) + `--out` always written even on gate failure | `cli.py`/tasks | `argkit`/each `run()` | stable output contract |
| 14 | **`--verbose` → `CHEMKIT_PYSCF_VERBOSE` env**, `--density-fit`, `--solvent-model` threading | `cli.py` `pyscf_kwargs` | shared `_add_chem_options` + `run()` kwargs | unchanged knobs, same defaults (df OFF, ddcosmo) |

### 10.1 The shared spine — why 20 parsers can't drift

The chokepoint is preserved, not deleted — it **moves** from `cli.py::main()` to a
single mandatory entrypoint that every skill calls:

```python
# assay_core/argkit.py
def run_cli(parser, run_fn) -> int:
    args = parser.parse_args()                 # (3) choices + (4) normalizers already applied via type=
    enforce_level_of_theory_gate(args, parser) # (2) refuse silent dft/hf defaults
    resolve_gas_phase_synonyms(args)           # solvent 'gas'/'none'/'vacuum' -> None
    with fd_stdout_to_stderr():                # (9) protect result JSON from banners
        result = call_with_integrity(run_fn, args)   # (8) gate wrap, --allow-unconverged, --out on failure
    ledger.write_input_configs(args, result)   # (12) parameter persistence
    return emit(result, mode=args.stdout)      # (7) json/path/none  + (10) live-log path

# every skill's scripts/run.py
if __name__ == "__main__":
    raise SystemExit(argkit.run_cli(build_parser(), run))
```

Skills are *physically unable* to bypass gates 2/7/8/9/12 because `run_cli` is
their only `__main__` path, and their `build_parser()` is *required* to compose
`argkit._add_chem_options` / `_add_gate_option` / `_add_stdout_option` (the same
builders used today). Composite skills that import a sibling's `run()` call the
**function**, which already contains the integrity gate (#8) internally — so
composed sub-steps stay gated too, exactly as today.

### 10.2 Two lints guard against drift (`tools/lint_skills.py`)

1. **Spine lint:** every `skills/*/scripts/run.py` (a) exposes a typed
   keyword-only `run()`, (b) has a `build_parser()` composing the shared
   `argkit` option builders, (c) uses `argkit.run_cli` as its `__main__`. Fail CI
   otherwise.
2. **Registry-sync lint:** the discovery registry (canonical names + aliases),
   the server tool list, the `assay --list-skills` output, and the PreToolUse
   hook's `METHOD_REQUIRED_SUBCMDS` are all regenerated from the skill manifests
   and must match — so #5, #6, #11 can never silently diverge from the actual
   skill set.

### 10.3 Gate redundancy is intentional (kept)

Feature #2 (in-engine) and #11 (PreToolUse shell hook) both enforce the
level-of-theory rule, on purpose: the engine gate protects **any** model/harness
that runs a calc; the shell hook adds a deterministic, model-independent block
plus the session carry-over catch ("we just used xtb, silently reuse xtb"). Both
are retained — defense in depth, unchanged from today.
