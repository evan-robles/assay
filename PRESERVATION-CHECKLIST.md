# PRESERVATION-CHECKLIST — no engine functionality dropped during the skill-inversion migration

Companion to [`DESIGN.md`](DESIGN.md) §10. Each skill's migration PR must tick
every applicable box. A box is **N/A** when the skill does not expose that knob
(e.g. `name-to-smiles` has no `--method`, so #2/#3-method are N/A). "Verified"
means an actual run reproduced the pre-migration result JSON (same numbers).

Legend: ☐ not done · ☑ done · N/A not applicable

## Global (once, not per-skill)

- ☑ `chemkit_engine` → `assay_core` rename; `assay_core` pip-installable (repo-root package, single `pip install -e .`; phase 1)
- ☑ `assay_core.argkit` holds: normalizers (#4), `choices=` builders (#3), LoT gate (#2), `_add_chem_options` / `_add_gate_option` / `_add_stdout_option`, `run_cli()` spine (phase 2)
- ☑ `assay_core.runlog` holds: live `.out` + `tail -f` at launch (#10), `CHEMKIT_REMOTE_HOST` ssh (#10), error envelopes (#10), per-tool log line (#10), fd-1→fd-2 redirect (#9) — used by the server AND, via `run_cli`, by stand-alone skill runs (phase 2/3)
- ☑ `assay_core.ledger.write_input_configs` (#12) (phase 2; wired into `run_cli` phase 3)
- ☑ `integrity.py` stays in `assay_core`; `run_cli` performs the catch/exit + `--allow-unconverged` (#8) (phase 2)
- ☐ Discovery registry publishes canonical names + aliases; feeds did-you-mean (#5), `--list-skills`/`--help-json` (#6) *(still via cli.py SUBCOMMAND_ALIASES; unchanged)*
- ☐ Server (`mcp_server/server.py`) builds typed tools via `run()` introspection (#1); no hand-maintained `TOOLS` dict *(still arg_spec-driven; server now runs converted skills' run.py via CONVERTED_SKILLS — full introspection is a later phase)*
- ☐ `assay`/`chemkit` CLI front-door dispatches to `skills/<n>/scripts/run.py`; `--list-skills`, alias resolution, `--help`/`--help-json` passthrough (#6) *(front door still routes via `-m assay_core.cli`; both `assay` and `chemkit` entry points exist — phase 1)*
- ☐ PreToolUse hook `chemkit-method-gate.sh` retained; `METHOD_REQUIRED_SUBCMDS` regenerated from manifests (#11) *(retained + matches `assay_core.cli`/`assay` — phase 1)*
- ☐ `tools/lint_skills.py`: spine lint (#10.2-1) + registry-sync lint (#10.2-2) wired into CI *(lint_skill now accepts underscore package dirs — phase 3)*
- ◑ Full `tests/` suite (test_regression.py, test_cli_interface.py) green *(at baseline: 146 pass / 1 skip / 1 pre-existing ferrocene-spec fail, unrelated to this refactor)*
- ☐ `rules/skill-standards.md` + `README.md` updated (drop "thin client"; add `run()`/`build_parser()`/`run_cli` contract) *(single-point-energy SKILL.md updated; global docs later phase)*

## Per-skill grid

For each skill, confirm the entrypoint spine, then the applicable guardrails.

| Skill | typed `run()` (#1) | uses `run_cli` spine (#2,7,8,9,12) | shared `choices`+normalizers (#3,#4) | `--stdout` modes (#7) | integrity gate verified (#8) | fd-redirect: JSON clean (#9) | live `.out` path (#10) | `input_configs.yaml` (#12) | `--out` default+on-fail (#13) | regression + example reproduce numbers |
|---|---|---|---|---|---|---|---|---|---|---|
| single-point-energy | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| geometry-optimize | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| vibrational-analysis | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| conformer-search | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| conformational-analysis | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| build-from-smiles | ☑ | ☑ | ☑ (`--opt` not `--method`) | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| name-to-smiles | ☑ | ☑ | N/A (no QM knobs) | ☑ | N/A | ☑ | ☑ | ☑ | ☑ | ☑ |
| binding-energy | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| redox-potential | ☑ | ☑ | ☑ (+`--ref`,`--mode`) | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| pka-acidity | ☑ | ☑ | ☑ (+`--mode`; `--accept-defaults`) | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| logp-partition | ☑ | ☑ | ☑ (solvent pinned) | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| solvation | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| frontier-orbitals | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| electrostatics | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| fukui-reactivity | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| transition-state | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| intrinsic-reaction-coordinate | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| reaction-energy | ☑ | ☑ | ☑ (+`--mode`) | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| reaction-profile | ☑ | ☑ | ☑ (+`--accept-defaults`) | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| visualize-orbitals | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |

## Composite-specific (skill→skill in-process, #5 of DESIGN)

Each composite must import sibling **skill** `run()`s (not the old engine tasks),
and its declared `depends_on:` must match its imports (DAG lint).

| Composite | imports (sibling skill run()) | `depends_on:` declared | sub-step still integrity-gated (#8) |
|---|---|---|---|
| vibrational-analysis | opt, conformer-search | ☐ (composes via the opt/confsearch task shims → skills, not yet direct sibling imports) | ☑ (sub-steps run through the skill run()s, which gate) |
| conformational-analysis | conformer-search, opt | ☐ (via task shims → skills) | ☑ (sub-steps gate) |
| binding-energy | single-point-energy | ☐ (via sp task shim → skill) | ☑ (sub-steps gate) |
| logp-partition | single-point-energy | ☐ (via sp task shim → skill) | ☑ (sub-steps gate) |
| solvation | single-point-energy | ☐ (via sp task shim → skill) | ☑ (sub-steps gate) |
| fukui-reactivity | electrostatics | ☐ (via electrostatics task shim → skill) | ☑ (sub-steps gate) |
| redox-potential | sp, opt, freq | ☐ (via task shims → skills) | ☑ (sub-steps gate) |
| reaction-energy | sp, opt, freq | ☐ (via task shims → skills) | ☑ (sub-steps gate) |
| pka-acidity | freq | ☐ (via freq task shim → skill) | ☑ (sub-steps gate) |
| transition-state | freq | ☐ (via freq task shim → skill) | ☑ (sub-steps gate) |
| reaction-profile | opt, freq, ts, irc | ☐ (via task shims → skills) | ☑ (sub-steps gate) |
| build-from-smiles | opt (optional QM refine) | ☐ (composes via the opt task shim → skill, not yet a direct sibling import) | ☑ (the --opt step runs through opt's skill run(), which gates) |

## Sign-off gate

Migration is **not** complete until: every applicable box above is ☑, both lints
pass, the full test suite is green, and every skill's example README still
reports numbers matching its committed result JSON.
