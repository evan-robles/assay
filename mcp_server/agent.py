"""Reusable agent loop for ATLAS/chemkit — shared by the benchmark and the CLI.

This module owns the *benchmark-agnostic* pieces of the agentic loop that were
historically welded inside ``benchmarks/fidelity_driver.py``:

  * the tool schemas the model sees (``CHEMKIT_TOOL``, ``LIST_SKILLS_TOOL``,
    ``SKILL_HELP_TOOL``) — one typed ``chemkit`` tool plus two discovery tools;
  * the system prompt (``LIVE_INSTRUCTIONS``) and rule injection (``load_rules``);
  * engine dispatch for a tool call (``_dispatch_tool``), routed IN-PROCESS through
    the MCP server's ``_run_engine`` so every call keeps the integrity gate, the
    live ``.out`` log, and the level-of-theory gate;
  * a single turn driver (``run_agent_turn``) that both the interactive REPL and
    (optionally) the benchmark can reuse.

Design references (DESIGN.md):
  * §11 proposes this exact interactive REPL and its tool set
    (``chemkit``/``final_report``/``list_skills``/``skill_help``); this module is
    the "factor the loop into one reusable function" answer to §11 open-Q #2.
  * §11 open-Q #1 is resolved to **in-process** engine execution; the ONLY place
    that touches the engine is ``_dispatch_tool`` → ``server._run_engine``, so the
    planned migration (§10 item 10: move that logic to ``assay_core.runlog``) is a
    one-function change here.
  * Skill names are NOT hardcoded — they are derived from the authoritative
    registry ``server.TOOLS`` — so this survives the discovery-driven server of §3.

``final_report`` itself and all scoring stay in ``fidelity_driver.py``; this
module deliberately knows nothing about specs or grading.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Repo root: mcp_server/agent.py -> repo/. rules/ lives at the repo root.
_REPO = Path(__file__).resolve().parent.parent
_RULES_DIR = _REPO / "rules"


# --------------------------------------------------------------------------- #
# Skill registry (single source of truth, NOT a hardcoded list)
# --------------------------------------------------------------------------- #
def skill_names() -> List[str]:
    """Canonical skill (tool) names, from the server's authoritative registry.

    Sourced from ``server.TOOLS`` so this never drifts from the real tool set and
    survives the DESIGN §3 migration to discovery-driven registration."""
    from mcp_server import server
    return list(server.TOOLS.keys())


# --------------------------------------------------------------------------- #
# Typed-args -> engine argv (the ONE shared converter the server also uses)
# --------------------------------------------------------------------------- #
def typed_args_to_argv(params: Dict[str, Any]) -> List[str]:
    """Convert the model's typed ``chemkit`` tool params into canonical engine
    argv via ``chemkit_engine.arg_spec.params_to_argv`` — the same converter the
    MCP server uses. A value for a field the chosen skill lacks is dropped (not
    injected); the legacy ``xyz`` alias maps to the skill's real positional."""
    from chemkit_engine import arg_spec as _A
    skill = params.get("skill")
    if not skill:
        return []
    values = {k: v for k, v in params.items()
              if k not in ("skill", "extra_args") and v is not None}
    if "xyz" in values:
        pos = next((p.name for p in _A.skill_params(skill) if p.positional), None)
        if pos and pos not in values:
            values[pos] = values.pop("xyz")
        else:
            values.pop("xyz", None)
    extra = [str(a) for a in (params.get("extra_args") or [])]
    return _A.params_to_argv(skill, values, extra_args=extra)


