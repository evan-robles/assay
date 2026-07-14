#!/usr/bin/env bash
# stop_sweep.sh — cleanly STOP a running (self-cloning) fidelity sweep.
#
# Stopping is fiddly: the node-holder has a SIGTERM self-clone trap (qdel spawns
# a clone), the login autorelauncher relaunches on a running holder, and pkill
# over ssh often returns rc=255. A single naive attempt reliably leaves one job
# (the clone) alive. This script does the correct order, idempotently, and loops
# the qdel until zero assay jobs remain.
#
# Run on the Aurora login node:  bash tools/stop_sweep.sh
set +e   # never abort mid-cleanup; kills/qdels returning nonzero is expected
REPO="$HOME/chem-skills"; cd "$REPO"

echo "[stop] 1. setting .sweep_done (blocks holder self-clone + exits autorelauncher)"
touch "$REPO/.sweep_done"

echo "[stop] 2. killing autorelauncher"
pkill -9 -f aurora_autorelaunch 2>/dev/null

echo "[stop] 3. killing sweep launchers + drivers"
pkill -9 -f "tools/parallel_suite" 2>/dev/null
pkill -9 -f fidelity_driver 2>/dev/null

echo "[stop] 4. qdel every assay-nodeholder job (loop until none; catches clones)"
for attempt in 1 2 3 4 5; do
    # Get FULL job ids. `qstat -u $USER | awk '{print $1}'` TRUNCATES the id to
    # "<n>.aurora-pbs-*" (with a literal *), which qdel rejects as "illegally
    # formed job identifier" — that is why an earlier version looped 5× and left
    # the job alive. `qselect -N <name>` returns untruncated ids selected by job
    # name, which qdel accepts. Fall back to a full-id qstat parse if qselect is
    # unavailable.
    ids=$(qselect -N assay-nodeholder -u "$USER" 2>/dev/null)
    [ -z "$ids" ] && ids=$(qstat -f -u "$USER" 2>/dev/null \
        | awk '/^Job Id:/{jid=$3} /Job_Name = assay-nodeholder/{print jid}')
    [ -z "$ids" ] && { echo "  no assay jobs remaining."; break; }
    echo "  attempt $attempt: qdel $ids"
    for j in $ids; do
        qdel "$j" 2>/dev/null
        # a queued/exiting job that won't clear on plain qdel needs a forced delete
        [ "$attempt" -ge 2 ] && qdel -W force "$j" 2>/dev/null
    done
    # re-touch in case a dying holder deleted the flag before its clone read it
    touch "$REPO/.sweep_done"
    sleep 4
done

echo "[stop] 5. verify (non-self-matching patterns)"
procs=$(ps -eo args | grep -E "[a]urora_autorelaunch|[f]idelity_driver|[t]ools/parallel_suite" | wc -l | tr -d ' ')
jobs=$(qstat -u "$USER" 2>/dev/null | grep -c assay)
echo "  live procs: $procs   assay jobs: $jobs"
if [ "$procs" -eq 0 ] && [ "$jobs" -eq 0 ]; then
    echo "[stop] STOPPED cleanly."
else
    echo "[stop] WARNING: not fully stopped — re-run this script."
fi
