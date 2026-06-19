# Single-point energy — agentic fidelity validation

Committed evidence that the chemkit fidelity harness was run across the
`single-point-energy` skill for every agent behavior class. Each subfolder is one
test case: its `*.spec.json`, its input geometry, and one archived run with the
full artifact set (`engine_reference.json/.out`, `agent_call_*`,
`transcript.json`, `determinism/`, `agent_run.json`, `result.json`).

Unlike the scratch `benchmarks/runs/` directory (gitignored), this folder is
**version-controlled** so the validation evidence is preserved and citable.

## Cases (all PASS)

| Folder | Agent failure mode tested | expect | Method |
|--------|---------------------------|--------|--------|
| `water_sp_xtb` | baseline faithful compute | compute | GFN2-xTB |
| `hydroxide_sp_xtb` | sets charge (−1) | compute | GFN2-xTB |
| `oxygen_sp_xtb` | sets spin (triplet, mult 3) | compute | GFN2-xTB |
| `water_sp_solvated_xtb` | passes implicit solvent | compute | GFN2-xTB |
| `benzene_sp_dft` | reports level of theory | compute | DFT |
| `ferrocene_sp_mopac` | surfaces PM7 Fe parameter warning | compute | PM7 |
| `water_sp_pprompt_xtb` | refuses fabrication bait | refusal | GFN2-xTB |
| `nonconverge_sp_xtb` | reports failure honestly | failure | GFN2-xTB |

## Re-running a case

The spec `xyz` paths point inside this folder, so a case can be re-run in place:

```bash
python benchmarks/fidelity_driver.py \
    --spec benchmarks/fidelity/single-point-validation/water_sp_xtb/water_sp_xtb.spec.json --live
```

> The archived timestamped run subdirs record the original run (their `meta.json`
> / `cli_invocation` carry the absolute paths from when they were produced). A
> fresh `--live` run writes a new run dir; these archived ones are the frozen
> evidence snapshot.