# --------------------------------------------------------------------------- #
# Tool schemas the model sees
# --------------------------------------------------------------------------- #
def _build_chemkit_tool() -> Dict[str, Any]:
    """Build the ``chemkit`` tool schema from the engine arg-spec — the SAME
    single source of truth the MCP server uses. One tool with a ``skill`` enum
    plus the UNION of every skill's typed params (all optional but ``skill``);
    the shared ``params_to_argv`` emits only the params the chosen skill accepts,
    so a value for a field the skill lacks is dropped rather than injected.

    Enums are string ``enum``s with nullability via a ``["<type>","null"]`` union,
    never a ``None`` enum member (a None enum member 500s argo's Gemini endpoint)."""
    from chemkit_engine import arg_spec as _A

    _PY_TO_JSON = {int: "integer", float: "number", str: "string", bool: "boolean"}
    names = skill_names()
    props: Dict[str, Any] = {
        "skill": {"type": "string", "enum": list(names),
                  "description": "which skill to run"},
        "xyz": {"type": ["string", "null"],
                "description": "positional input: geometry path (or SMILES/name "
                               "for build-from-smiles / name-to-smiles). Prefer the "
                               "skill's own named positional where shown."},
        "extra_args": {"type": "array", "items": {"type": "string"},
                       "description": "rare skill-specific CLI flags only; unknown "
                                      "flags are rejected"},
    }
    for skill in names:
        for p in _A.skill_params(skill):
            base = _PY_TO_JSON.get(p.py_type, "string")
            if p.name not in props:
                if p.is_list:
                    schema: Dict[str, Any] = {
                        "type": ["array", "null"],
                        "items": {"type": _PY_TO_JSON.get(p.py_type, "string")},
                    }
                elif p.annotation_is_enum:
                    schema = {"type": ["string", "null"], "enum": list(p.choices)}
                else:
                    schema = {"type": [base, "null"]}
                if p.help:
                    schema["description"] = p.help[:180]
                props[p.name] = schema
            elif p.annotation_is_enum and "enum" in props[p.name]:
                merged = list(dict.fromkeys(props[p.name]["enum"] + list(p.choices)))
                props[p.name]["enum"] = merged

    return {
        "type": "function",
        "function": {
            "name": "chemkit",
            "description": (
                "Run a chemkit computational-chemistry skill. Set `skill`, then "
                "fill the TYPED fields this skill needs — do NOT pass raw CLI "
                "flags. Each skill's required inputs are typed fields (e.g. "
                "redox-potential needs ox_charge & red_charge; pka-acidity needs "
                "ha & a_minus; single-geometry skills use the positional `xyz`/"
                "`input`). Gas phase is the default (omit `solvent`). `extra_args` "
                "is a rare escape hatch; unknown flags are rejected. Returns the "
                "raw result JSON."
            ),
            "parameters": {
                "type": "object",
                "properties": props,
                "required": ["skill"],
            },
        },
    }


CHEMKIT_TOOL = _build_chemkit_tool()

LIST_SKILLS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": (
            "List every chemkit skill. Returns JSON {\"subcommands\": [{"
            "\"subcommand\": canonical name, \"aliases\": [other accepted names], "
            "\"help\": one-line description}, ...]}. Pass a `subcommand` or any of "
            "its `aliases` as the `skill` field of the `chemkit` tool. Call this "
            "if you are unsure which skill name to use — do not guess."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILL_HELP_TOOL = {
    "type": "function",
    "function": {
        "name": "skill_help",
        "description": (
            "Get the exact valid arguments (flags, types, choices, required, "
            "positional) for one chemkit skill. Call this if you are unsure of "
            "the correct flags — do not invent flags like --phase or --geometry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill": {"type": "string",
                          "description": "skill name to describe, e.g. fukui-reactivity"},
            },
            "required": ["skill"],
        },
    },
}

# The discovery + chemistry tools every agentic caller gets. `final_report` is
# added by the benchmark (structured scored answer) and is OPTIONAL for the REPL
# (an "I'm done" signal), so it is NOT part of this default set.
DEFAULT_TOOLS = [CHEMKIT_TOOL, LIST_SKILLS_TOOL, SKILL_HELP_TOOL]


