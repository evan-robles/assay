# Changelog

All notable changes to ASSAY are recorded here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project version is
single-sourced from `assay_core/__init__.py::__version__`.

## [Unreleased]

### Added
- **`--outdir`** on every skill: routes *every* artifact of a run — result JSON,
  live `.out` log, config ledger, and all sidecars (`.xyz`, `.molden`, `.cube`,
  trajectories, plots) — into one directory. Implemented as a `chdir` in the
  `run_cli` spine because artifact paths derive two different ways (some off the
  result path, others off the input basename), so there is no single path
  parameter to thread. Input paths resolve against the caller's original cwd.
  Default is unchanged (the current directory), so this is purely opt-in.
- **`assay_core/env.py`**: single accessor for tunables, reading `ASSAY_*` first
  and falling back to the legacy `CHEMKIT_*` spelling, so existing shells, MCP
  client configs and PBS scripts keep working through the rename.
- **`Makefile`**: `install`, `test`, `test-all`, `lint`, `serve`, `clean`,
  `clean-runs` — encodes the editable-install/PYTHONPATH setup the suite needs.
- Regression tests for `--outdir` (including the front-door/MCP subprocess path,
  where the parent tees the live log) and for per-result config ledgers.

### Changed
- **`input_configs.yaml` → `<result-stem>_input_configs.yaml`.** The fixed name
  was silently overwritten by the next run in the same directory, so a folder of
  uniquely-named results carried a single ledger describing whichever ran last —
  voiding the reproducibility guarantee the file exists to provide.
- **chemkit → ASSAY** across the library: prose, docstrings, private `_chemkit_*`
  attributes (now `_assay_*`), the distribution name (`chemkit-mcp` →
  `assay-chem`), and the conda env (`chemkit` → `assay`). The `chemkit` and
  `chemkit-mcp` console scripts are retained as aliases, and `CHEMKIT_*` env vars
  are still honored (see `assay_core/env.py`).
- `pyproject.toml` now discovers packages instead of hand-listing them, and
  **includes `skills*`** — previously omitted, so a built wheel shipped without
  the skill packages that composites import and that discovery walks.

### Fixed
- **CI was referencing files that no longer exist** after the `assay_core`
  refactor and could not have been passing: `tools/build_skill_folders.py` (gone;
  replaced by `tools/lint_skills.py --all`), `mcp_server/chemkit_engine` in the
  TOOLS↔CLI check, a `--cov=chemkit_engine` target that measured nothing, and a
  `.[qm]` extra that is not defined. `tests/test_cli_interface.py` now runs in CI.
- **`benchmarks/spec_schema.py`** imported `server` via a path insert that the
  refactor removed (`ModuleNotFoundError: No module named 'server'`).
- **Test suite reported ~50 failures on a plain working copy.** Skill
  subprocesses could not import `assay_core` (no editable install), and
  `_have_pyscf()` inferred availability from a skill run's stderr — the run died
  on `assay_core` first, and later on a `/dev/null` geometry check, before ever
  reaching the pyscf import gate, so pyscf was reported present and every DFT/HF
  test failed instead of skipping. `tests/conftest.py` now exports `PYTHONPATH`
  and the probe imports pyscf directly. Failures: 53 → 1.
- `visualize-orbitals` tests now skip without pyscf (its molden I/O needs it on
  every backend, including xtb/mopac).

---

## [Unreleased — earlier, pre-rename]

Entries below predate the chemkit → ASSAY rename and the skill-inversion
refactor; module paths are given as they were at the time except where a path is
named as current.

### Added
- **Canonical result-schema layer** (`assay_core/result_schema.py`):
  a typed (TypedDict, no pydantic) layer wired into `integrity.finalize()` that
  additively stamps a discoverable headline pointer (`headline_field`/`value`/
  `units`) + `schema_version` onto every result and aliases the equivalent
  `electronic_energy_eV`↔`total_energy_eV`. Warning-severity shape checks fold
  into the integrity block; no existing run can be broken.
