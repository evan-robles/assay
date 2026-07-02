#!/usr/bin/env bash
# check_nodes.sh — count running chemkit engine processes on every compute node
# of the current PBS job. Use it to confirm parallel_suite.sh is actually
# spreading work across all nodes (not piling onto one).
#
# Works from EITHER the login node or inside a compute-node shell:
#   * node list comes from $PBS_NODEFILE (set in a job shell) or a captured
#     ~/nodes.txt fallback;
#   * the node you are ON is counted locally (no ssh to self);
#   * sibling nodes are queried over ssh with a short timeout.
#
# Usage:
#   tools/check_nodes.sh            # count chemkit_engine procs per node
#   tools/check_nodes.sh <pattern>  # count procs matching a custom pgrep pattern
set -u

PATTERN="${1:-chemkit_engine}"

# --- Resolve the node list ----------------------------------------------------
if [ -n "${PBS_NODEFILE:-}" ] && [ -f "$PBS_NODEFILE" ]; then
  mapfile -t NODES < <(sort -u "$PBS_NODEFILE")
elif [ -f "$HOME/nodes.txt" ]; then
  mapfile -t NODES < <(sort -u "$HOME/nodes.txt")
else
  echo "no \$PBS_NODEFILE and no ~/nodes.txt — run inside the job, or capture the"
  echo "node list first:  sort -u \$PBS_NODEFILE > ~/nodes.txt"
  exit 1
fi

me="${HOSTNAME:-$(hostname)}"
total=0
for n in "${NODES[@]}"; do
  short="${n%%.*}"
  if [ "$short" = "${me%%.*}" ]; then
    c=$(pgrep -f "$PATTERN" | wc -l | tr -d ' ')
    echo "$short: $c (local)"
  else
    c=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$n" \
          "pgrep -f '$PATTERN' | wc -l" 2>/dev/null | tr -d ' ')
    echo "$short: ${c:-unreachable}"
  fi
  case "$c" in ''|*[!0-9]*) ;; *) total=$((total + c)) ;; esac
done
echo "----"
echo "total running: $total  (pattern: $PATTERN)"
