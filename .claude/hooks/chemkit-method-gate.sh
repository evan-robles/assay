#!/usr/bin/env bash
#
# chemkit method-gate — PreToolUse(Bash) hook
# ===========================================
# Purpose: stop ANY agent — regardless of which model or vendor drives it
# (Claude, GPT, Gemini, Llama, Mistral, …) — from SILENTLY choosing the level of
# theory for a chemkit calculation. Per calculation-reporting-standards
# non-negotiable #10, the method / functional / basis / tier / solvent / charge /
# multiplicity must be confirmed WITH THE USER when unspecified — never guessed,
# and never carried over from a previous run. The enforcement is deterministic
# shell that fires on the Bash tool itself, so it does not depend on the model
# reading or obeying any prose; weaker models simply benefit most.
#
# Mechanism (deterministic, model- and harness-independent):
#   * Recognise a chemkit calculation command in the Bash being run.
#   * Build a normalised "knob signature" of the consequential flags present.
#   * BLOCK (exit 2) the calc when, for this session, either:
#       - no acknowledgement has been recorded yet, OR
#       - the knob signature differs from the last acknowledged one
#         (catches the "we just used xtb, so reuse xtb" carry-over), OR
#       - a method-requiring subcommand is missing --method.
#   * The block message instructs the agent to STOP AND ASK THE USER (via
#     whatever question/clarification mechanism its harness provides — e.g.
#     AskUserQuestion in Claude Code, or simply a question turn in any other
#     harness), then record the acknowledgement by re-invoking THIS script with
#     `--ack '<signature>'`
#     (a separate, explicit action — not a flag stapled onto the calc), then
#     re-run. A matching ack lets the calc through with no friction.
#
# Two modes:
#   (1) Hook mode (default): reads the PreToolUse JSON event on stdin.
#   (2) Ack mode: `chemkit-method-gate.sh --ack '<sig>' --session '<id>'`
#       writes the per-session marker and exits 0. The agent runs this as a
#       Bash command after asking the user; the hook recognises its own --ack
#       invocation and lets it pass.
#
# Exit codes: 0 = allow; 2 = block (stderr shown to the agent).

set -uo pipefail

GATE_SCRIPT_BASENAME="chemkit-method-gate.sh"
MARKER_DIR="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/.chemkit-gate"

# Consequential knobs whose values define the level of theory / state.
# --opt is the build-from-smiles QM-refinement method (its analogue of --method).
# --density-fit toggles the RI approximation (off by default); a change to it
# between runs is a level-of-theory change worth re-confirming.
KNOBS=(--method --opt --functional --basis --tier --density-fit --solvent --charge --mult --multiplicity)

# Subcommands / skill folders that REQUIRE an explicit --method (everything
# except the pure structure builder, which uses an optional --opt instead).
# Listed for documentation / future use; the gate keys off --method presence.
METHOD_REQUIRED_SUBCMDS="sp opt freq binding redox confsearch frontier electrostatics solvation logp profile pka fukui ts irc scan orbitals"

# ---------------------------------------------------------------------------
# Ack mode: record the acknowledgement for a session, then exit 0.
#   --ack '<signature>'   the knob signature the user confirmed
#   --session '<id>'      the session id (echoed in the block message)
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--ack" ]]; then
  ack_sig="${2:-}"
  ack_session=""
  if [[ "${3:-}" == "--session" ]]; then
    ack_session="${4:-}"
  fi
  if [[ -z "$ack_session" ]]; then
    echo "chemkit-method-gate: --ack requires --session '<id>'." >&2
    exit 1
  fi
  mkdir -p "$MARKER_DIR"
  printf '%s\n' "$ack_sig" > "$MARKER_DIR/${ack_session}.ack"
  echo "chemkit-method-gate: acknowledged level-of-theory for session ${ack_session}: ${ack_sig:-<none>}" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Hook mode: parse the PreToolUse event from stdin.
# ---------------------------------------------------------------------------
INPUT="$(cat)"
command -v jq >/dev/null 2>&1 || { exit 0; }   # no jq -> do not interfere

CMD="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')"
SESSION="$(printf '%s' "$INPUT" | jq -r '.session_id // empty')"
[[ -z "$CMD" ]] && exit 0

# Let the agent's own ack invocation through (it is run as a Bash command and
# would otherwise re-trigger this hook).
if [[ "$CMD" == *"$GATE_SCRIPT_BASENAME"* && "$CMD" == *"--ack"* ]]; then
  exit 0
fi

