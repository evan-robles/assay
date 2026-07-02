#!/usr/bin/env bash
# argo_concurrency_probe.sh — measure how many CONCURRENT live agent runs the
# argo tunnel can sustain before it stalls. Answers the design question: does
# adding more compute nodes speed up a sweep, or is the single argo tunnel the
# ceiling? Run on the LOGIN node with a fresh job (argo tunnel up).
#
# It fires N concurrent one-shot fukui runs (same model, throwaway output) and
# reports how many finish vs stall over ~2 min. Interpretation:
#   * most finish quickly            -> argo has headroom; more nodes help.
#   * only ~K finish, the rest stall -> argo caps concurrency at ~K; more nodes
#                                       past K do NOT help (resume/fewer-reps is
#                                       the better lever).
#
# Usage:
#   tools/argo_concurrency_probe.sh <compute-node-hostname> [N] [model]
# Example:
#   tools/argo_concurrency_probe.sh x4509c4s0b0n0.hsn.cm.aurora.alcf.anl.gov 6 argo:o3
set -u

NODE="${1:?usage: argo_concurrency_probe.sh <compute-node-host> [N=6] [model=argo:o3]}"
N="${2:-6}"
MODEL="${3:-argo:o3}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER="$REPO/benchmarks/fidelity_driver.py"
SPEC="$REPO/benchmarks/fidelity/fukui-reactivity-validation/acrolein/acrolein_fukui_reactivity.spec.json"
# --out-dir MUST be on the SHARED filesystem: the engine ssh's to a compute node,
# and a login-node /tmp path does not exist there (cd fails). Use a dir under the
# repo ($HOME is shared onto compute nodes on Aurora). Logs stay in /tmp (read
# locally on the login node only).
OUT="$REPO/.argo_probe_$$"
LOGDIR="/tmp/argo_probe_$$"
mkdir -p "$OUT" "$LOGDIR"

echo "[probe] $N concurrent '$MODEL' fukui runs -> node $NODE"
echo "[probe] output: $OUT"

for i in $(seq 1 "$N"); do
  CHEMKIT_REMOTE_HOST="$NODE" \
    python "$DRIVER" --spec "$SPEC" --live --model "$MODEL" --out-dir "$OUT" \
    > "$LOGDIR/run_$i.log" 2>&1 &
done

# Sample progress every 30s up to 3 min. "finished" = log contains OVERALL: (the
# run reached scoring); "running" = launched but not yet finished.
for t in 30 60 90 120 150 180; do
  sleep 30
  fin=$(grep -l "OVERALL:\|ERROR:" "$LOGDIR"/run_*.log 2>/dev/null | wc -l | tr -d ' ')
  run=$(( N - fin ))
  echo "[probe] t=${t}s  finished=$fin  still-running=$run"
  [ "$fin" -ge "$N" ] && break
done

wait
fin=$(grep -l "OVERALL:\|ERROR:" "$LOGDIR"/run_*.log 2>/dev/null | wc -l | tr -d ' ')
echo "[probe] DONE: $fin/$N runs completed."
echo "[probe] if all $N finished fast -> argo has headroom (more nodes help)."
echo "[probe] if far fewer than $N finished -> argo is the ceiling (resume/fewer reps)."
echo "[probe] logs kept in $LOGDIR for inspection."
rm -rf "$OUT"   # throwaway shared output dir (results are not needed, only timing)
