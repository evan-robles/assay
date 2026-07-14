#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# aurora_sweep_controller.sh — drive a resumable fidelity sweep on Aurora from
# the MAC, using the persistent ControlMaster SSH connection to the `aurora` host.
#
# Architecture (why this shape):
#   * The agent loop needs the login-node argo tunnel (127.0.0.1:60639), which a
#     PBS batch job on a compute node cannot reach. So the sweep is driven ON the
#     LOGIN node, not inside the job.
#   * debug-scaling caps walltime at 1h, so no single job finishes the sweep.
#     This controller loops: submit a dumb node-holder job -> wait for it to run
#     -> read its published node list -> run parallel_suite.sh on the login node
#     (resume skips completed work) -> when the job expires, resubmit. Repeat
#     until every (molecule,model) has REPEAT completed runs.
#
# Adapted from the split-allocation + self-resubmit pattern of the MD
# submit_multi.sh, but the resubmit loop lives on the durable Mac (the job cannot
# self-resubmit across the argo boundary).
#
# Usage (on the Mac):
#   tools/aurora_sweep_controller.sh <suite-rel-path> <repeat> <model...>
# Example:
#   tools/aurora_sweep_controller.sh benchmarks/fidelity/fukui-reactivity-validation 10 \
#       argo:o3 argo:claude-opus-4.8 argo:claude-sonnet-4.6 argo:claude-haiku-4.5
#
# Requires: a live ControlMaster to `aurora` (ssh -O check aurora -> Master running).
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

SUITE="${1:?usage: aurora_sweep_controller.sh <suite-rel-path> <repeat> <model...>}"
REPEAT="${2:?usage: aurora_sweep_controller.sh <suite-rel-path> <repeat> <model...>}"
shift 2
MODELS=("$@")
[ "${#MODELS[@]}" -ge 1 ] || { echo "error: supply at least one model"; exit 1; }

SSH="ssh -o BatchMode=yes aurora"
REMOTE_REPO="chem-skills"                       # ~/chem-skills on Aurora
PBS_SCRIPT="${REMOTE_REPO}/tools/aurora_nodeholder.pbs"
NODES_FILE="${REMOTE_REPO}/.sweep_nodes"
MAX_JOBS="${MAX_JOBS:-100}"                      # resubmit-chain cap
POLL_SEC="${POLL_SEC:-20}"                       # qstat poll interval

# Env the login-side launcher needs (argo + thread caps for the ssh'd engine).
# These mirror what worked interactively; adjust the model list, not these.
REMOTE_ENV='export CHEMKIT_LLM_BASE_URL=http://127.0.0.1:60639/v1;
            export CHEMKIT_LLM_API_KEY=erobles;
            export NO_PROXY=127.0.0.1,localhost; export no_proxy=127.0.0.1,localhost;
            export CHEMKIT_REMOTE_ENV_SETUP="module use /soft/modulefiles && module load frameworks && conda activate assay_env && export CHEMKIT_PYSCF_THREADS=64 OPENBLAS_NUM_THREADS=64 OMP_NUM_THREADS=64 MKL_NUM_THREADS=64 OMP_NESTED=FALSE OMP_MAX_ACTIVE_LEVELS=1";
            module use /soft/modulefiles >/dev/null 2>&1; module load frameworks >/dev/null 2>&1;
            conda activate assay_env >/dev/null 2>&1'

# Diagnostics go to STDERR so they never pollute a $(...) command substitution.
# submit_and_wait_running() returns the job id via `echo "$jid"` on STDOUT; if
# log() also wrote to stdout, the caller `jid=$(submit_and_wait_running)` would
# capture the log lines INTO jid — producing a malformed job identifier that
# breaks qdel/qstat (observed 2026-07-10: "qdel: illegally formed job
# identifier: submitted …"). Keeping logs on stderr keeps the return value clean
# while still showing every message in a combined (2>&1) log.
log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }

# ── Preflight: ControlMaster + argo reachable ─────────────────────────────────
ssh -O check aurora >/dev/null 2>&1 || { echo "error: no ControlMaster to aurora (run: ssh aurora)"; exit 1; }
$SSH 'curl -s --max-time 8 --noproxy "*" -o /dev/null -w "%{http_code}" http://127.0.0.1:60639/v1/models' \
    | grep -q 200 || { echo "error: argo not reachable on the login node (tunnel down?)"; exit 1; }
log "ControlMaster + argo OK."

# ── Completion check: are all (molecule,model) at REPEAT? ─────────────────────
sweep_complete() {
    # Returns 0 (complete) if every model has REPEAT completed runs for every
    # molecule. Counts result.json files per model across the suite.
    local need done_ct nmol
    nmol=$($SSH "ls -d ${REMOTE_REPO}/${SUITE}/*/ 2>/dev/null | wc -l" | tr -d ' ')
    need=$(( nmol * REPEAT ))
    for m in "${MODELS[@]}"; do
        local mslug="${m//[:\/]/_}"
        done_ct=$($SSH "find ${REMOTE_REPO}/${SUITE} -path '*/${mslug}/*/result.json' 2>/dev/null | wc -l" | tr -d ' ')
        log "  ${m}: ${done_ct}/${need} runs"
        [ "$done_ct" -lt "$need" ] && return 1
    done
    return 0
}

