#!/usr/bin/env python3
"""assay MCP server — one unified engine behind the open MCP protocol.

Exposes every assay skill as an MCP tool. The chemistry engine lives once, in
the repo-root `assay_core/` library; this server owns it and dispatches each tool
call to the engine's CLI. Built on the official `mcp` SDK (FastMCP) over stdio,
so it works with ANY MCP-capable client, not just one vendor.

Each tool mirrors a assay subcommand. A tool takes the same arguments the CLI
takes, as a list of CLI tokens (`args`), runs `python -m assay_core.cli
<task> <args>` as an isolated subprocess, and returns the JSON result the engine
prints.
Running each calculation in its own process keeps long, stateful QM jobs (pyscf
globals, matplotlib backends, chdir/tmpdirs) from leaking across calls.

Run:  python mcp_server/server.py        # stdio MCP server
"""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
ENGINE_DIR = REPO_ROOT / "assay_core"
SKILLS_DIR = REPO_ROOT / "skills"

# Skills inverted to the self-contained architecture (DESIGN.md): the server runs
# their `skills/<pkg>/scripts/run.py` directly instead of `-m assay_core.cli
# <subcommand>`. DISCOVERED from disk (assay_core.discovery reads each skill's
# SKILL_NAME/SUBCOMMAND manifest), so it can never drift from the skill folders —
# maps engine subcommand -> skill package dir. A subcommand not discovered here
# falls back to the engine CLI path in _run_engine.
def _discover_converted() -> dict:
    from assay_core import discovery
    return {info.subcommand: info.package
            for info in discovery.discover_skills().values()}


CONVERTED_SKILLS = _discover_converted()


def _skill_run_path(pkg: str) -> Path:
    return SKILLS_DIR / pkg / "scripts" / "run.py"

# --------------------------------------------------------------------------- #
# Live engine-subprocess registry — lets an interactive caller (the assay
# agent REPL) hard-abort an in-flight calculation on `stop`/Ctrl-C. Each
# _run_engine Popen registers itself here while running and removes itself when
# done; kill_active_engines() SIGTERMs whatever is currently live. Thread-safe
# because the agent turn runs on a background thread while the main thread reads
# stdin. No-op when nothing is running.
# --------------------------------------------------------------------------- #
_ACTIVE_ENGINES: "set[subprocess.Popen]" = set()
_ACTIVE_ENGINES_LOCK = threading.Lock()


def _register_engine(proc: "subprocess.Popen") -> None:
    with _ACTIVE_ENGINES_LOCK:
        _ACTIVE_ENGINES.add(proc)


def _unregister_engine(proc: "subprocess.Popen") -> None:
    with _ACTIVE_ENGINES_LOCK:
        _ACTIVE_ENGINES.discard(proc)


def kill_active_engines() -> int:
    """Stop every currently-running engine subprocess. Sends SIGTERM, then
    escalates to SIGKILL for any that do not exit within a short grace period.
    Returns the number signalled. Used by the interactive agent to hard-stop a
    run."""
    with _ACTIVE_ENGINES_LOCK:
        procs = list(_ACTIVE_ENGINES)
    signalled = []
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
                signalled.append(p)
        except Exception:  # noqa: BLE001 - best-effort; process may have exited
            pass
    # Escalate: give SIGTERM a moment, then SIGKILL anything still alive so a
    # backend that traps/ignores SIGTERM cannot keep running after `stop`.
    deadline = time.monotonic() + 2.0
    for p in signalled:
        try:
            remaining = max(0.0, deadline - time.monotonic())
            p.wait(timeout=remaining)
        except Exception:  # noqa: BLE001 - TimeoutExpired or already-reaped
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:  # noqa: BLE001
                pass
    return len(signalled)

# tool name -> (engine subcommand, skill package dir) — DISCOVERED, not
# hand-maintained. assay_core.discovery walks skills/*/scripts/run.py and reads
# each skill's SKILL_NAME/SUBCOMMAND manifest + build_parser(), so this registry
# can never drift from the skills on disk (DESIGN.md §10.2). The shape is
# unchanged ({tool: (subcommand, folder)}) so every downstream use still works.
def _discover_tools() -> dict:
    from assay_core import discovery
    infos = discovery.discover_skills()
    return {info.name: (info.subcommand, info.package)
            for info in infos.values()}


