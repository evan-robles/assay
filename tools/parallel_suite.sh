#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# parallel_suite.sh — run a fidelity suite across MULTIPLE Aurora compute nodes.
#
# Architecture:
#   * The agent + argo tunnel stay on the LOGIN node (this script runs there).
#   * Engine (QM) calls are distributed across the compute nodes listed in
#     $PBS_NODEFILE by giving each worker its own CHEMKIT_REMOTE_HOST.
#   * Molecules are assigned round-robin to nodes; each (molecule, model) pair
#     is a background fidelity_driver.py invocation targeting that node.
#
# Because DFT engine time dominates, distributing molecules across N nodes gives
# ~N-way speedup (the shared argo tunnel is not the bottleneck for DFT work).
#
# Safety:
#   * Engine references are WARMED serially first (one worker per molecule) so no
#     two parallel workers race to compute the same engine-reference/.
#   * Each (molecule, model) writes into its own <case>/<model>/ folder — disjoint.
#   * Per-node concurrency is throttled (PER_NODE_JOBS) to avoid core/BW
#     oversubscription on a single node.
#
# Usage:
#   # from the repo root, inside a multi-node interactive job (qsub -I -l select=N):
#   tools/parallel_suite.sh <suite-dir> <repeat> <model...>
#
# Example:
#   tools/parallel_suite.sh benchmarks/fidelity/fukui-reactivity-validation 10 \
#       argo:o3 argo:claude-opus-4.8 argo:claude-sonnet-4.7 argo:claude-haiku-4.5
#
# Requires (in the environment, typically from ~/.bashrc):
#   CHEMKIT_LLM_BASE_URL, CHEMKIT_LLM_API_KEY, NO_PROXY, no_proxy,
#   CHEMKIT_REMOTE_ENV_SETUP  (module load + conda activate + thread caps)
# ---------------------------------------------------------------------------
set -euo pipefail

SUITE="${1:?usage: parallel_suite.sh <suite-dir> <repeat> <model...>}"
REPEAT="${2:?usage: parallel_suite.sh <suite-dir> <repeat> <model...>}"
shift 2
MODELS=("$@")
[ "${#MODELS[@]}" -ge 1 ] || { echo "error: supply at least one model"; exit 1; }

# Per-node concurrency. At CHEMKIT_PYSCF_THREADS=64, keep PER_NODE_JOBS small so
# jobs*threads stays under the node core count (208 on Aurora): 3*64=192 is safe.
PER_NODE_JOBS="${PER_NODE_JOBS:-3}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER="$REPO/benchmarks/fidelity_driver.py"
cd "$REPO"

# --- Ensure the LAUNCHER shell has python (the agent side runs here) -----------
# The driver + the STEP 3 collector are invoked as bare `python`. If this shell
# has no conda env active (e.g. launched via nohup/ssh where ~/.bashrc does not
# auto-activate), every run dies "python: command not found" and the sweep
# "completes" having run nothing. Activate the env here so the launcher is
# self-sufficient. Skips silently if python is already available.
if ! command -v python >/dev/null 2>&1; then
    module use /soft/modulefiles >/dev/null 2>&1 || true
    module load frameworks       >/dev/null 2>&1 || true
    conda activate "${ASSAY_ENV:-assay_env}" >/dev/null 2>&1 || true
fi
command -v python >/dev/null 2>&1 || {
    echo "FATAL: no python on PATH after env activation — cannot run the driver."
    echo "  Activate assay_env (module load frameworks && conda activate assay_env) first."
    exit 1
}

# --- Resolve the compute-node list --------------------------------------------
# In a PBS job, $PBS_NODEFILE lists one line per node (with repeats per rank).
if [ -n "${PBS_NODEFILE:-}" ] && [ -f "$PBS_NODEFILE" ]; then
  mapfile -t NODES < <(sort -u "$PBS_NODEFILE")
elif [ -n "${CHEMKIT_REMOTE_HOST:-}" ]; then
  NODES=("$CHEMKIT_REMOTE_HOST")   # single node fallback
else
  echo "error: no \$PBS_NODEFILE and no CHEMKIT_REMOTE_HOST — cannot place engine work"
  exit 1
fi
NNODES="${#NODES[@]}"
echo "[parallel] $NNODES compute node(s): ${NODES[*]}"
echo "[parallel] suite=$SUITE repeat=$REPEAT models=${MODELS[*]} per_node_jobs=$PER_NODE_JOBS"