# ── Submit a node-holder job, wait until it's Running, return its job id ───────
submit_and_wait_running() {
    local jid state waited=0
    jid=$($SSH "cd ${REMOTE_REPO} && qsub tools/aurora_nodeholder.pbs" 2>&1 | tr -d ' ')
    case "$jid" in
        *[!0-9A-Za-z._-]*|'') log "qsub failed: $jid"; return 1 ;;
    esac
    log "submitted node-holder job $jid; waiting for it to start…"
    while :; do
        state=$($SSH "qstat -f $jid 2>/dev/null | awk '/job_state/{print \$3}'" | tr -d ' ')
        [ "$state" = "R" ] && { log "job $jid running."; echo "$jid"; return 0; }
        [ -z "$state" ] && { log "job $jid vanished before running."; return 1; }
        sleep "$POLL_SEC"; waited=$((waited+POLL_SEC))
        [ $((waited % 120)) -eq 0 ] && log "  still queued (${waited}s, state=$state)…"
    done
}

# ── Run the sweep on the login node against the current allocation ─────────────
run_sweep_once() {
    local jid="$1"
    # The node-holder publishes .sweep_nodes a moment AFTER its job enters state R
    # (the job script has to start, resolve $PBS_NODEFILE, and write the file). So
    # a single `test -s` right after "running" races and often finds it empty,
    # which used to skip the whole cycle ("no $PBS_NODEFILE — cannot place engine
    # work"). WAIT for .sweep_nodes to be populated (bounded) before launching.
    local waited=0
    until $SSH "test -s ${NODES_FILE}"; do
        waited=$((waited+5))
        [ "$waited" -ge 120 ] && { log "timed out waiting for ${NODES_FILE} (120s); skipping this cycle"; return 1; }
        sleep 5
    done
    log "running parallel_suite.sh on the login node (job $jid)…"
    # PBS_NODEFILE must be an ABSOLUTE path: this ssh command `cd`s into
    # ${REMOTE_REPO} first, so a relative NODES_FILE ("chem-skills/.sweep_nodes")
    # would resolve to ~/chem-skills/chem-skills/.sweep_nodes (doubled, missing)
    # and parallel_suite would abort with "no $PBS_NODEFILE — cannot place engine
    # work" (observed 2026-07-10). ${REMOTE_REPO} is relative to $HOME, so the
    # node file is $HOME/${NODES_FILE}. Then parallel_suite reads THIS job's nodes.
    # We block here and let it run until the job dies (ssh returns when the remote
    # command exits, which happens when the nodes vanish and the sweep finishes
    # its current items or errors out).
    #
    # NOTE: warmup is NOT skipped here. STEP 1 of parallel_suite rebuilds each
    # molecule's engine-reference/ before the agent runs; this sweep starts with
    # the pka/redox references intentionally DELETED, so they must be regenerated
    # (a skipped warmup would leave every run with no reference to score against).
    # A cached-and-valid reference makes warmup near-instant, so leaving it on is
    # cheap once the first pass has rebuilt them.
    $SSH "cd ${REMOTE_REPO} && ${REMOTE_ENV};
          export PBS_NODEFILE="\$HOME/${NODES_FILE}";
          tools/parallel_suite.sh ${SUITE} ${REPEAT} ${MODELS[*]}" \
        2>&1 | sed 's/^/    [login] /'
}

# ── Main loop ─────────────────────────────────────────────────────────────────
log "=== Aurora sweep controller ==="
log "suite=${SUITE} repeat=${REPEAT} models=${MODELS[*]}"
job_n=0
while [ "$job_n" -lt "$MAX_JOBS" ]; do
    if sweep_complete; then
        log "SWEEP COMPLETE — all models at ${REPEAT} reps for every molecule."
        log "collecting final summary…"
        $SSH "cd ${REMOTE_REPO} && ${REMOTE_ENV};
              python - ${SUITE} ${REPEAT} <<'PY'
import sys; from pathlib import Path
sys.path.insert(0, 'benchmarks')
from collect_results import collect_repeats, _print_repeat_table, write_grouped_csv
suite=Path(sys.argv[1]); n=int(sys.argv[2])
rows=collect_repeats(suite, n=n); _print_repeat_table(rows)
write_grouped_csv(rows, suite/'summary.csv', base=suite, n=n); print('wrote', suite/'summary.csv')
PY" 2>&1 | sed 's/^/    [collect] /'
        exit 0
    fi
    job_n=$((job_n+1))
    log "--- submission ${job_n}/${MAX_JOBS} ---"
    # PRE-SUBMISSION CLEANUP: before starting a NEW generation, kill any login-node
    # parallel_suite/drivers still running from a PRIOR generation and qdel any stray
    # assay jobs. When a node-holder expires by walltime, the parallel_suite ssh'd
    # from run_sweep_once does not always die with it — a lingering worker keeps
    # firing driver runs at the now-DEAD nodes (rc=255 remote_host_unreachable),
    # and if the controller submits a fresh holder those generations STACK and churn
    # dead-node ERROR slots (observed 2026-07-10/11: 3–5 concurrent parallel_suite
    # procs). Killing the old generation first guarantees exactly one live generation.
    $SSH "pkill -f 'tools/parallel_suite.sh ${SUITE}' 2>/dev/null; pkill -f fidelity_driver 2>/dev/null;
          for j in \$(qselect -N assay-nodeholder -u \$USER 2>/dev/null); do qdel -W force \$j 2>/dev/null; done" \
        >/dev/null 2>&1 || true
    jid=$(submit_and_wait_running) || { log "submit/start failed; retrying in 60s"; sleep 60; continue; }
    run_sweep_once "$jid"
    # job likely expired (walltime) or sweep chunk finished; clean up the holder AND
    # any parallel_suite/drivers it spawned so nothing lingers into the next cycle.
    $SSH "qdel -W force $jid 2>/dev/null;
          pkill -f 'tools/parallel_suite.sh ${SUITE}' 2>/dev/null; pkill -f fidelity_driver 2>/dev/null" \
        >/dev/null 2>&1 || true
    log "job $jid ended; re-checking completion…"
done
log "STOP: hit MAX_JOBS=${MAX_JOBS} without completing. Re-run to continue (resume-safe)."
exit 2