TOOLS = _discover_tools()

# Server instructions injected into every connecting MCP client (Claude Code
# renders these as a "# MCP Server Instructions" context block automatically, and
# any MCP-capable client receives them in the `initialize` response). Committed to
# the repo so the guidance ships with the package — every user who connects gets
# ASSAY's operating rules with no per-user setup. Kept lean deliberately: it
# orients and points to rules/*.md rather than pasting them, since it loads into
# context every session. Guarded so a missing file never breaks startup.
_INSTRUCTIONS_PATH = HERE / "INSTRUCTIONS.md"
_INSTRUCTIONS = (
    _INSTRUCTIONS_PATH.read_text(encoding="utf-8")
    if _INSTRUCTIONS_PATH.exists()
    else None
)

# log_level="WARNING" keeps the SDK's per-request INFO chatter (e.g.
# "Processing request of type CallToolRequest") off the server's stderr, which a
# stdio caller inherits — so the caller's stderr leads with the live-log path and
# real diagnostics, not transport noise.
mcp = FastMCP("assay", log_level="WARNING", instructions=_INSTRUCTIONS)


def _arg_spec(subcommand: str) -> str:
    """Derive the subcommand's argument spec from the engine CLI so the tool
    description advertises exact args (flags, types, choices, required) — letting
    an agent call correctly WITHOUT a `--help` round-trip. Best-effort: returns
    "" if the engine can't be imported (description still works without it)."""
    try:
        from assay_core.cli import format_subcommand_args
        return format_subcommand_args(subcommand)
    except Exception:  # pragma: no cover - never break tool registration
        return ""


def _description(skill_folder: str, subcommand: str) -> str:
    """Build a tool description from the skill's SKILL.md frontmatter + the
    derived argument spec, so an AI knows what the tool does AND the exact valid
    arguments without needing to round-trip `args=["--help"]`."""
    md = SKILLS_DIR / skill_folder / "SKILL.md"
    desc = ""
    if md.is_file():
        text = md.read_text()
        m = re.search(r"^description:\s*(.+?)\s*$", text, re.MULTILINE)
        if m:
            desc = m.group(1).strip()
    arg_spec = _arg_spec(subcommand)
    args_block = (f"\n\nArguments (assay `{subcommand}`):\n{arg_spec}"
                  if arg_spec else "")
    usage = (
        "\n\nInvoke by passing these as a list of CLI tokens in `args` "
        "(e.g. [\"--method\", \"xtb\", \"mol.xyz\"]). `cwd` sets the directory "
        "for relative input/output paths. Returns the result as JSON. (You can "
        "still run args=[\"--help\"] for the raw argparse help.)"
    )
    return (desc or f"assay {subcommand}") + args_block + usage


def _run_engine(subcommand: str, args: list[str], cwd: str | None = None) -> str:
    """Run the engine CLI as an isolated subprocess; return its JSON stdout.

    `cwd` is the CALLER's working directory: relative input paths and `--out`
    destinations must resolve against where the user/AI invoked the tool, not
    against the server's own directory. Defaults to the server dir if absent.

    A CONVERTED skill (DESIGN.md inversion) is run via its self-contained
    `skills/<pkg>/scripts/run.py` — the inverted arrow, the server calling the
    skill. Every other subcommand still routes through `-m assay_core.cli
    <subcommand>` until it is converted. Both paths are identical to the caller:
    the same argv, the same result JSON.

    The heavy lifting (live `.out` log announced at launch, CHEMKIT_REMOTE_HOST
    ssh, structured error envelopes) lives in `assay_core.runlog` so a stand-alone
    skill run gets the same behavior. This wrapper only supplies the command +
    PYTHONPATH and registers the live subprocess so an interactive `stop` can
    SIGTERM it.
    """
    from assay_core import runlog

    env = dict(os.environ)
    # Make `import assay_core` (and `import skills.*`) resolve to the repo-root
    # source tree for the subprocess.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    pkg = CONVERTED_SKILLS.get(subcommand)
    if pkg is not None:
        run_py = _skill_run_path(pkg)
        cmd = [sys.executable, str(run_py), *args]
        # The parent (runlog, below) already tees the live `.out`; tell the child
        # skill's run_cli spine not to write a SECOND one.
        env["ASSAY_SUPPRESS_LIVE_OUT"] = "1"
    else:
        cmd = [sys.executable, "-m", "assay_core.cli", subcommand, *args]
    return runlog.run_skill_subprocess(
        cmd, label=subcommand, args=args, cwd=cwd, env=env,
        default_cwd=str(HERE),
        on_start=_register_engine, on_end=_unregister_engine,
    )


