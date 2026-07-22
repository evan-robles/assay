"""Reusable agent loop for ASSAY/chemkit — shared by the benchmark and the CLI.

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
import math
import os
import re
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
    "mandatory on EVERY calculation run, including follow-up questions; a bare "
    "number is NOT a sufficient summary. The tool ALREADY RETURNED the full "
    "result to you in the tool response; read the numbers out of that JSON and "
    "report them directly. NEVER tell the user to open, `cat`, or `tail` a file to see the "
    "answer, and NEVER say you cannot show the result — you have it. The live "
    "`.out` path is an EXTRA convenience to mention, never a substitute for "
    "stating the answer. Your summary MUST include: the headline number(s) at "
    "full precision (no rounding), the method / level of theory and software "
    "used, charge/multiplicity, solvent or gas phase, the engine's "
    "integrity.trustworthy verdict, AND the full path of EVERY file the run "
    "generated — the result JSON, the live `.out` log, and any geometry (.xyz), "
    "plot (.png), trajectory, cube, or molden files listed in the result JSON. "
    "Always tell the user exactly what files were written and where. For a "
    "follow-up question about a run you "
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


# --------------------------------------------------------------------------- #
# Mandatory result-summary enforcement (REPL / one-shot only)
#
# A `chemkit` calculation ALWAYS returns the full result JSON to the model, yet a
# model may end its turn with a bare number (or nothing). For interactive use we
# GUARANTEE a complete summary every run: after the turn, if a calculation ran
# but the final assistant text is missing/incomplete, we force one model re-call
# (tool_choice="none"), and if that still fails, synthesize the summary from the
# JSON deterministically. Gated by `enforce_summary` so the fidelity benchmark
# keeps measuring the model's UNAIDED reporting.
# --------------------------------------------------------------------------- #

def _tool_result_dicts(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse every ``role == "tool"`` message's content as JSON (skip non-JSON)."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        if m.get("role") != "tool":
            continue
        try:
            d = json.loads(m.get("content") or "")
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _last_calculation_result(
    messages: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """The most recent tool result that is a real CALCULATION — i.e. carries a
    ``headline_value`` (the ``canonicalize()`` stamp). Discovery/lookup results
    (list_skills, skill_help, name-to-smiles) have no headline and are skipped,
    so identity questions are never forced to carry a calculation summary."""
    for d in reversed(_tool_result_dicts(messages)):
        if "subcommands" in d or "arguments" in d:   # discovery-tool result
            continue
        if d.get("headline_value") is not None or d.get("error"):
            return d
    return None


# Result-JSON keys whose values are always metadata, never a generated artifact
# path — excluded from generic file-path discovery so we don't list the input.
_NON_ARTIFACT_PATH_KEYS = {"input_file", "input_path", "cli", "cli_invocation"}
# Substrings marking a key as an output-artifact path (covers current task keys:
# optimized_xyz, best_xyz, diagram_png, molden_path, cube_paths, *_trajectory,
# reaction_profile, plot, ensemble_xyz, out_log, out, …) and any future ones.
_ARTIFACT_KEY_HINTS = ("xyz", "png", "molden", "cube", "mgf", "traj", "profile",
                       "plot", "diagram", "path", "log", "json", "file", "out")


def _looks_like_path(s: Any) -> bool:
    """A string that plausibly names a file on disk (has a dir separator or a
    recognizable extension) — used to filter generic path discovery."""
    if not isinstance(s, str) or not s.strip():
        return False
    if "/" in s or "\\" in s:
        return True
    return "." in s and s.rsplit(".", 1)[-1].isalnum() and len(s.rsplit(".", 1)[-1]) <= 6


def _abspath(path: str, base_dir: Optional[str]) -> str:
    """Absolute form of ``path``. Already-absolute paths are returned normalized;
    a relative path is resolved against ``base_dir`` (the run's cwd) when given,
    else against the process cwd. Surfacing an absolute ADDRESS lets the user
    locate/open the file unambiguously, regardless of where they launched from."""
    if os.path.isabs(path):
        return os.path.normpath(path)
    if base_dir:
        return os.path.normpath(os.path.join(base_dir, path))
    return os.path.abspath(path)


def collect_output_files(
    result: Dict[str, Any], base_dir: Optional[str] = None,
) -> List[str]:
    """Every generated-artifact path in a result JSON, as ``label: /abs/path``
    strings, discovered generically so new task output keys are surfaced
    automatically. Every path is normalized to an ABSOLUTE address (relative ones
    resolved against ``base_dir`` — the run cwd — else the process cwd). Handles
    scalar paths (``optimized_xyz``), lists (``ensemble_xyz``), and dicts of
    ``{label: path}`` (``cube_paths``). ``out`` (the result JSON) and ``out_log``
    (the live log) are always included first. Excludes the input geometry."""
    files: List[str] = []
    seen: set = set()

    def _add(label: str, val: Any) -> None:
        if not _looks_like_path(val):
            return
        absval = _abspath(val, base_dir)
        if absval not in seen:
            seen.add(absval)
            files.append(f"{label.replace('_', ' ')}: {absval}")

    # Canonical outputs first, in a stable order.
    if result.get("out"):
        _add("result JSON", result["out"])
    if result.get("out_log"):
        _add("live log", result["out_log"])

    for key, val in result.items():
        if key in ("out", "out_log") or key in _NON_ARTIFACT_PATH_KEYS:
            continue
        low = key.lower()
        if not any(h in low for h in _ARTIFACT_KEY_HINTS):
            continue
        if isinstance(val, str):
            _add(key, val)
        elif isinstance(val, list):
            for i, v in enumerate(val):
                _add(f"{key}[{i}]", v)
        elif isinstance(val, dict):
            for k, v in val.items():
                _add(f"{key}.{k}", v)
    return files


def _result_base_dir(result: Dict[str, Any]) -> Optional[str]:
    """The run's working directory, inferred from an absolute output path in the
    result (``out_log`` or ``out``). Used to resolve any relative task-emitted
    path to an absolute address."""
    for key in ("out_log", "out"):
        p = result.get(key)
        if isinstance(p, str) and os.path.isabs(p):
            return os.path.dirname(p)
    return None


def summarize_calculation_result(result: Dict[str, Any]) -> str:
    """Deterministically synthesize a complete result summary from a result JSON.
    The GUARANTEED fallback when a model refuses to summarize — always includes
    the headline number (full precision), method, charge/multiplicity, solvent,
    the integrity.trustworthy verdict, warnings verbatim, and EVERY generated
    file as an ABSOLUTE address (result JSON, live log, geometries, plots,
    trajectories, cubes, …)."""
    parts: List[str] = []
    hv = result.get("headline_value")
    hf = result.get("headline_field")
    hu = result.get("headline_units")
    if hv is not None:
        label = (hf or "result").replace("_", " ")
        parts.append(f"{label} = {hv}" + (f" {hu}" if hu else ""))
    if result.get("method"):
        parts.append(f"method: {result['method']}")
    if result.get("charge") is not None:
        parts.append(f"charge: {result['charge']}")
    mult = result.get("multiplicity")
    if mult is not None:
        parts.append(f"multiplicity: {mult}")
    parts.append(f"solvent: {result.get('solvent') or 'gas phase'}")
    integ = result.get("integrity") or {}
    if "trustworthy" in integ:
        parts.append(f"trustworthy: {integ['trustworthy']}")
    for w in (result.get("warnings") or []):
        parts.append(f"warning: {w}")
    if result.get("error"):
        parts.append(f"error: {result['error']}")
    output_files = collect_output_files(result, base_dir=_result_base_dir(result))
    if output_files:
        parts.append("files generated:")
        parts.extend(f"  - {f}" for f in output_files)
    return ("(engine-generated summary; the model did not provide one)\n"
            + "\n".join(parts))


def _mentions_headline_value(text: str, hv: Any) -> bool:
    """True if ``text`` reports the headline value — accepting the model's SENSIBLE
    ROUNDING, not just a full-precision verbatim paste.

    The old check required ``str(hv)`` as an exact substring, which rejected a
    good model summary that wrote e.g. "−2077.998 eV" for a stored
    -2077.9982313705, forcing the robotic engine fallback. Here we pass if:
      • the exact string appears (fast path, also covers non-numeric headlines), OR
      • any number in the text equals hv when both are rounded to a shared
        precision — matched to 4 significant figures (a tolerance that accepts a
        few-decimal rounding of an energy while still requiring the right number).
    """
    if hv is None:
        return True
    if str(hv) in text:
        return True
    # Models routinely write a Unicode minus (− U+2212) or figure dash instead of
    # ASCII '-'; normalize so a negative headline (e.g. an energy) is parsed with
    # its sign intact.
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    try:
        target = float(hv)
    except (TypeError, ValueError):
        return False  # non-numeric headline that wasn't an exact substring

    def _sig(x: float, figs: int = 4) -> float:
        if x == 0:
            return 0.0
        return round(x, -int(math.floor(math.log10(abs(x)))) + (figs - 1))

    target_sig = _sig(target)
    # Scan every number-like token in the text; accept if any matches to 4 sig figs.
    for tok in re.findall(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?", text):
        try:
            val = float(tok.replace(",", ""))
        except ValueError:
            continue
        if _sig(val) == target_sig:
            return True
        # also accept when the model rounded to fewer decimals than we stored
        for nd in range(0, 7):
            if round(target, nd) == round(val, nd) and round(val, nd) == val:
                return True
    return False


def _summary_is_complete(text: str, result: Dict[str, Any]) -> bool:
    """True if the assistant's final text already reports the calculation: it must
    mention the headline value (full precision OR a sensible rounding), the
    trustworthy verdict, AND every generated file path. Lenient on wording and on
    rounding, strict on the load-bearing facts and on surfacing artifacts."""
    if not text or not text.strip():
        return False
    low = text.lower()
    hv = result.get("headline_value")
    if not _mentions_headline_value(text, hv):
        return False
    integ = result.get("integrity") or {}
    if "trustworthy" in integ and ("trustworth" not in low and "integrity" not in low):
        return False
    # Every generated file must appear in the summary — accept either the
    # absolute address we'd surface or at least the file's basename (the model
    # may quote the path as it appeared in the JSON).
    for entry in collect_output_files(result, base_dir=_result_base_dir(result)):
        abspath = entry.split(": ", 1)[-1]
        if abspath not in text and os.path.basename(abspath) not in text:
            return False
    return True


_FORCE_SUMMARY_PROMPT = (
    "Summarize the calculation result now, directly to the user, from the tool "
    "JSON you already received. Your summary MUST include: the headline value at "
    "FULL precision (no rounding), the method / level of theory and software, "
    "charge and multiplicity, solvent (or gas phase), the integrity.trustworthy "
    "verdict, any warnings verbatim, AND the full path of every file the run "
    "generated (the result JSON, the live .out log, and any geometry/plot/"
    "trajectory/cube/molden files). Do NOT run another tool."
)


def run_agent_turn(
    client,
    model: str,
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    max_turns: int = 12,
    cwd: Optional[str] = None,
    on_tool: Optional[Callable[[str, Dict[str, Any], str], None]] = None,
    on_tool_start: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    enforce_summary: bool = False,
) -> List[Dict[str, Any]]:
    """Drive one user turn to completion, mutating and returning ``messages``.

    Calls the model; while it emits tool calls, dispatches each via
    ``_dispatch_tool`` and feeds the result back; stops when the model returns a
    plain assistant message (no tool call) OR calls ``final_report`` (an optional
    "I'm done" signal — its args are appended as an acknowledgement, no scoring).
    ``on_tool(name, args, result)`` is called after each tool call for surfacing
    (e.g. the live ``.out`` path) — the REPL uses it; the benchmark need not.
    ``on_tool_start(name, args)`` is called just BEFORE each tool is dispatched,
    so the REPL can show a live "running [skill]…" indicator while the call is in
    flight. Optional; the benchmark leaves it unset.

    ``should_cancel``: an optional predicate polled at each round-trip boundary
    (before a model call, and before/after each tool call). When it returns True,
    the turn stops immediately by raising ``RunCancelled`` — this is how the
    interactive `stop` aborts a running agent without killing the REPL. The
    caller is responsible for any hard side effects (e.g. killing an in-flight
    engine subprocess); this loop only stops issuing further work.

    ``enforce_summary``: when True (REPL / one-shot), GUARANTEE a complete result
    summary after any turn that ran a calculation — force one model re-call, then
    synthesize from the JSON if the model still won't. Left False for the fidelity
    benchmark so it measures the model's UNAIDED reporting.

    Returns the updated message list; the final assistant text is the last
    assistant message's ``content``.
    """
    tools = tools if tools is not None else DEFAULT_TOOLS

    def _check_cancel():
        if should_cancel is not None and should_cancel():
            raise RunCancelled()

    def _finish() -> List[Dict[str, Any]]:
        """Common exit: optionally guarantee a complete calculation summary."""
        if enforce_summary:
            _ensure_summary(client, model, messages)
        return messages

    for _turn in range(max_turns):
        _check_cancel()
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=tools, tool_choice="auto",
        )
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            return _finish()
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
                return _finish()
            _check_cancel()   # don't start a new tool call after a stop
            if on_tool_start is not None:
                try:
                    on_tool_start(fn, fargs)
                except Exception:  # noqa: BLE001 - surfacing must never break the loop
                    pass
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
    return _finish()


def _ensure_summary(client, model: str, messages: List[Dict[str, Any]]) -> None:
    """GUARANTEE a complete calculation summary as the final assistant message.

    No-op unless this turn ran a real calculation (a tool result with a
    ``headline_value``). If the current final assistant text already reports the
    headline value and the trustworthy verdict, leave it. Otherwise force ONE
    model re-call (``tool_choice="none"`` so it cannot run another tool); if that
    still fails the check (or errors), append a deterministic engine summary so a
    completed run is NEVER shown without its numbers."""
    result = _last_calculation_result(messages)
    if result is None:
        return  # no calculation this turn (e.g. an identity/lookup answer)

    # Current final assistant prose, if any.
    final_text = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            final_text = m["content"]
            break
    if _summary_is_complete(final_text, result):
        return

    # Tier 1: force one model re-call that must produce prose (no tools).
    try:
        probe = messages + [{"role": "user", "content": _FORCE_SUMMARY_PROMPT}]
        resp = client.chat.completions.create(
            model=model, messages=probe, tool_choice="none",
        )
        forced = (resp.choices[0].message.content or "").strip()
        if _summary_is_complete(forced, result):
            messages.append({"role": "assistant", "content": forced})
            return
    except Exception:  # noqa: BLE001 - never let enforcement break the turn
        pass

    # Tier 2: deterministic synthesis from the JSON — the hard guarantee.
    messages.append({"role": "assistant",
                     "content": summarize_calculation_result(result)})
