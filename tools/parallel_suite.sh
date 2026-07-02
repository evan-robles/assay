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

# --- STEP 2: fan out (molecule × model × repeat) across nodes -----------------
echo "[parallel] STEP 2: parallel fan-out…"
# Build the FLAT work-list of every (spec, model, rep) unit first, so we can
# distribute individual UNITS across nodes — not whole molecules. (A
# molecule-outer loop would pile a molecule's entire batch onto one node before
# touching the next, leaving the other nodes idle. Interleaving units keeps all
# nodes busy from the start.) Each unit is assigned to a node round-robin; a
# per-node PID map enforces a strict PER_NODE_JOBS cap on each node independently.
# Interleave so ALL models make progress from the start. rep is the OUTER loop
# and model the MIDDLE: the first units queued are (rep 1: mol0×modelA, mol0×modelB,
# …, mol1×modelA, …), so every model appears near the front instead of one model's
# entire batch draining before the next begins. Without this (molecule→model→rep),
# the concurrency window fills with only the first model and the others look like
# they "never run" until much later.
WORK=()
for rep in $(seq 1 "$REPEAT"); do
  for model in "${MODELS[@]}"; do
    for idx in "${!SPECS[@]}"; do
      WORK+=("$idx|$model|$rep")
    done
  done
done
echo "[parallel] ${#WORK[@]} run units across $NNODES node(s), ${PER_NODE_JOBS}/node"

# Track running PIDs per node by node INDEX using plain indexed arrays
# (portable to bash 3.2+; avoids `declare -A`, which needs bash 4). NODE_PIDS[i]
# holds a space-separated PID list for NODES[i].
NODE_PIDS=()
for i in "${!NODES[@]}"; do NODE_PIDS[$i]=""; done

_running_on() {  # count live PIDs on node index $1; prune dead ones in place
  local ni="$1" pid cnt=0 alive=""
  for pid in ${NODE_PIDS[$ni]}; do
    if kill -0 "$pid" 2>/dev/null; then alive+="$pid "; cnt=$((cnt+1)); fi
  done
  NODE_PIDS[$ni]="$alive"
  echo "$cnt"
}

wi=0
for unit in "${WORK[@]}"; do
  IFS='|' read -r idx model rep <<< "$unit"
  # round-robin the UNIT across nodes (by index)
  ni=$(( wi % NNODES )); node="${NODES[$ni]}"; wi=$((wi+1))
  # wait until THIS node has a free slot (strict per-node cap)
  while [ "$(_running_on "$ni")" -ge "$PER_NODE_JOBS" ]; do sleep 1; done
  spec="${SPECS[$idx]}"
  moldir="$(dirname "$spec")"          # the molecule's own folder
  mol="$(basename "$moldir")"
  msafe="${model//[:\/]/_}"
  # --out-dir places the run under <molecule>/<model>/<timestamp>/ (matching
  # run_suite.py) and points the engine-reference cache at the molecule folder —
  # without it the driver defaults to benchmarks/runs/ and also misses the cache.
  CHEMKIT_REMOTE_HOST="$node" \
    python "$DRIVER" --spec "$spec" --live --model "$model" --out-dir "$moldir" \
    > "/tmp/run_${mol}_${msafe}_${rep}.log" 2>&1 &
  NODE_PIDS[$ni]="${NODE_PIDS[$ni]}$! "
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