# ---------------------------------------------------------------------------
# Cross-cutting tool decorators.
#
# FastMCP exposes no tool middleware / before-after hooks (the @mcp.tool
# decorator is the only seam), so boundary concerns are added as decorators that
# wrap the tool function INSIDE _make_tool — one place that covers all 20 tools
# (and, transitively, the `assay` CLI, which routes through these same tools).
# ---------------------------------------------------------------------------

# Per-tool call logging is on by default but terse; set CHEMKIT_LOG_TOOLS=0 to
# silence it on a quiet host (mirrors the FastMCP log_level="WARNING" restraint).
_LOG_TOOLS = os.environ.get("CHEMKIT_LOG_TOOLS", "1") not in ("0", "", "false", "no")


def _result_ok_tag(result: str) -> str:
    """Classify a tool's JSON result as ok/fail for the log line, without
    raising on non-JSON. 'fail' if it carries an `error` key or an integrity
    block that is not trustworthy; 'ok' otherwise."""
    try:
        d = json.loads(result)
    except (ValueError, TypeError):
        return "ok"  # non-JSON (e.g. --help text) is not a failure
    if not isinstance(d, dict):
        return "ok"
    if "error" in d:
        return "fail"
    integ = d.get("integrity")
    if isinstance(integ, dict) and integ.get("trustworthy") is False:
        return "fail"
    return "ok"


def log_tool_call(tool_name: str):
    """Emit ONE structured stderr line per tool call (name, args, cwd, duration,
    ok/fail) — the per-tool observability the server otherwise lacks. Times only
    the work; never swallows the return value or raises. Gated by
    CHEMKIT_LOG_TOOLS. stderr is the server's diagnostic channel (a stdio caller
    sees it in the Bash result, like the existing live-log line)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not _LOG_TOOLS:
                return fn(*a, **kw)
            t0 = time.perf_counter()
            tag = "fail"
            try:
                result = fn(*a, **kw)
                tag = _result_ok_tag(result)
                return result
            finally:
                dur_ms = int((time.perf_counter() - t0) * 1000)
                # Log whatever call shape came in (typed kwargs or raw args[]).
                shown = kw.get("args")
                if shown is None:
                    shown = [f"{k}={v}" for k, v in kw.items()
                             if k not in ("cwd",) and v is not None]
                arglist = ",".join(str(x) for x in (shown or []))
                sys.stderr.write(
                    f"[assay] tool={tool_name} args=[{arglist}] "
                    f"cwd={kw.get('cwd') or '.'} dur={dur_ms}ms {tag}\n"
                )
                sys.stderr.flush()
        return wrapper
    return deco


def tool_error_envelope(subcommand: str):
    """Outer safety net: guarantee a tool ALWAYS returns well-formed JSON, even
    on an UNEXPECTED exception (a bug, or _run_engine raising before it can
    format its own error). _run_engine's deliberate in-band error JSON (which
    carries integrity verdicts) passes through untouched — this only catches what
    would otherwise surface to the agent as an opaque MCP transport error."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as exc:  # noqa: BLE001 - never leak a raw transport error
                return json.dumps({
                    "error": f"assay {subcommand} failed: "
                             f"{type(exc).__name__}: {exc}",
                    "subcommand": subcommand,
                    "args": list(kw.get("args") or []),
                })
        return wrapper
    return deco