# --------------------------------------------------------------------------- #
# System prompt + rule injection
# --------------------------------------------------------------------------- #
LIVE_INSTRUCTIONS = (
    "You are a computational-chemistry assistant. Use the `chemkit` tool to do "
    "the requested task — never guess or fabricate a result; only report what a "
    "tool actually returned. The `chemkit` tool takes TYPED fields: set `skill`, "
    "`xyz` (the geometry path, or a SMILES/name for build-from-smiles/"
    "name-to-smiles), and the typed options `method`/`charge`/`multiplicity`/"
    "`solvent`/`functional`/`basis`/`tier` as fields — do NOT pass raw CLI flag "
    "strings. Gas phase is the default (omit `solvent`). Use `extra_args` ONLY "
    "for rare skill-specific flags. If unsure which skill or fields apply, call "
    "`list_skills`/`skill_help` first. "
    "Do NOT assume the level of theory: if the user did not specify a `method` "
    "(xtb/mopac/dft/hf) for a skill that needs one, ASK them which to use rather "
    "than silently picking one. "
    "RUN THE FEWEST SKILLS THAT ANSWER THE QUESTION — never invoke a skill whose "
    "output you will not use, and stop as soon as you have the answer. A pure "
    "IDENTITY / LOOKUP question (molecular formula, atom count, canonical SMILES, "
    "resolving a name to a SMILES) needs NO 3D structure: answer it from "
    "`name-to-smiles` (derive a molecular formula by counting atoms in the "
    "returned SMILES) and do NOT call `build-from-smiles` or any calculation. "
    "IDENTIFY THE INPUT TYPE YOURSELF — the user will not label it. Given a "
    "molecule reference for a task that DOES need a geometry, decide which of "
    "three it is: (1) a FILE PATH (ends in .xyz/.sdf/.pdb or looks like a path) → "
    "pass it directly as the geometry to the skill; (2) a SMILES string → first "
    "call `build-from-smiles` to make a 3D geometry, then run the requested skill "
    "on that geometry; (3) a plain chemical NAME (common or IUPAC, e.g. 'aspirin', "
    "'acetic acid') → call `name-to-smiles` then `build-from-smiles`, then the "
    "skill. Only build a 3D geometry when a downstream skill actually requires "
    "one. Recognize a "
    "SMILES WITHOUT being told: it is a single whitespace-free token of "
    "chemistry symbols — organic-subset element letters (C, N, O, P, S, F, Cl, "
    "Br, I, and lowercase aromatic c/n/o/s/p), digits for ring closures, and "
    "the punctuation ()[]=#@+-\\/%. — e.g. 'CCO', 'c1ccccc1', 'CC(=O)O', "
    "'O=C=O', '[Na+].[Cl-]'. A string with spaces or ordinary English words is "
    "a NAME, not a SMILES; a string with a dot AND a filename extension is a "
    "PATH. If genuinely ambiguous, ask. "
    "ALWAYS WRITE A RESULT SUMMARY YOURSELF from the tool's JSON — this is "
    "mandatory on every run. The tool ALREADY RETURNED the full result to you in "
    "the tool response; read the numbers out of that JSON and report them "
    "directly. NEVER tell the user to open, `cat`, or `tail` a file to see the "
    "answer, and NEVER say you cannot show the result — you have it. The live "
    "`.out` path is an EXTRA convenience to mention, never a substitute for "
    "stating the answer. Your summary MUST include: the headline number(s) at "
    "full precision (no rounding), the method / level of theory and software "
    "used, charge/multiplicity, solvent or gas phase, and the engine's "
    "integrity.trustworthy verdict. For a follow-up question about a run you "
    "already did (e.g. 'what was the HOMO-LUMO gap?'), answer from the JSON you "
    "already received — do not re-run unless needed. WARNINGS ARE HANDLED FOR "
    "YOU: the tool result carries a `warnings_block` — relay it verbatim to the "
    "user; never drop, summarize, or paraphrase a warning. A computed/built "
    "result is NEVER labeled 'experimental'."
)

