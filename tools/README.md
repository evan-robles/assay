# tools/

Repository utilities for ASSAY (formerly chemkit). These are developer/ops
helpers, **not** chemistry skills or MCP tools — run them directly with `python`.

| Tool | Purpose |
|---|---|
| `aurora_submit.py` | Submit / monitor / collect ASSAY jobs on the Aurora supercomputer (PBS Pro). |
| `build_skill_folders.py` | Scaffold new skill folders. |
| `lint_skills.py` | Lint skill folders against the standards. |

---

## aurora_submit.py — run ASSAY on Aurora (PBS Pro)

A standalone orchestration tool. A PBS job is **asynchronous** (submit now,
results in minutes–hours), so this deliberately splits into three separate
actions rather than blocking your shell. It generates a correct PBS script from
your saved defaults, `qsub`s it, and later collects the output.

It runs **on an Aurora login node** (it needs the `qsub` binary). The only part
testable elsewhere is the pure `build_pbs_script()` function.

### One-time setup

```bash
cp tools/aurora.example.yaml ~/.assay/aurora.yaml
$EDITOR ~/.assay/aurora.yaml     # set `project` (your allocation) and `repo_path`
```

Only `project` is required; everything else has a documented default (see the
template). CLI flags override the config per-run.

### Submit

Run the fidelity suite (engine-only — see the `--live` note):

```bash
python tools/aurora_submit.py submit \
    --suite benchmarks/fidelity/logp-partition-validation \
    --queue debug --walltime 01:00:00
```

Or submit any command:

```bash
python tools/aurora_submit.py submit \
    --cmd "python -m chemkit_engine.cli sp --method xtb mol.xyz"
```

`submit` writes three artifacts (into `repo_path`) **before** calling `qsub`, so
they're inspectable even if submission fails:
- `<jobname>_<stamp>.pbs` — the generated batch script (a reproducibility record)
- `input_configs_<stamp>.yaml` — the effective config incl. all defaults
- `submission_<stamp>.json` — job id + command + config (written on success)

### Monitor and collect

```bash
python tools/aurora_submit.py status  <jobid>          # qstat state (Q/R/F)
python tools/aurora_submit.py collect <jobid-or-rundir> # gather .o/.e + results
```

`collect` finds the PBS `.o<id>`/`.e<id>` output, detects the
`ASSAY_JOB_DONE rc=<n>` completion marker, and points at any suite
`summary.csv` produced.

### ⚠ Compute-node internet (important for `--live`)

Aurora compute nodes have **no direct outbound internet**. A `--live` benchmark
run needs the argo-proxy / model endpoint, so on a compute node agent scoring
will **silently skip** unless you route through the ALCF proxy:

```bash
python tools/aurora_submit.py submit --suite <folder> --live --proxy   # or set proxy: true
```

Engine-only (non-`--live`) runs are unaffected. The tool prints a loud warning if
you submit `--live` without the proxy enabled.

### Common overrides

```
--project   ALCF allocation (qsub -A)      --nodes        node count
--queue     debug | debug-scaling | prod    --filesystems  declared filesystems
--walltime  HH:MM:SS                         --env          conda env to activate
--proxy     inject ALCF proxy exports        --run-dir      dir to cd into on node
--name      PBS job name                      --config       path to aurora.yaml
```