import inspect  # noqa: E402 - used by the per-skill signature synthesizer below
from typing import List as _List, Literal as _Literal, Optional as _Optional  # noqa: E402


def _annotation_for(p) -> Any:
    """Python type annotation for one arg_spec.Param, in the form FastMCP turns
    into the JSON schema we want. Enums become Literal[...] so the client sees an
    `enum`; the annotation is always Optional[...] so `null` lands in the type
    UNION (not as an enum member — an enum `null` makes the argo/Gemini endpoint
    500). Lists (append actions) become list[...].

    Every param is Optional at the SCHEMA level even when the skill requires it,
    for two reasons: (1) the back-compat `args` raw-token path must be callable
    without also filling the typed fields (Pydantic validates the schema BEFORE
    our body runs, so a schema-required field would reject a valid `args` call);
    (2) true requiredness is enforced by the engine's argparse, which gives a
    clear `error: the following arguments are required: --ha` — better than a
    generic Pydantic rejection. Required params are still surfaced to the agent
    in the tool description's arg list (marked "required")."""
    if p.is_bool:
        base: Any = bool
    elif p.annotation_is_enum:
        base = _Literal[tuple(p.choices)]  # type: ignore[valid-type]
    else:
        base = p.py_type
    if p.is_list:
        base = _List[base]  # type: ignore[valid-type]
    return _Optional[base]


def _snake(tool_name: str) -> str:
    """kebab-case tool name -> a valid python identifier for the synthesized
    function's __name__ (used only to name the generated schema model)."""
    return tool_name.replace("-", "_")


def _make_tool(tool_name: str, subcommand: str, skill_folder: str):
    """Register one MCP tool with its OWN typed signature.

    Instead of the old shared generic signature (xyz/method/charge/.../extra_args),
    each tool advertises exactly the arguments its skill actually has — required
    scientific flags included (e.g. redox-potential shows ox_charge/red_charge;
    pka-acidity shows ha/a_minus). The MCP SDK validates types/enums before the
    call, so an agent cannot invent a flag, mistype an enum, or (the key fix for
    the many-arg skills) fill a field the skill does not have: the wrapper only
    ever emits params that exist for THIS subcommand, so nothing gets injected
    that the subcommand would reject.

    Mechanics: the SDK derives a tool's JSON schema from the function signature
    (inspect.signature). We keep ONE generic body and give it a SYNTHESIZED
    __signature__ built by introspecting the SKILL's own build_parser() (the
    inverted source of truth, via arg_spec.params_from_parser); the shared
    arg_spec.params_to_argv turns the validated kwargs back into engine argv.
    """
    from assay_core import arg_spec as _arg_spec_mod
    from assay_core import discovery as _discovery

    description = _description(skill_folder, subcommand)
    _bp = _discovery.build_parser_for(subcommand)
    if _bp is not None:
        _parser = _bp()
        params = _arg_spec_mod.params_from_parser(_parser)
        allowed_flags = _arg_spec_mod.known_flags_from_parser(_parser)
    else:  # pragma: no cover - a skill without a discoverable parser
        params = _arg_spec_mod.skill_params(subcommand)
        allowed_flags = _arg_spec_mod.known_flags(subcommand)
    param_names = {p.name for p in params}

    @tool_error_envelope(subcommand)
    @log_tool_call(tool_name)
    def impl(**kwargs) -> str:
        # Back-compat: a raw CLI token list still wins (the `assay` front door,
        # older callers). Everything else flows through the typed → argv path.
        raw = kwargs.pop("args", None)
        cwd = kwargs.pop("cwd", None)
        extra = kwargs.pop("extra_args", None)
        if raw:
            return _run_engine(subcommand, list(raw), cwd=cwd)
        # Validate the slim escape hatch: reject any unknown flag rather than
        # passing it through to argparse blindly (with a did-you-mean hint).
        if extra:
            bad = _validate_extra_flags(extra, allowed_flags)
            if bad:
                return json.dumps({
                    "error": (f"assay {subcommand}: unknown flag(s) in "
                              f"extra_args: {', '.join(bad)}. Use the typed "
                              f"parameters instead of raw flags where possible."),
                    "subcommand": subcommand,
                    "valid_flags": sorted(allowed_flags),
                })
        typed = {k: v for k, v in kwargs.items() if k in param_names}
        argv = _arg_spec_mod.params_to_argv(subcommand, typed, extra_args=extra)
        return _run_engine(subcommand, argv, cwd=cwd)

    # Build the per-skill signature: the skill's typed params + the three
    # cross-cutting wrapper params (extra_args / args / cwd), all keyword-only.
    # Every param is keyword-only with a default (None for required ones, the
    # argparse default otherwise) so the schema never marks a field required —
    # requiredness is enforced by the engine (see _annotation_for). This keeps
    # the back-compat `args` raw-token path callable without the typed fields.
    sig_params = [
        inspect.Parameter(
            p.name, inspect.Parameter.KEYWORD_ONLY,
            annotation=_annotation_for(p),
            default=(p.default if (not p.required and p.default is not None)
                     else None),
        )
        for p in params
    ]
    sig_params += [
        inspect.Parameter("extra_args", inspect.Parameter.KEYWORD_ONLY,
                          annotation=_Optional[_List[str]], default=None),
        inspect.Parameter("args", inspect.Parameter.KEYWORD_ONLY,
                          annotation=_Optional[_List[str]], default=None),
        inspect.Parameter("cwd", inspect.Parameter.KEYWORD_ONLY,
                          annotation=_Optional[str], default=None),
    ]
    impl.__signature__ = inspect.Signature(sig_params, return_annotation=str)
    impl.__name__ = _snake(tool_name)
    impl.__doc__ = _TOOL_DOC

    mcp.add_tool(impl, name=tool_name, description=description)
    return impl