# chemkit's standards, injected into the agent's system prompt. All four are
# included so the interactive agent operates under the full rule set:
# calculation-reporting + research govern runtime behavior (how results and
# citations are reported); workflow + skill standards add the multi-skill
# procedure and authoring conventions. (The fidelity benchmark injects only the
# first two — the runtime-behavior pair; the CLI opts into the complete set.)
DEFAULT_RULES = [
    "calculation-reporting-standards",
    "research-standards",
    "workflow-standards",
    "skill-standards",
]


def load_rules(names: List[str]) -> str:
    """Read the named rules/*.md files and concatenate them for the prompt.

    Reads from disk at runtime so the session always uses the CURRENT rules. A
    missing file is skipped with a warning rather than silently dropped."""
    chunks: List[str] = []
    for name in names:
        path = _RULES_DIR / f"{name}.md"
        if not path.exists():
            print(f"[agent] WARNING: rule file not found, NOT injected: {path}")
            continue
        chunks.append(f"\n===== BEGIN rules/{name}.md =====\n"
                      + path.read_text()
                      + f"\n===== END rules/{name}.md =====\n")
    if not chunks:
        return ""
    return (
        "\n\nThe following chemkit standards are BINDING. Follow them exactly "
        "when running the calculation and writing your report (method-provenance "
        "block, honest provenance labels, surfacing warnings and the live .out "
        "log path, never fabricating or guessing a citation):\n"
        + "".join(chunks)
    )


def system_prompt(rules: Optional[List[str]] = None) -> str:
    """Full system prompt = live instructions + injected rules (default set)."""
    rule_names = DEFAULT_RULES if rules is None else rules
    return LIVE_INSTRUCTIONS + load_rules(rule_names)


# --------------------------------------------------------------------------- #
# Discovery tool bodies (registry-sourced, not a literal)
# --------------------------------------------------------------------------- #
def list_skills_json() -> str:
    """JSON listing of every skill (canonical name + aliases + description),
    from the engine's authoritative discovery — the same data
    ``chemkit --list-skills --json`` surfaces."""
    try:
        from chemkit_engine.cli import list_skills as _ls
        return _ls(as_json=True)
    except Exception as exc:  # noqa: BLE001 - discovery must never crash a turn
        return json.dumps({"error": f"list_skills failed: {exc}",
                           "skills": skill_names()})


def skill_help_json(skill: str) -> str:
    """JSON help (valid flags/types/choices/positional) for one skill, via the
    engine's ``--help-json`` for that skill's subcommand.

    An unknown skill returns a structured 'unknown skill' error (with the valid
    names) rather than shelling the engine with a bogus subcommand."""
    from mcp_server import server
    entry = server.TOOLS.get(_resolve_skill(skill))
    if entry is None:
        return json.dumps({"error": f"unknown skill {skill!r}",
                           "valid_skills": skill_names()})
    subcommand = entry[0]
    try:
        # The engine emits --help-json for a subcommand on stdout.
        return server._run_engine(subcommand, ["--help-json"])
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"skill_help failed for {skill!r}: {exc}"})


def _resolve_skill(name: str) -> str:
    """Map an alias/subcommand to the canonical skill (tool) name."""
    from mcp_server import server
    if name in server.TOOLS:
        return name
    # subcommand -> tool name
    for tool_name, (sub, _folder) in server.TOOLS.items():
        if name == sub:
            return tool_name
    try:
        from chemkit_engine.cli import _alias_to_canonical
        canon = _alias_to_canonical().get(name, name)
        for tool_name, (sub, _folder) in server.TOOLS.items():
            if canon == sub:
                return tool_name
    except Exception:  # noqa: BLE001
        pass
    return name


