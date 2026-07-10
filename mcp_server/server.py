#!/usr/bin/env python3
"""chemkit MCP server — one unified engine behind the open MCP protocol.

Exposes every chemkit skill as an MCP tool. The chemistry engine lives once, in
`mcp_server/chemkit_engine/`; this server owns it and dispatches each tool call
to the engine's CLI. Built on the official `mcp` SDK (FastMCP) over stdio, so it
works with ANY MCP-capable client, not just one vendor.

Each tool mirrors a chemkit subcommand. A tool takes the same arguments the CLI
takes, as a list of CLI tokens (`args`), runs `python -m chemkit_engine.cli
<task> <args>` as an isolated subprocess, and returns the JSON result the engine
prints.
Running each calculation in its own process keeps long, stateful QM jobs (pyscf
globals, matplotlib backends, chdir/tmpdirs) from leaking across calls.

Run:  python mcp_server/server.py        # stdio MCP server
"""
from __future__ import annotations

import datetime
import functools
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
ENGINE_DIR = HERE / "chemkit_engine"
SKILLS_DIR = HERE.parent / "skills"

# tool name -> (engine subcommand, skill folder for its SKILL.md description)
# Tool names == skill folder names (kebab-case). Mirrors the chemkit CLI
# subcommands; one entry per skill.
TOOLS = {
    "single-point-energy":     ("sp",             "single-point-energy"),
    "geometry-optimize":       ("opt",            "geometry-optimize"),
    "vibrational-analysis":    ("freq",           "vibrational-analysis"),
    "binding-energy":          ("binding",        "binding-energy"),
    "redox-potential":         ("redox",          "redox-potential"),
    "conformer-search":        ("confsearch",     "conformer-search"),
    "frontier-orbitals":       ("frontier",       "frontier-orbitals"),
    "electrostatics":          ("electrostatics", "electrostatics"),
    "solvation":               ("solvation",      "solvation"),
    "logp-partition":          ("logp",           "logp-partition"),
    "reaction-profile":        ("profile",        "reaction-profile"),
    "pka-acidity":             ("pka",            "pka-acidity"),
    "build-from-smiles":       ("build",          "build-from-smiles"),
    "name-to-smiles":          ("resolve",        "name-to-smiles"),
    "fukui-reactivity":        ("fukui",          "fukui-reactivity"),
    "transition-state":        ("ts",             "transition-state"),
    "intrinsic-reaction-coordinate": ("irc",      "intrinsic-reaction-coordinate"),
    "reaction-energy":         ("rxn-energy",     "reaction-energy"),
    "conformational-analysis": ("scan",           "conformational-analysis"),
    "visualize-orbitals":      ("orbitals",       "visualize-orbitals"),
}

# log_level="WARNING" keeps the SDK's per-request INFO chatter (e.g.
# "Processing request of type CallToolRequest") off the server's stderr, which a
# stdio caller inherits — so the caller's stderr leads with the live-log path and
# real diagnostics, not transport noise.
mcp = FastMCP("chemkit", log_level="WARNING")


def _arg_spec(subcommand: str) -> str:
    """Derive the subcommand's argument spec from the engine CLI so the tool
    description advertises exact args (flags, types, choices, required) — letting
    an agent call correctly WITHOUT a `--help` round-trip. Best-effort: returns
    "" if the engine can't be imported (description still works without it)."""
    try:
        from chemkit_engine.cli import format_subcommand_args
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
    args_block = (f"\n\nArguments (chemkit `{subcommand}`):\n{arg_spec}"
                  if arg_spec else "")
    usage = (
        "\n\nInvoke by passing these as a list of CLI tokens in `args` "
        "(e.g. [\"--method\", \"xtb\", \"mol.xyz\"]). `cwd` sets the directory "
        "for relative input/output paths. Returns the result as JSON. (You can "
        "still run args=[\"--help\"] for the raw argparse help.)"
    )
    return (desc or f"chemkit {subcommand}") + args_block + usage