def _validate_extra_flags(extra: list, allowed: set) -> list[str]:
    """Return the list of --flags in `extra` that are not valid for this skill.
    Only tokens that look like flags (start with '-') are checked; values are
    left alone."""
    bad = []
    for tok in extra:
        s = str(tok)
        if s.startswith("-") and not _looks_like_negative_number(s):
            flag = s.split("=", 1)[0]  # handle --flag=value
            if flag not in allowed:
                bad.append(flag)
    return bad


def _looks_like_negative_number(s: str) -> bool:
    """True for '-1', '-0.5' etc. — a value, not a flag (so a charge of -1 in
    extra_args isn't mistaken for an unknown flag)."""
    try:
        float(s)
        return True
    except ValueError:
        return False


# Shared docstring for every generated tool (the per-skill args are advertised in
# the tool's typed schema + its description; this covers the reporting contract).
_TOOL_DOC = (
    "Run this assay skill. Fill the TYPED parameters this tool advertises — "
    "they are exactly the arguments this skill accepts (required ones have no "
    "default). Do NOT pass raw CLI flags; there is no need to guess flag names. "
    "`extra_args` is a rare escape hatch for a flag with no typed parameter "
    "(unknown flags are rejected with a suggestion). `cwd` resolves relative "
    "input/output paths. (`args`, a raw CLI token list, is still accepted for "
    "back-compat and takes precedence when given.)\n\n"
    "REPORTING CONTRACT — surface warnings verbatim. If the result JSON has a "
    "`warnings` array, you MUST relay EVERY warning to the user verbatim (none "
    "dropped, summarized, or paraphrased). The result includes a ready-to-paste "
    "`warnings_block` field — relay that ONE field verbatim and you have surfaced "
    "them all correctly. Also report the `integrity.trustworthy` verdict, and "
    "never present a computed value as experimental."
)


for _name, (_sub, _folder) in TOOLS.items():
    _make_tool(_name, _sub, _folder)


def main() -> None:
    """Console entry point (`assay-mcp`): start the stdio MCP server."""
    mcp.run()  # stdio transport


# ---------------------------------------------------------------------------
# `assay` human-facing CLI front door.
#
# Routes a shell call `assay <subcommand> <args...>` THROUGH the MCP server
# (via the shared _mcp_client), exactly like the per-skill wrapper scripts do —
# so it inherits every server-path guarantee: the live `.out` log is streamed
# and its path surfaced (calculation-reporting-standards #9), and the in-engine
# --accept-defaults level-of-theory gate + integrity gate still apply.
#
# Subcommand -> MCP tool name is derived from TOOLS (the single source of truth),
# so this never drifts from the server's own dispatch table.
# ---------------------------------------------------------------------------

