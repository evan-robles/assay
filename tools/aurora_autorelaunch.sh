#!/usr/bin/env bash
# aurora_autorelaunch.sh — LOGIN-SIDE companion to the self-cloning node-holder.
#
# Runs ON the login node (where argo is reachable), in the background (nohup).
# The node-holder self-clones across walltime boundaries (SIGTERM trap) and bumps
# .sweep_gen each time a fresh allocation comes up. This watcher notices a new
# generation, (re)launches the sweep against the new node list, and loops — so a
# multi-hour sweep runs unattended across many 1h jobs with NO manual steps.
#
# It stops when .sweep_done exists (the sweep completed) — which it writes itself
# once parallel_suite reports every (model,molecule) at REPEAT.
#
# Usage (on the login node, from ~/chem-skills):
#   nohup tools/aurora_autorelaunch.sh <suite> <repeat> <model...> > autorelaunch.log 2>&1 &
#
# Requires argo reachable on this login node (the tunnel session) and the fixed
# driver/engine synced. Injects the full env per the #1 rule.
# NOTE: NO `set -u` — `module load frameworks` (Lmod) references unset vars
# internally and would abort the script under -u (this silently killed an earlier
# run right after the startup log line). pipefail only.
set -o pipefail

SUITE="${1:?usage: aurora_autorelaunch.sh <suite> <repeat> <model...>}"
REPEAT="${2:?}"; shift 2
MODELS=("$@")
REPO="$HOME/chem-skills"; cd "$REPO"
GEN_FILE="$REPO/.sweep_gen"
NODES_FILE="$REPO/.sweep_nodes"
DONE_FILE="$REPO/.sweep_done"
POLL="${ASSAY_AUTORELAUNCH_POLL:-30}"

log(){ echo "[$(date '+%H:%M:%S')] autorelaunch: $*"; }

# The env every launcher shell + per-node engine ssh needs (the #1 rule).
activate() {
    module use /soft/modulefiles >/dev/null 2>&1
    module load frameworks       >/dev/null 2>&1
    conda activate assay_env     >/dev/null 2>&1
    export CHEMKIT_LLM_BASE_URL=http://127.0.0.1:60639/v1 CHEMKIT_LLM_API_KEY=erobles
    export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
    export CHEMKIT_REMOTE_ENV_SETUP="module use /soft/modulefiles && module load frameworks && conda activate assay_env && export CHEMKIT_PYSCF_THREADS=64 OPENBLAS_NUM_THREADS=64 OMP_NUM_THREADS=64 MKL_NUM_THREADS=64 OMP_NESTED=FALSE OMP_MAX_ACTIVE_LEVELS=1"
    export SKIP_WARMUP=1
}

# Sweep complete? every (molecule,model) has REPEAT result.json.
sweep_complete() {
    activate
    python - "$SUITE" "$REPEAT" "${MODELS[@]}" <<'PY'
import sys, glob, json
from pathlib import Path
suite, n = Path(sys.argv[1]), int(sys.argv[2]); models = sys.argv[3:]
mols = [d for d in suite.iterdir() if d.is_dir()]
for mol in mols:
    for m in models:
        mslug = m.replace(":","_").replace("/","_")
        done = len(glob.glob(str(mol/mslug/"*"/"result.json")))
        if done < n:
            sys.exit(1)
sys.exit(0)
PY
}

# A node-holder job is RUNNING (its nodes in .sweep_nodes are actually alive)?
# CRITICAL gate: never relaunch against a DEAD allocation. Between an old
# holder's walltime death and its clone starting (queued), .sweep_nodes still
# lists the now-dead nodes; launching then just produces rc=255 dead-node
# failures. Only launch when an assay-nodeholder job is in state R.
holder_running() {
    ssh_state=$(qstat -u "$USER" 2>/dev/null | awk '/assay-nodeholde/ && $10=="R"{print "R"}' | head -1)
    [ "$ssh_state" = "R" ]
}
# The gen the CURRENTLY-RUNNING holder published (so we relaunch once per fresh
# allocation). We also relaunch if the sweep process died while the holder is
# still up (mid-window crash).

log "watching gen counter; suite=$SUITE repeat=$REPEAT models=${MODELS[*]}"
last_gen=""
while true; do
    if [ -f "$DONE_FILE" ]; then log "done marker present — exiting."; break; fi
    if sweep_complete; then
        log "SWEEP COMPLETE — writing .sweep_done + collecting."
        touch "$DONE_FILE"
        activate
        python - "$SUITE" "$REPEAT" <<'PY'
import sys; from pathlib import Path
sys.path.insert(0,"benchmarks")
from collect_results import collect_repeats,_print_repeat_table,write_grouped_csv
s=Path(sys.argv[1]); n=int(sys.argv[2]); rows=collect_repeats(s,n=n)
_print_repeat_table(rows); write_grouped_csv(rows, s/"summary.csv", base=s, n=n); print("wrote", s/"summary.csv")
PY
        break
    fi
    gen=$(cat "$GEN_FILE" 2>/dev/null || echo "")
    running=$(pgrep -f "tools/parallel_suite.sh $SUITE" | wc -l | tr -d ' ')
    # Only (re)launch when: gen exists, a holder is RUNNING (live nodes), the
    # sweep isn't already running, and EITHER it's a new generation OR the sweep
    # died mid-window. The holder_running gate is what prevents hammering dead
    # nodes during the queue gap between a holder's death and its clone starting.
    if [ -n "$gen" ] && [ -s "$NODES_FILE" ] && [ "$running" -eq 0 ] \
       && { [ "$gen" != "$last_gen" ] || holder_running; } && holder_running; then
        log "gen=$gen (was ${last_gen:-none}), holder=R, sweep=0 -> launching against $(wc -l <"$NODES_FILE") nodes"
        activate
        PBS_NODEFILE="$NODES_FILE" nohup bash tools/parallel_suite.sh "$SUITE" "$REPEAT" "${MODELS[@]}" \
            > "sweep_gen${gen}.log" 2>&1 &
        last_gen="$gen"
    elif [ -n "$gen" ] && [ "$running" -eq 0 ] && ! holder_running; then
        log "sweep idle but NO running holder (allocation gap; clone queued) — waiting."
    fi
    sleep "$POLL"
done
log "stopped."