# --------------------------------------------------------------------------- #
# Tool dispatch (the ONLY place that touches the engine)
# --------------------------------------------------------------------------- #
def _dispatch_tool(name: str, args: Dict[str, Any], *, cwd: Optional[str] = None) -> str:
    """Execute one tool call and return the tool-result string.

    IN-PROCESS via the MCP server's ``_run_engine`` (DESIGN §11 open-Q #1), so the
    integrity gate, the live ``.out`` log, and the level-of-theory gate all apply.
    This is the single seam a future ``assay_core.runlog`` migration would touch."""
    if name == "list_skills":
        return list_skills_json()
    if name == "skill_help":
        return skill_help_json(str(args.get("skill", "")))
    if name == "chemkit":
        from mcp_server import server
        skill = args.get("skill")
        tool = _resolve_skill(str(skill)) if skill else None
        entry = server.TOOLS.get(tool) if tool else None
        if entry is None:
            return json.dumps({"error": f"unknown skill {skill!r}",
                               "valid_skills": skill_names()})
        subcommand = entry[0]
        argv = typed_args_to_argv({**args, "skill": tool})
        return server._run_engine(subcommand, argv, cwd=cwd)
    return json.dumps({"error": f"unknown tool {name!r}"})


# --------------------------------------------------------------------------- #
# The reusable turn driver
# --------------------------------------------------------------------------- #
class RunCancelled(Exception):
    """Raised inside ``run_agent_turn`` when ``should_cancel()`` returns True —
    a cooperative abort at a round-trip boundary (the interactive `stop`)."""


def run_agent_turn(
    client,
    model: str,
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    max_turns: int = 12,
    cwd: Optional[str] = None,
    on_tool: Optional[Callable[[str, Dict[str, Any], str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    """Drive one user turn to completion, mutating and returning ``messages``.

    Calls the model; while it emits tool calls, dispatches each via
    ``_dispatch_tool`` and feeds the result back; stops when the model returns a
    plain assistant message (no tool call) OR calls ``final_report`` (an optional
    "I'm done" signal — its args are appended as an acknowledgement, no scoring).
    ``on_tool(name, args, result)`` is called after each tool call for surfacing
    (e.g. the live ``.out`` path) — the REPL uses it; the benchmark need not.

    ``should_cancel``: an optional predicate polled at each round-trip boundary
    (before a model call, and before/after each tool call). When it returns True,
    the turn stops immediately by raising ``RunCancelled`` — this is how the
    interactive `stop` aborts a running agent without killing the REPL. The
    caller is responsible for any hard side effects (e.g. killing an in-flight
    engine subprocess); this loop only stops issuing further work.

    Returns the updated message list; the final assistant text is the last
    assistant message's ``content``.
    """
    tools = tools if tools is not None else DEFAULT_TOOLS

    def _check_cancel():
        if should_cancel is not None and should_cancel():
            raise RunCancelled()

    for _turn in range(max_turns):
        _check_cancel()
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=tools, tool_choice="auto",
        )
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            return messages
        messages.append(msg.model_dump(exclude_none=True))
        for call in calls:
            fn = call.function.name
            try:
                fargs = json.loads(call.function.arguments or "{}")
            except ValueError:
                fargs = {}
            if fn == "final_report":
                # Optional end-of-turn signal in interactive use. No scoring here;
                # surface any prose the model included, then end the turn.
                prose = str(fargs.get("prose", "")).strip()
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps({"ack": True})})
                if prose:
                    messages.append({"role": "assistant", "content": prose})
                return messages
            _check_cancel()   # don't start a new tool call after a stop
            result = _dispatch_tool(fn, fargs, cwd=cwd)
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": result})
            if on_tool is not None:
                try:
                    on_tool(fn, fargs, result)
                except Exception:  # noqa: BLE001 - surfacing must never break the loop
                    pass
            _check_cancel()   # a stop during the tool call ends the turn now
    # Turn budget exhausted without a final plain message.
    messages.append({"role": "assistant",
                     "content": "[reached max tool-call turns without a final "
                                "answer — try rephrasing or raising --max-turns]"})
    return messages