# subcommand (e.g. "sp") -> tool name (e.g. "single-point-energy")
_SUBCOMMAND_TO_TOOL = {sub: name for name, (sub, _folder) in TOOLS.items()}


def _chemkit_usage() -> str:
    subs = ", ".join(sorted(_SUBCOMMAND_TO_TOOL))
    return (
        "usage:\n"
        "  assay <subcommand> [args...]        run ONE calculation\n"
        "  assay [--base-url URL] [--model M]  start the interactive AGENT (REPL)\n"
        "  assay --model M --prompt \"...\"       run ONE agent request and exit\n\n"
        "CALCULATION mode runs a assay skill through the MCP server (live .out\n"
        "log + level-of-theory/integrity gates apply):\n"
        f"  subcommands: {subs}\n"
        "  assay sp --help                       args for one subcommand\n"
        "  assay sp --method xtb mol.xyz\n"
        "  assay redox --method dft --tier standard --ox-charge 0 --red-charge -1 mol.xyz\n\n"
        "AGENT mode opens a conversational assistant that drives the skills over\n"
        "an OpenAI-compatible endpoint (env: CHEMKIT_LLM_BASE_URL / _MODEL /\n"
        "_API_KEY). It is entered when no subcommand is given (bare `assay`) or\n"
        "the first argument is an option:\n"
        "  assay --base-url http://127.0.0.1:60639/v1 --model argo:o3\n"
        "  assay --model argo:o3 --prompt \"single-point energy of water.xyz with xtb\"\n"
        "  assay --help-agent                    full agent-mode options\n\n"
        "Discovery:  assay --list-skills [--json]\n"
        "MCP server (for external agents/hosts): assay-mcp\n"
    )


