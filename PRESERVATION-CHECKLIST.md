# PRESERVATION-CHECKLIST — no engine functionality dropped during the skill-inversion migration

Companion to [`DESIGN.md`](DESIGN.md) §10. Each skill's migration PR must tick
every applicable box. A box is **N/A** when the skill does not expose that knob
(e.g. `name-to-smiles` has no `--method`, so #2/#3-method are N/A). "Verified"
means an actual run reproduced the pre-migration result JSON (same numbers).

Legend: ☐ not done · ☑ done · N/A not applicable

## Global (once, not per-skill)

- ☐ `chemkit_engine` → `assay_core` rename; `assay_core` pip-installable
- ☐ `assay_core.argkit` holds: normalizers (#4), `choices=` builders (#3), LoT gate (#2), `_add_chem_options` / `_add_gate_option` / `_add_stdout_option`, `run_cli()` spine
- ☐ `assay_core.runlog` holds: live `.out` + `tail -f` at launch (#10), `CHEMKIT_REMOTE_HOST` ssh (#10), error envelopes (#10), per-tool log line (#10), fd-1→fd-2 redirect (#9)
- ☐ `assay_core.ledger.write_input_configs` (#12)
- ☐ `integrity.py` stays in `assay_core`; `run_cli` performs the catch/exit + `--allow-unconverged` (#8)
- ☐ Discovery registry publishes canonical names + aliases; feeds did-you-mean (#5), `--list-skills`/`--help-json` (#6)
- ☐ Server (`mcp_server/server.py`) builds typed tools via `run()` introspection (#1); no hand-maintained `TOOLS` dict
- ☐ `assay`/`chemkit` CLI front-door dispatches to `skills/<n>/scripts/run.py`; `--list-skills`, alias resolution, `--help`/`--help-json` passthrough (#6)
- ☐ PreToolUse hook `chemkit-method-gate.sh` retained; `METHOD_REQUIRED_SUBCMDS` regenerated from manifests (#11)
- ☐ `tools/lint_skills.py`: spine lint (#10.2-1) + registry-sync lint (#10.2-2) wired into CI
- ☐ Full `tests/` suite (test_regression.py, test_cli_interface.py) green
- ☐ `rules/skill-standards.md` + `README.md` updated (drop "thin client"; add `run()`/`build_parser()`/`run_cli` contract)

## Per-skill grid

For each skill, confirm the entrypoint spine, then the applicable guardrails.

| Skill | typed `run()` (#1) | uses `run_cli` spine (#2,7,8,9,12) | shared `choices`+normalizers (#3,#4) | `--stdout` modes (#7) | integrity gate verified (#8) | fd-redirect: JSON clean (#9) | live `.out` path (#10) | `input_configs.yaml` (#12) | `--out` default+on-fail (#13) | regression + example reproduce numbers |
|---|---|---|---|---|---|---|---|---|---|---|
| single-point-energy | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| geometry-optimize | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| vibrational-analysis | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| conformer-search | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| conformational-analysis | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| build-from-smiles | ☐ | ☐ | ☐ (`--opt` not `--method`) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| name-to-smiles | ☐ | ☐ | N/A (no QM knobs) | ☐ | N/A | ☐ | ☐ | ☐ | ☐ | ☐ |
| binding-energy | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| redox-potential | ☐ | ☐ | ☐ (+`--ref`,`--mode`) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| pka-acidity | ☐ | ☐ | ☐ (+`--mode`; `--accept-defaults`) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| logp-partition | ☐ | ☐ | ☐ (solvent pinned) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| solvation | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| frontier-orbitals | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| electrostatics | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| fukui-reactivity | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| transition-state | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| intrinsic-reaction-coordinate | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| reaction-energy | ☐ | ☐ | ☐ (+`--mode`) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| reaction-profile | ☐ | ☐ | ☐ (+`--accept-defaults`) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| visualize-orbitals | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |

## Composite-specific (skill→skill in-process, #5 of DESIGN)

Each composite must import sibling **skill** `run()`s (not the old engine tasks),
and its declared `depends_on:` must match its imports (DAG lint).

| Composite | imports (sibling skill run()) | `depends_on:` declared | sub-step still integrity-gated (#8) |
|---|---|---|---|
| vibrational-analysis | opt, conformer-search | ☐ | ☐ |
| conformational-analysis | conformer-search, opt | ☐ | ☐ |
| binding-energy | single-point-energy | ☐ | ☐ |
| logp-partition | single-point-energy | ☐ | ☐ |
| solvation | single-point-energy | ☐ | ☐ |
| fukui-reactivity | electrostatics | ☐ | ☐ |
| redox-potential | sp, opt, freq | ☐ | ☐ |
| reaction-energy | sp, opt, freq | ☐ | ☐ |
| pka-acidity | freq | ☐ | ☐ |
| transition-state | freq | ☐ | ☐ |
| reaction-profile | opt, freq, ts, irc | ☐ | ☐ |
| build-from-smiles | opt (optional QM refine) | ☐ | ☐ |

## Sign-off gate

Migration is **not** complete until: every applicable box above is ☑, both lints
pass, the full test suite is green, and every skill's example README still
reports numbers matching its committed result JSON.
