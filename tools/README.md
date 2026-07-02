# tools/

Repository utilities for ASSAY (formerly chemkit). These are developer/ops
helpers, **not** chemistry skills or MCP tools — run them directly with `python`.

| Tool | Purpose |
|---|---|
| `aurora_submit.py` | Submit / monitor / collect ASSAY jobs on the Aurora supercomputer (PBS Pro). |
| `build_skill_folders.py` | Scaffold new skill folders. |
| `lint_skills.py` | Lint skill folders against the standards. |

---

## Remote engine execution (`CHEMKIT_REMOTE_HOST`)

For the **interactive agentic** setup on a cluster where the agent + MCP server
must run on a LOGIN node (e.g. so a login-node argo tunnel is reachable) but the
chemistry must run on a COMPUTE node (login nodes lack compute, and on Aurora a
login-node filesystem quirk breaks the engine's nested `mkdir`):

Set `CHEMKIT_REMOTE_HOST` and the MCP server will run each engine call on that
host via `ssh` instead of locally.

```bash
# 1. hold a compute node (separate shell); note its hostname from $PBS_NODEFILE
qsub -I -l select=1 -l walltime=01:00:00 -l filesystems=flare -A <project> -q debug

# 2. in your agent/server shell on the LOGIN node:
export CHEMKIT_REMOTE_HOST=<compute-node-hostname>   # e.g. x4303c1s3b0n0
# optional extra ssh flags (batch mode, key, etc.):
export CHEMKIT_REMOTE_SSH_OPTS="-o BatchMode=yes"

# 3. run the agent / suite as usual — every engine call now executes on the
#    compute node; argo stays local to the login node where your tunnel lands.
```

**Assumes a shared `$HOME`/filesystem** (true on Aurora: `$HOME` is mounted on
compute nodes), so `cwd`, input paths, and `--out` resolve identically on both
sides — no file copy-back needed. The result JSON returns on ssh stdout; the
live `.out` log is written locally. The `.out` header records the full
`ssh … ` command so the run is reproducible.

Unset `CHEMKIT_REMOTE_HOST` to go back to running the engine locally.

### `CHEMKIT_REMOTE_ENV_SETUP` — activate the env on the remote side (required on Aurora)

A non-interactive `ssh host "…"` shell does **not** source `~/.bashrc` or
activate conda, so `xtb` / `mopac` / the right `python` are not on `PATH`.
`CHEMKIT_REMOTE_ENV_SETUP` is prepended to every remote command. On Aurora the
default (`source ~/.bashrc; conda activate <env>`) does **not** work — you must
load the frameworks module first:

```bash
export CHEMKIT_REMOTE_ENV_SETUP='module use /soft/modulefiles && module load frameworks && conda activate assay_env'
```

Verify it before running a suite (should print an `xtb` path, a `mopac` path, and `OK`):

```bash
ssh -o BatchMode=yes "$CHEMKIT_REMOTE_HOST" "$CHEMKIT_REMOTE_ENV_SETUP; which xtb; which mopac; echo OK"
```

### Thread caps — required on large-core nodes (OpenBLAS segfault)

Aurora nodes report `os.cpu_count() == 208`. Many conda-forge OpenBLAS builds are
compiled for a **maximum of 128 threads**; if PySCF pins its OpenMP pool to the
full core count, OpenBLAS aborts the process mid-SCF
(`precompiled NUM_THREADS exceeded` → segfault / `double free or corruption`).

The engine now **self-caps the auto (cpu_count) default at 64** (see
`calculators.py`; override with `CHEMKIT_PYSCF_MAX_AUTO_THREADS`), so a fresh run
will not crash even with no thread env vars set. To run at a specific higher
count (still under 128), set it explicitly in `CHEMKIT_REMOTE_ENV_SETUP`:

```bash
export CHEMKIT_REMOTE_ENV_SETUP='module use /soft/modulefiles && module load frameworks && conda activate assay_env && export CHEMKIT_PYSCF_THREADS=96 OPENBLAS_NUM_THREADS=96 OMP_NUM_THREADS=96 MKL_NUM_THREADS=96 OMP_NESTED=FALSE OMP_MAX_ACTIVE_LEVELS=1'
```

`CHEMKIT_PYSCF_THREADS` is the one the engine honours for the PySCF pool; the
`OMP_NESTED=FALSE` / `OMP_MAX_ACTIVE_LEVELS=1` pair prevents nested thread
explosion. Keep the count **≤ 128** (64 is a safe, well-scaling default for DFT).

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