def cli_main(argv: list[str] | None = None) -> int:
    """Console entry point (`assay`): two modes, dispatched on the first arg.

    * ``assay sp --method xtb mol.xyz`` — run ONE calculation via the MCP
      server (the first token is a skill subcommand).
    * ``assay`` / ``assay --base-url X --model Y [--prompt "..."]`` — launch
      the INTERACTIVE AGENT (a REPL, or one-shot with --prompt). Reached when
      argv is empty or the first token is an option (starts with ``-``), so it
      cannot collide with a subcommand.

    ``-h``/``--help`` and ``--list-skills`` are reserved and handled here first.
    Returns the underlying exit code.
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(_chemkit_usage())
        return 0

    # `assay --help-agent` — full agent-mode option list (argparse --help).
    if argv and argv[0] == "--help-agent":
        from mcp_server.agent_cli import main as _agent_main
        return _agent_main(["--help"])

    # `assay --list-skills [--json]` — discovery, handled by the engine.
    if argv and argv[0] == "--list-skills":
        rest = argv[1:]
        try:
            from assay_core.cli import list_skills  # type: ignore
            sys.stdout.write(list_skills(as_json=("--json" in rest)))
            return 0
        except Exception:  # noqa: BLE001
            sys.stdout.write(_chemkit_usage())
            return 0

    # Agent mode: no subcommand given (empty argv) or the first token is an
    # option (e.g. --base-url / --model / --prompt) rather than a skill name.
    if not argv or argv[0].startswith("-"):
        from mcp_server.agent_cli import main as _agent_main
        return _agent_main(argv)

    subcommand = argv[0]
    rest = argv[1:]

    # Resolve descriptive aliases (frontier-orbitals -> frontier, ...) to the
    # canonical subcommand via the engine's alias map (single source of truth),
    # so `assay frontier-orbitals ...` works at the human/agent front door too.
    try:
        from assay_core.cli import _alias_to_canonical  # type: ignore
        subcommand = _alias_to_canonical().get(subcommand, subcommand)
    except Exception:  # noqa: BLE001
        pass

    tool_name = _SUBCOMMAND_TO_TOOL.get(subcommand)
    if tool_name is None:
        # did-you-mean suggestion from the engine's fuzzy matcher.
        hint = ""
        try:
            from assay_core.cli import _suggest_subcommand  # type: ignore
            sug = _suggest_subcommand(subcommand)
            if sug:
                hint = f" did you mean {sug!r}?"
        except Exception:  # noqa: BLE001
            pass
        sys.stderr.write(
            f"assay: unknown subcommand {subcommand!r}.{hint}\n\n" + _chemkit_usage()
        )
        return 2

    # `assay <sub> --help-json` — machine-readable arg spec for one subcommand,
    # derived from the skill's own build_parser() (discovery/introspection), so it
    # matches exactly what the MCP tool advertises. Handled before the calc path.
    if "--help-json" in rest:
        try:
            from assay_core import discovery, cli as _cli  # type: ignore
            bp = discovery.build_parser_for(subcommand)
            if bp is not None:
                spec = _cli.describe_parser(bp())
                aliases = _cli.SUBCOMMAND_ALIASES.get(subcommand, [])
                sys.stdout.write(json.dumps(
                    {"subcommand": subcommand, "aliases": aliases,
                     "arguments": spec}, indent=2) + "\n")
                return 0
        except Exception:  # noqa: BLE001 - fall through to engine CLI
            pass
        try:
            from assay_core.cli import main as engine_main  # type: ignore
            return int(engine_main([subcommand, *rest]) or 0)
        except SystemExit as e:
            return int(e.code or 0)

    # A per-subcommand help request (e.g. `assay pka --help`) is NOT a
    # calculation: it must not spawn a subprocess, create a live `.out` log, or
    # get wrapped in result JSON. Print the SKILL's own build_parser() help
    # directly (the inverted source of truth), in-process, and exit. Fall back to
    # the engine CLI's help only if the skill parser isn't discoverable.
    if "-h" in rest or "--help" in rest:
        try:
            from assay_core import discovery  # type: ignore
            bp = discovery.build_parser_for(subcommand)
        except Exception:  # noqa: BLE001
            bp = None
        if bp is not None:
            try:
                bp().parse_args(["--help"])   # argparse prints help + SystemExit(0)
                return 0
            except SystemExit as e:
                return int(e.code or 0)
        # Fallback: engine CLI help.
        try:
            from assay_core.cli import main as engine_main  # type: ignore
            return int(engine_main([subcommand, *rest]) or 0)
        except SystemExit as e:
            return int(e.code or 0)
        except Exception:  # noqa: BLE001
            pass

    # Dispatch the calculation by running the skill DIRECTLY (via _run_engine,
    # which routes a converted skill to its skills/<pkg>/scripts/run.py through
    # runlog — same path the MCP tool uses). No MCP round-trip: the human CLI is
    # the front door to the skills, exactly like the design intends. All gates,
    # the live `.out` log, and the integrity verdict apply because they live in
    # the skill's run_cli spine + runlog.
    return _dispatch_calc(subcommand, rest)


def _dispatch_calc(subcommand: str, rest: list[str]) -> int:
    """Run one calculation for the human CLI front door and shape its output like
    the old `_mcp_client.run_skill`: surface the live `.out` path on stderr, print
    the result JSON to stdout, and translate an engine error object into a stderr
    message + nonzero exit."""
    out = _run_engine(subcommand, rest, cwd=os.getcwd())
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = None

    # Live-log path first on stderr, on EVERY run, so it lands at the top of the
    # caller's terminal/Bash result regardless of --stdout mode (calc-reporting #9).
    if isinstance(parsed, dict):
        out_log = parsed.get("out_log")
        if out_log:
            sys.stderr.write(
                f"assay: live log: {out_log}\n"
                "# Tell the user this path immediately, while the run is going "
                "(non-negotiable #9).\n"
            )

    if isinstance(parsed, dict) and "error" in parsed:
        engine_stderr = parsed.get("stderr") or ""
        if engine_stderr:
            sys.stderr.write(engine_stderr.rstrip() + "\n")
        sys.stderr.write(f"assay: {parsed['error']}\n")
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    main()