def _run_engine(subcommand: str, args: list[str], cwd: str | None = None) -> str:
    """Run the engine CLI as an isolated subprocess; return its JSON stdout.

    `cwd` is the CALLER's working directory: relative input paths and `--out`
    destinations must resolve against where the user/AI invoked the tool, not
    against the server's own directory. Defaults to the server dir if absent.

    The engine prints the result JSON to stdout and human notes to stderr. On
    failure we return a JSON error object rather than raising, so the client
    always gets structured output.
    """
    env = dict(os.environ)
    # Make `import chemkit_engine` resolve to mcp_server/chemkit_engine for the subprocess.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERE), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    run_cwd = cwd if (cwd and os.path.isdir(cwd)) else str(HERE)
    cmd = [sys.executable, "-m", "chemkit_engine.cli", subcommand, *args]

    # --- Optional remote execution (CHEMKIT_REMOTE_HOST) -----------------------
    # On clusters (e.g. Aurora) the agent + this server can run on a LOGIN node
    # while the actual chemistry must run on a COMPUTE node (login nodes may lack
    # compute resources or, on Aurora, have an fs quirk that breaks the engine's
    # nested mkdir). If CHEMKIT_REMOTE_HOST is set, run the engine on that host
    # via ssh. This ASSUMES a SHARED $HOME/filesystem so `cd run_cwd` and all
    # input/--out paths resolve identically on both sides (true on Aurora, where
    # $HOME is mounted on compute nodes). The result JSON still comes back on the
    # ssh stdout, and the live `.out` is written locally from the tee'd stderr,
    # so no file copy-back is needed under a shared filesystem.
    remote_host = os.environ.get("CHEMKIT_REMOTE_HOST", "").strip()
    if remote_host:
        # Reproduce cwd + PYTHONPATH on the remote side, then run the same cmd.
        # shlex.quote every piece so paths/args with spaces or shell metachars
        # survive the single remote shell string ssh runs.
        remote_inner = "cd {cwd} && PYTHONPATH={pp} {run}".format(
            cwd=shlex.quote(run_cwd),
            pp=shlex.quote(env["PYTHONPATH"]),
            run=" ".join(shlex.quote(c) for c in cmd),
        )
        ssh_opts = shlex.split(os.environ.get("CHEMKIT_REMOTE_SSH_OPTS", ""))
        cmd = ["ssh", *ssh_opts, remote_host, remote_inner]

    # Live `.out` log the user can `tail -f` while the calculation runs.
    # Written in the CALLER's cwd so it sits next to their inputs/outputs.
    # The engine prints the result JSON to stdout and all human/PySCF/xtb/mopac
    # log text to stderr; we tee ONLY stderr into the .out line-by-line (stdout
    # is kept clean so the returned JSON never gets corrupted), then append the
    # final result JSON under a banner so the file is self-contained.
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(run_cwd, f"{subcommand}_{stamp}.out")

    def _write_header(fh):
        fh.write("# chemkit live log\n")
        fh.write(f"# subcommand : {subcommand}\n")
        fh.write(f"# args       : {' '.join(args)}\n")
        fh.write(f"# command    : {' '.join(cmd)}\n")
        fh.write(f"# cwd        : {run_cwd}\n")
        fh.write(f"# started    : {stamp}\n")
        fh.write("# " + "=" * 60 + "\n")
        fh.flush()

    timed_out = False
    try:
        # line-buffered so `tail -f` sees lines as they are produced.
        log_fh = open(out_path, "w", buffering=1, encoding="utf-8")
    except OSError:
        # If the .out can't be created (e.g. read-only cwd), fall back to the
        # original buffered behavior rather than failing the calculation.
        log_fh = None

    try:
        if log_fh is not None:
            _write_header(log_fh)
            proc = subprocess.Popen(
                cmd, cwd=run_cwd, env=env, text=True, bufsize=1,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

            # Reader thread: tee engine stderr -> .out as it arrives.
            stderr_chunks: list[str] = []

            def _pump_stderr():
                assert proc.stderr is not None
                for line in proc.stderr:
                    stderr_chunks.append(line)
                    log_fh.write(line)
                    log_fh.flush()

            t = threading.Thread(target=_pump_stderr, daemon=True)
            t.start()

            # Announce the live-log path AT LAUNCH (before the blocking read
            # below), so a caller learns where to `tail -f` while the
            # calculation is still running — calculation-reporting-standards
            # non-negotiable #9. Flush so it isn't buffered behind the result.
            sys.stderr.write(f"# chemkit live log (tail -f): {out_path}\n")
            sys.stderr.flush()

            stdout_data = ""
            try:
                # proc.stdout.read() blocks until the engine closes stdout,
                # i.e. until the process is done writing its result JSON.
                stdout_data = proc.stdout.read() if proc.stdout else ""
                proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                timed_out = True
            t.join(timeout=5)
            stderr_data = "".join(stderr_chunks)

            # Make the .out self-contained: append the result JSON at the end.
            log_fh.write("\n# " + "=" * 60 + "\n")
            log_fh.write("# ===== RESULT JSON (stdout) =====\n")
            log_fh.write(stdout_data.strip() + "\n")
            log_fh.flush()
        else:
            # Fallback: no live log; behave like the old capture_output path.
            try:
                proc_run = subprocess.run(
                    cmd, cwd=run_cwd, env=env,
                    capture_output=True, text=True, timeout=3600,
                )
                stdout_data, stderr_data = proc_run.stdout, proc_run.stderr
                returncode = proc_run.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                stdout_data = stderr_data = ""
    finally:
        if log_fh is not None:
            log_fh.close()

    if timed_out:
        return json.dumps({"error": "calculation timed out (3600 s)",
                           "subcommand": subcommand, "args": args,
                           "out_log": out_path})

    returncode = proc.returncode if log_fh is not None else returncode
    # (The live-log path is announced once at launch on line ~153 and is also
    # returned to the caller under `out_log`; no end-of-run duplicate needed.)

    if returncode != 0:
        # An integrity hard-abort exits nonzero but STILL prints the full
        # structured result (with an `integrity` block) to stdout. Preserve that
        # structured result — augmented with an `error` key so the caller still
        # treats it as a failure — instead of throwing it away for a truncated
        # stdout stub. This keeps the integrity verdict, warnings, and out-path
        # reachable by the agent while signalling that the number is untrustworthy.
        parsed = None
        try:
            parsed = json.loads(stdout_data.strip())
        except ValueError:
            parsed = None
        # Recognize either the full result JSON (has an `integrity` block, from
        # --stdout json) or the compact pointer (has `status`/`trustworthy`, from
        # --stdout path). Either way the structured verdict is worth preserving.
        is_integrity_result = isinstance(parsed, dict) and (
            isinstance(parsed.get("integrity"), dict)
            or ("trustworthy" in parsed and "status" in parsed)
        )
        if is_integrity_result:
            parsed["error"] = "integrity gate failed (result is not trustworthy)"
            parsed["returncode"] = returncode
            parsed.setdefault("out_log", out_path)
            return json.dumps(parsed)
        return json.dumps({
            "error": "chemkit engine exited non-zero",
            "returncode": returncode,
            "subcommand": subcommand, "args": args,
            "stderr": stderr_data.strip()[-4000:],
            "stdout": stdout_data.strip()[-2000:],
        })
    # On success the JSON result is on stdout. Inject the live-log path under
    # `out_log` so the caller (and ultimately the agent) learns where to
    # `tail -f` it — calculation-reporting-standards non-negotiable #9. The
    # server's own stderr writes (above) do NOT reach a stdio MCP caller, so the
    # returned JSON is the only channel that crosses back to the Bash tool.
    out = stdout_data.strip()
    try:
        parsed = json.loads(out)
        if log_fh is not None and isinstance(parsed, dict) and "out_log" not in parsed:
            parsed["out_log"] = out_path
            return json.dumps(parsed)
        return out
    except ValueError:
        wrapped = {"raw_stdout": out, "stderr": stderr_data.strip()}
        if log_fh is not None:
            wrapped["out_log"] = out_path
        return json.dumps(wrapped)


# ---------------------------------------------------------------------------
# Cross-cutting tool decorators.
#
# FastMCP exposes no tool middleware / before-after hooks (the @mcp.tool
# decorator is the only seam), so boundary concerns are added as decorators that
# wrap the tool function INSIDE _make_tool — one place that covers all 20 tools
# (and, transitively, the `chemkit` CLI, which routes through these same tools).
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
                    f"[chemkit] tool={tool_name} args=[{arglist}] "
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
                    "error": f"chemkit {subcommand} failed: "
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
    __signature__ built from arg_spec.skill_params(subcommand); the shared
    arg_spec.params_to_argv turns the validated kwargs back into engine argv.
    """
    from chemkit_engine import arg_spec as _arg_spec_mod

    description = _description(skill_folder, subcommand)
    params = _arg_spec_mod.skill_params(subcommand)
    allowed_flags = _arg_spec_mod.known_flags(subcommand)
    param_names = {p.name for p in params}

    @tool_error_envelope(subcommand)
    @log_tool_call(tool_name)
    def impl(**kwargs) -> str:
        # Back-compat: a raw CLI token list still wins (the `chemkit` front door,
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
                    "error": (f"chemkit {subcommand}: unknown flag(s) in "
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
    "Run this chemkit skill. Fill the TYPED parameters this tool advertises — "
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
    """Console entry point (`chemkit-mcp`): start the stdio MCP server."""
    mcp.run()  # stdio transport


# ---------------------------------------------------------------------------
# `chemkit` human-facing CLI front door.
#
# Routes a shell call `chemkit <subcommand> <args...>` THROUGH the MCP server
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
        "usage: chemkit <subcommand> [args...]\n\n"
        "Runs a chemkit calculation through the MCP server (same path as the\n"
        "skill scripts: live .out log + level-of-theory/integrity gates apply).\n\n"
        f"subcommands: {subs}\n\n"
        "Run a subcommand with --help for its arguments, e.g.:\n"
        "  chemkit sp --help\n"
        "  chemkit sp --method xtb mol.xyz\n"
        "  chemkit redox --method dft --tier standard --ox-charge 0 --red-charge -1 mol.xyz\n\n"
        "To start the MCP server instead (for agents), use: chemkit-mcp\n"
    )


def cli_main(argv: list[str] | None = None) -> int:
    """Console entry point (`chemkit`): run one calculation via the MCP server.

    `chemkit sp --method xtb mol.xyz` -> calls the `single-point-energy` MCP
    tool with the remaining argv. Returns the tool's exit code.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(_chemkit_usage())
        return 0 if argv else 2

    subcommand = argv[0]
    rest = argv[1:]

    # `chemkit --list-skills [--json]` — discovery, handled by the engine.
    if subcommand in ("--list-skills",):
        try:
            from chemkit_engine.cli import list_skills  # type: ignore
            sys.stdout.write(list_skills(as_json=("--json" in rest)))
            return 0
        except Exception:  # noqa: BLE001
            sys.stdout.write(_chemkit_usage())
            return 0

    # Resolve descriptive aliases (frontier-orbitals -> frontier, ...) to the
    # canonical subcommand via the engine's alias map (single source of truth),
    # so `chemkit frontier-orbitals ...` works at the human/agent front door too.
    try:
        from chemkit_engine.cli import _alias_to_canonical  # type: ignore
        subcommand = _alias_to_canonical().get(subcommand, subcommand)
    except Exception:  # noqa: BLE001
        pass

    tool_name = _SUBCOMMAND_TO_TOOL.get(subcommand)
    if tool_name is None:
        # did-you-mean suggestion from the engine's fuzzy matcher.
        hint = ""
        try:
            from chemkit_engine.cli import _suggest_subcommand  # type: ignore
            sug = _suggest_subcommand(subcommand)
            if sug:
                hint = f" did you mean {sug!r}?"
        except Exception:  # noqa: BLE001
            pass
        sys.stderr.write(
            f"chemkit: unknown subcommand {subcommand!r}.{hint}\n\n" + _chemkit_usage()
        )
        return 2

    # A per-subcommand help request (e.g. `chemkit pka --help`) is NOT a
    # calculation: it must not spawn the server, create a live `.out` log, or get
    # wrapped in result JSON. Print argparse's help directly, in-process, and
    # exit. (We import the engine CLI lazily and let argparse's own --help action
    # print + SystemExit; we translate that exit code back to an int.)
    if "-h" in rest or "--help" in rest:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        try:
            from chemkit_engine.cli import main as engine_main  # type: ignore
        except Exception:  # noqa: BLE001 — fall back to the server path if import fails
            engine_main = None
        if engine_main is not None:
            try:
                return int(engine_main([subcommand, *rest]) or 0)
            except SystemExit as e:  # argparse --help raises SystemExit(0)
                return int(e.code or 0)

    # Route through the shared MCP client (skills/_mcp_client.py), which speaks to
    # this same server. It lives in skills/, a sibling of mcp_server/.
    skills_dir = HERE.parent / "skills"
    if str(skills_dir) not in sys.path:
        sys.path.insert(0, str(skills_dir))
    try:
        from _mcp_client import run_skill  # type: ignore
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            sys.stderr.write("chemkit needs the MCP client SDK: pip install mcp\n")
            return 2
        sys.stderr.write(f"chemkit: could not load the MCP client ({exc}).\n")
        return 2
    return run_skill(tool_name, rest)


if __name__ == "__main__":
    main()
