#!/usr/bin/env bash
# aurora_sweep_submit.sh — submit the single-job fidelity sweep, pinning the
# argo login node into the PBS script itself so self-resubmits inherit it.
#
# The argo tunnel (Logos->Mac->Aurora) rides ONE `ssh aurora` session pinned to
# ONE login node for the whole run (the laptop must stay up feeding argo anyway).
# So the login node is a stable, known value: detect it once here, write it into
# tools/aurora_sweep.pbs's LOGIN_NODE default, then qsub. Every job — including
# the job's own self-resubmits — then targets the correct login node with no
# -v propagation needed.
#
# Run on the Aurora LOGIN node (or from the Mac via: ssh aurora 'cd chem-skills && tools/aurora_sweep_submit.sh').
set -euo pipefail

REPO="${PBS_O_WORKDIR:-$HOME/chem-skills}"
PBS="${REPO}/tools/aurora_sweep.pbs"
[ -f "$PBS" ] || { echo "error: $PBS not found"; exit 1; }

# 1. Detect the login node that currently holds argo (this shell's login node).
LOGIN_NODE="$(hostname)"

# 2. Confirm argo is actually reachable here before submitting.
code=$(curl -s --max-time 8 --noproxy '*' -o /dev/null -w '%{http_code}' \
       http://127.0.0.1:60639/v1/models 2>/dev/null || true)
[ "$code" = "200" ] || { echo "error: argo not reachable on $LOGIN_NODE (code=$code) — tunnel down?"; exit 1; }
echo "argo OK on $LOGIN_NODE"

# 3. Pin the login node into the PBS script's LOGIN_NODE default line, so the
#    job AND its self-resubmits all target this node.
#    Line format: LOGIN_NODE="${ASSAY_LOGIN_NODE:-<node>}"
sed -i.bak -E "s#^(LOGIN_NODE=\"\\\$\{ASSAY_LOGIN_NODE:-)[^}]*(\}\".*)#\1${LOGIN_NODE}\2#" "$PBS"
rm -f "${PBS}.bak"
echo "pinned LOGIN_NODE=${LOGIN_NODE} into $(basename "$PBS"):"
grep -n '^LOGIN_NODE=' "$PBS"

# 4. Submit.
cd "$REPO"
jid=$(qsub tools/aurora_sweep.pbs)
echo "submitted: $jid"
echo "monitor:  qstat -u \$USER   |   tail -f ${REPO}/assay_sweep.log"