# --- Molecule work-list --------------------------------------------------------
mapfile -t SPECS < <(ls "$SUITE"/*/*.spec.json 2>/dev/null | sort)
[ "${#SPECS[@]}" -ge 1 ] || { echo "error: no *.spec.json under $SUITE"; exit 1; }
echo "[parallel] ${#SPECS[@]} molecule spec(s)"

# --- STEP 1: warm engine-references serially (no race) -------------------------
# One driver per molecule on its assigned node — WITHOUT --live, so it only
# builds/validates the engine-reference/ and exits (NO agent/LLM call). This
# populates the cache so the parallel fan-out only reads it (no recompute race).
# If a reference is already cached+valid, this is near-instant (a cache-load);
# only an uncached molecule pays the real engine cost. Skip entirely with
# SKIP_WARMUP=1 when you know every reference is already cached.
if [ "${SKIP_WARMUP:-0}" = "1" ]; then
  echo "[parallel] STEP 1: SKIPPED (SKIP_WARMUP=1) — assuming references cached."
else
  echo "[parallel] STEP 1: warming engine references (serial, no agent call)…"
  for idx in "${!SPECS[@]}"; do
    spec="${SPECS[$idx]}"
    node="${NODES[$(( idx % NNODES ))]}"
    moldir="$(dirname "$spec")"   # write into the molecule's OWN folder (and its
                                  # engine-reference/ cache lives there too)
    echo "  warm $(basename "$moldir") on $node"
    CHEMKIT_REMOTE_HOST="$node" \
      python "$DRIVER" --spec "$spec" --out-dir "$moldir" \
      > "/tmp/warm_$(basename "$moldir").log" 2>&1 || \
      echo "    (warm run exited nonzero — check /tmp/warm_*.log; continuing)"
  done
  echo "[parallel] STEP 1 done."
fi

# --- STEP 2: one SERIAL worker per model, models running in PARALLEL ----------
# The single argo tunnel cannot serve many concurrent agent calls — firing a wide
# pool of workers oversubscribes it and every run stalls at the agent call (only
# meta.json is written, nothing computes). So the concurrency unit is the MODEL,
# not the run: each model gets ONE worker that processes its (molecule × repeat)
# list strictly SERIALLY (one agent call at a time), and the workers for different
# models run in parallel. This bounds concurrent argo calls to #models — enough to
# use the cluster, not enough to overwhelm the proxy. Each model's engine work is
# pinned to its own compute node (round-robin), so nodes are still used in parallel.
echo "[parallel] STEP 2: ${#MODELS[@]} model worker(s), each serial over"
echo "           ${#SPECS[@]} molecule(s) × $REPEAT repeat(s); models run in parallel."

# Count COMPLETED runs for a (molecule-dir, model): timestamped run folders under
# <moldir>/<model_slug>/ that contain a result.json. Used for RESUME so a re-run
# after a killed/expired job only fills the shortfall toward REPEAT instead of
# redoing (or piling on top of) work already done. An ERRORED result.json still
# counts as "attempted" here — it occupies a rep slot; use FORCE=1 to ignore
# existing runs and always run the full REPEAT afresh.
_completed_runs() {  # $1 = moldir, $2 = model_slug  -> integer count
  local moldir="$1" mslug="$2" n=0 d
  for d in "$moldir/$mslug"/*/; do
    [ -f "${d}result.json" ] && n=$((n+1))
  done
  echo "$n"
}

run_model_serial() {  # $1 = model, $2 = node — process all molecules×repeats serially
  local model="$1" node="$2" msafe spec moldir mol rep have need r
  msafe="${model//[:\/]/_}"
  # DEPTH-FIRST: complete all REPEAT reps of one molecule before moving to the
  # next (molecule outer, rep inner). This yields complete per-molecule data
  # early — a molecule's full pass-rate is ready as soon as its reps finish —
  # rather than one rep of everything (breadth-first). Deliberately SERIAL per
  # model (no '&'): one agent call at a time to avoid oversubscribing argo.
  for idx in "${!SPECS[@]}"; do
    spec="${SPECS[$idx]}"
    moldir="$(dirname "$spec")"
    mol="$(basename "$moldir")"
    # RESUME: run only the missing reps (REPEAT minus already-completed), unless
    # FORCE=1. This makes the sweep survive across multiple <=walltime jobs — just
    # re-submit the same command and it continues where it left off.
    if [ "${FORCE:-0}" = "1" ]; then
      have=0
    else
      have="$(_completed_runs "$moldir" "$msafe")"
    fi
    need=$(( REPEAT - have ))
    if [ "$need" -le 0 ]; then
      echo "[parallel]   $mol [$model]: already have $have/$REPEAT — skipping"
      continue
    fi
    [ "$have" -gt 0 ] && echo "[parallel]   $mol [$model]: resuming, $have/$REPEAT done, running $need more"
    for r in $(seq 1 "$need"); do
      rep=$(( have + r ))   # label continues from what exists (for the /tmp log name)
      # --out-dir places the run under <molecule>/<model>/<timestamp>/ (matching
      # run_suite.py) and points the engine-reference cache at the molecule folder.
      CHEMKIT_REMOTE_HOST="$node" \
        python "$DRIVER" --spec "$spec" --live --model "$model" --out-dir "$moldir" \
        > "/tmp/run_${mol}_${msafe}_${rep}.log" 2>&1
    done
  done
  echo "[parallel]   model $model done."
}

# Launch one serial worker per model, each pinned to its own node (round-robin).
for mi in "${!MODELS[@]}"; do
  model="${MODELS[$mi]}"
  node="${NODES[$(( mi % NNODES ))]}"
  echo "[parallel]   model $model -> node $node (serial)"
  run_model_serial "$model" "$node" &
done
wait
echo "[parallel] STEP 2 done — all runs complete."

# --- STEP 3: collect once ------------------------------------------------------
# Aggregate the N newest runs per (case,model) into a pass-rate table. This is
# the same collect_repeats() path run_suite.py uses for --repeat; call it here
# directly so the parallel fan-out gets one final roll-up with no re-running.
echo "[parallel] STEP 3: collecting…"
python - "$SUITE" "$REPEAT" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("benchmarks").resolve()))
from collect_results import collect_repeats, _print_repeat_table, write_grouped_csv
suite = Path(sys.argv[1]); n = int(sys.argv[2])
rows = collect_repeats(suite, n=n)
_print_repeat_table(rows)
write_grouped_csv(rows, suite / "summary.csv")
print(f"\n[collect] wrote {suite/'summary.csv'}")
PY
echo "[parallel] all done."