- **TOOLS↔CLI consistency check** (`assay_core.cli.check_tools_cli_consistency`,
  via the extracted `build_parser()`): catches a server `TOOLS` entry that has no
  engine subparser (which would silently break the tool).
- **Static fidelity-spec validator** (`benchmarks/spec_schema.py`): validates every
  `*.spec.json` shape, that `report_value_field` is the canonical headline field
  the engine emits, that `intended.method` is real, and that referenced input
  geometries exist. Surfaced a real gap: ~32 specs reference fixture `.xyz` files
  that do not yet exist (transition-state / IRC / reaction-profile / binding).
- **SKILL.md linter** (`tools/lint_skills.py`): enforces the skill-standards
  frontmatter/section/author contract.
- **Thin-client drift check**: the 20 per-skill scripts are verified to match the
  generator (`tools/build_skill_folders.py`) exactly.
- New regression tests for all of the above.
- **`workflows/` directory** with the first workflow,
  `workflows/name-to-3d-structure.md`: the two-step `name-to-smiles` →
  `build-from-smiles` procedure for turning a molecule name into a 3D `.xyz`,
  authored per `rules/workflow-standards.md`.
- **Per-skill typed MCP argument surfaces** (`mcp_server/chemkit_engine/arg_spec.py`):
  each MCP tool now advertises its OWN typed parameters (redox-potential exposes
  `ox_charge`/`red_charge`/`ref`; pka-acidity exposes `ha`/`a_minus`/`mode`;
  binding-energy exposes a `monomer` list; …) instead of a shared generic
  `xyz/method/charge/.../extra_args` signature. Generated from the engine's own
  argparse via `cli.describe_subcommand()` (drift-proof single source), consumed
  by BOTH the server (synthesized per-tool `__signature__`) and the benchmark
  driver (`_CHEMKIT_TOOL` schema). This fixes the dominant many-arg-skill failure
  mode (agents filling `xyz`/`charge` on multi-species tasks, or inventing flags):
  the required skill-specific flags are now first-class typed fields, a value for
  a field the skill lacks is never injected, and an unknown `extra_args` flag is
  rejected with a suggestion. Requiredness is enforced by the engine (schema
  fields stay optional so the back-compat `args` raw-token path still works).

### Changed
- **`build-from-smiles` is now SMILES-only.** It no longer resolves molecule
  names online; a non-SMILES input (a name, or any string Open Babel cannot parse
  as SMILES) is rejected up front with an error pointing at `name-to-smiles`. Name
  → structure is the explicit `name-to-smiles` → `build-from-smiles` workflow. The
  resolver and the `name-to-smiles` skill are unchanged. The `build-from-name`
  subcommand alias was dropped; `build` result JSON no longer carries a
  `smiles_source` block. The `build-validation` benchmark suite was converted to
  SMILES inputs (adversarial name cases became invalid-SMILES `expect: failure`
  cases) and its engine-references regenerated.
- **Single-sourced energy-unit constants**: `KCAL_TO_EV`/`EV_TO_KCAL`/`CAL_TO_EV`
  now live only in `schema.py`; six task modules that redefined `1/23.0605…`
  locally (diverging at ~1e-13) import them. Unified to the CODATA value; shift is
  far below all tolerances.
- **Co-located solvent tables** in `schema.py` (one documented home). They remain
  three distinct tables (per-backend ε genuinely differ). **PySCF ε verified to
  be the Gaussian SCRF/PCM default set** (https://gaussian.com/scrf/, all values
  matched); MOPAC ε documented honestly as rounded ~25 °C reference values.
- **Version single-sourced** from `chemkit_engine.__version__`: `pyproject.toml`
  reads it dynamically and `mcp_server.__version__` re-exports it (no more
  three-place drift).

## [1.0.0]
- Initial chemkit MCP server: 20 skills over the open Model Context Protocol,
  unified engine (xtb / MOPAC / PySCF / Open Babel), the computation-side
  integrity gate, and the three-layer agent-fidelity benchmark.