# Is this a chemkit calculation command? Match a skill script under
# skills/<name>/scripts/ OR a direct engine call (_engine.cli / _mcp_client).
is_chemkit=0
if [[ "$CMD" == *"skills/"*"/scripts/"*".py"* ]] \
   || [[ "$CMD" == *"_engine.cli"* ]] \
   || [[ "$CMD" == *"_mcp_client"* ]]; then
  is_chemkit=1
fi
[[ "$is_chemkit" -eq 0 ]] && exit 0   # not a chemkit calc -> never interfere

# The pure structure builder takes no required level of theory; don't gate it
# unless it carries a QM-refine knob. Detect build-from-smiles / `build`.
is_build=0
if [[ "$CMD" == *"build-from-smiles"* ]] || [[ "$CMD" == *" build "* ]] \
   || [[ "$CMD" == *"_engine.cli build"* ]]; then
  is_build=1
fi

# Extract a normalised knob signature: for each consequential flag present in
# the command, capture "name=value" (supports "--flag value" and "--flag=value").
# Sorted + space-joined so order doesn't change the signature.
extract_sig() {
  local cmd="$1" sig="" knob val
  # tokenise on whitespace; walk tokens so we can grab the following value
  # shellcheck disable=SC2206
  local toks=($cmd)
  local i n=${#toks[@]}
  for ((i = 0; i < n; i++)); do
    local t="${toks[i]}"
    for knob in "${KNOBS[@]}"; do
      if [[ "$t" == "$knob" ]]; then
        val="${toks[i+1]:-}"
        # normalise the --multiplicity alias to --mult
        [[ "$knob" == "--multiplicity" ]] && knob="--mult"
        sig+="${knob}=${val} "
      elif [[ "$t" == "${knob}="* ]]; then
        val="${t#*=}"
        [[ "$knob" == "--multiplicity" ]] && knob="--mult"
        sig+="${knob}=${val} "
      fi
    done
  done
  # sort tokens for a stable signature
  printf '%s' "$sig" | tr ' ' '\n' | sed '/^$/d' | sort | paste -sd' ' -
}

SIG="$(extract_sig "$CMD")"

# Does the command carry an explicit --method?
has_method=0
[[ "$CMD" == *"--method "* || "$CMD" == *"--method="* ]] && has_method=1

MARKER="$MARKER_DIR/${SESSION}.ack"
STORED_SIG=""
[[ -f "$MARKER" ]] && STORED_SIG="$(cat "$MARKER" 2>/dev/null)"

emit_block() {
  local reason="$1"
  cat >&2 <<EOF
==================== chemkit method-gate: BLOCKED ====================
$reason

Per calculation-reporting-standards non-negotiable #10, do NOT guess or
silently carry over the level of theory. Before running this calculation:

  1. STOP AND ASK THE USER (use your harness's question mechanism — e.g.
     AskUserQuestion in Claude Code, or a plain clarifying turn otherwise) to
     confirm:
       --method   (xtb | mopac | dft | hf)
     and, where relevant:
       --functional / --basis / --tier   (DFT)
       --solvent  (or explicit gas phase)
       --charge   and   --mult
  2. Record the acknowledgement by running EXACTLY this command (a separate
     step, after the user answers):

       bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/${GATE_SCRIPT_BASENAME}" \\
         --ack '${SIG}' --session '${SESSION}'

  3. Re-run the calculation.

Also (non-negotiable #9): the moment the run launches, give the user the live
.out log path and offer 'tail -f'.

Current level-of-theory signature for this command:
  ${SIG:-<none provided>}
=====================================================================
EOF
  exit 2
}

# A pure structure build carrying NO consequential knobs has no level of theory
# to confirm — let it through unconditionally. (A build that DOES carry a
# QM-refine knob, e.g. --opt dft, falls through to the signature checks below so
# that choice is still confirmed.)
if [[ "$is_build" -eq 1 && -z "$SIG" ]]; then
  exit 0
fi

# Method-requiring command missing --method (argparse would also catch this,
# but we give the teaching message first). Builds are exempt (they use an
# optional --opt instead of a required --method).
if [[ "$is_build" -eq 0 && "$has_method" -eq 0 ]]; then
  emit_block "This calculation has no explicit --method."
fi

# No acknowledgement yet for this session.
if [[ ! -f "$MARKER" ]]; then
  emit_block "No level-of-theory has been confirmed with the user in this session."
fi

# Acknowledgement exists but the knobs changed since it was recorded.
if [[ "$SIG" != "$STORED_SIG" ]]; then
  emit_block "The level of theory changed since it was last confirmed
(was: ${STORED_SIG:-<none>}; now: ${SIG:-<none>})."
fi

# Signature matches the acknowledged set -> allow with no friction.
exit 0
