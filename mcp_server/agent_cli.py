"""Interactive agent CLI for ASSAY (DESIGN.md §11).

Launches a conversational agent that drives the ASSAY skills over an
OpenAI-compatible endpoint. Two modes:

  * REPL (default): type natural-language chemistry requests; the agent runs
    skills and answers, across multiple turns, until you exit.
  * one-shot (``--prompt "..."``): run a single agentic task and exit.

This is a thin wrapper over ``mcp_server.agent`` (the shared agent loop), so it
inherits the typed ``assay`` tool, the discovery tools, the integrity gate,
the live ``.out`` log, and the level-of-theory guidance — the same machinery the
benchmark uses.

Endpoint / model resolution mirrors the benchmark's env vars so any configured
model (e.g. an argo-proxy model like ``argo:o3``) can back the chat:
  --base-url / CHEMKIT_LLM_BASE_URL   (default http://127.0.0.1:60639/v1)
  --model    / CHEMKIT_LLM_MODEL      (default argo:o3)
  --api-key  / CHEMKIT_LLM_API_KEY

Usage:
    assay --base-url http://127.0.0.1:60639/v1 --model argo:o3        # REPL
    assay --model argo:o3 --prompt "single-point energy of water.xyz with xtb"
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from typing import Any, Dict, List, Optional

from mcp_server import agent
from mcp_server import server as _server

_DEFAULT_BASE_URL = os.environ.get("CHEMKIT_LLM_BASE_URL", "http://127.0.0.1:60639/v1")
_DEFAULT_MODEL = os.environ.get("CHEMKIT_LLM_MODEL", "argo:o3")
_DEFAULT_API_KEY = os.environ.get("CHEMKIT_LLM_API_KEY", "")

# ASSAY ASCII banner shown at REPL launch (interactive TTY only).
_ASSAY_ART = r"""
      _     ____   ____      _    __   __
     / \   / ___| / ___|    / \   \ \ / /
    / _ \  \___ \ \___ \   / _ \   \ V /
   / ___ \  ___) | ___) | / ___ \   | |
  /_/   \_\|____/ |____/ /_/   \_\  |_|

  Agentic Simulation Suite for Automated chemistrY
"""

_BANNER = (
    _ASSAY_ART
    + "\nInteractive computational chemistry — 20 skills over one agent.\n"
    "Type a request (e.g. \"single-point energy of water.xyz with xtb\").\n"
    "Commands: 'exit'/'quit' or Ctrl-D to leave.\n"
)


class _EndpointUnreachable(Exception):
    """The LLM endpoint could not be contacted at all (connection-level)."""


def _available_models(client) -> List[str]:
    """Model ids the endpoint serves, via /v1/models.

    Returns ``[]`` if the endpoint is reachable but does not support model
    listing. Raises ``_EndpointUnreachable`` if the endpoint cannot be contacted
    at all (so callers can print a clean connection error, not a stack trace)."""
    try:
        return sorted(m.id for m in client.models.list().data)
    except Exception as exc:  # noqa: BLE001
        # Connection-level failures (ConnectError/timeout/DNS) mean the endpoint
        # is down/wrong; API-level failures (e.g. listing unsupported, auth) mean
        # it's reachable — treat only the former as fatal here.
        name = type(exc).__name__.lower()
        if "connect" in name or "timeout" in name or "connection" in name:
            raise _EndpointUnreachable(str(exc)) from exc
        return []


def _require_model(client, model: str, base_url: str) -> None:
    """Fail early with a clear, actionable message if the endpoint is unreachable
    or the requested model is not served."""
    try:
        available = _available_models(client)
    except _EndpointUnreachable as exc:
        raise SystemExit(
            f"error: cannot reach the LLM endpoint at {base_url!r}.\n"
            f"  ({exc})\n"
            f"check --base-url (or CHEMKIT_LLM_BASE_URL) and that the endpoint "
            f"/ tunnel is up."
        )
    if available and model not in available:
        preview = ", ".join(available[:20]) + (" …" if len(available) > 20 else "")
        raise SystemExit(
            f"error: model {model!r} is not available at this endpoint.\n"
            f"available models: {preview}\n"
            f"pick one with --model, or check --base-url."
        )


def _make_client(base_url: str, api_key: str):
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("error: the `openai` package is required "
                         "(pip install openai).")
    if not api_key:
        raise SystemExit(
            "error: no API key. Pass --api-key or set CHEMKIT_LLM_API_KEY "
            "(for argo-proxy this is your username)."
        )
    return OpenAI(base_url=base_url, api_key=api_key)


class _Spinner:
    """A lightweight 'working…' indicator on stderr so the user knows assay is
    running while the agent thinks / a calculation is in flight.

    Animates only on an interactive TTY (no spinner noise in piped/redirected
    output). `pause()`/`resume()` clear and restore the line so other prints
    (tool-call notes, the final answer) never collide with the spinner. Runs on
    its own daemon thread; `stop()` clears the line and joins."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str = "assay working", stream=sys.stderr):
        self._label = label
        self._stream = stream
        self._enabled = bool(getattr(stream, "isatty", lambda: False)())
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        i = 0
        while not self._stop.is_set():
            if not self._paused.is_set():
                frame = self._FRAMES[i % len(self._FRAMES)]
                self._stream.write(f"\r{frame} {self._label}… ")
                self._stream.flush()
                i += 1
            self._stop.wait(0.1)
        self._clear()

    def _clear(self) -> None:
        # Erase the current line (spinner) so nothing is left behind.
        self._stream.write("\r\033[K")
        self._stream.flush()

    def start(self) -> None:
        if not self._enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Hide the spinner and clear its line so a normal print won't collide."""
        if not self._enabled:
            return
        self._paused.set()
        self._clear()

    def resume(self) -> None:
        if not self._enabled:
            return
        self._paused.clear()

    def set_label(self, label: str) -> None:
        """Change the text shown next to the spinner (e.g. 'running sp')."""
        self._label = label

    def stop(self) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._clear()


# The spinner active for the current turn, so _on_tool can pause around its
# prints. None when no turn is running (or output is not a TTY).
_ACTIVE_SPINNER: Optional[_Spinner] = None


def _on_tool_start(name: str, args: Dict[str, Any]) -> None:
    """Show a live 'running [skill]…' indicator while a tool call is in flight.
    Updates the active spinner's label; the `chemkit` tool carries the concrete
    skill in args['skill'] (the dispatch tool name is still `chemkit`)."""
    if name == "chemkit":
        skill = args.get("skill", "?")
        label = f"running {skill}"
    else:
        label = f"running {name}"
    if _ACTIVE_SPINNER is not None:
        _ACTIVE_SPINNER.set_label(label)
    else:
        # Non-TTY (piped/redirected): emit a plain one-liner so the running skill
        # is still surfaced when there's no spinner.
        sys.stderr.write(f"  → {label}…\n")
        sys.stderr.flush()


def _on_tool(name: str, args: Dict[str, Any], result: str) -> None:
    """Surface each tool call to the user: the skill run and, per
    calculation-reporting-standards #9 / DESIGN §11.3, the live .out log path."""
    if name == "chemkit":
        skill = args.get("skill", "?")
        note = f"  → ran assay:{skill}"
    else:
        note = f"  → {name}"
    # Surface the live-log path when the engine returned one.
    out_log = None
    try:
        import json as _json
        parsed = _json.loads(result)
        if isinstance(parsed, dict):
            out_log = parsed.get("out_log")
            if parsed.get("error"):
                note += f"  [error: {parsed['error']}]"
    except Exception:  # noqa: BLE001
        pass
    if out_log:
        note += f"  (log: {out_log})"
    # Clear the spinner line, print the note, then let the spinner resume with the
    # generic label (the skill has finished; the model is thinking again).
    if _ACTIVE_SPINNER is not None:
        _ACTIVE_SPINNER.pause()
    print(note, file=sys.stderr)
    if _ACTIVE_SPINNER is not None:
        _ACTIVE_SPINNER.set_label("assay working")
        _ACTIVE_SPINNER.resume()


def _print_reply(messages: List[Dict[str, Any]]) -> None:
    """Print the agent's final reply (last non-empty assistant message). With
    ``enforce_summary=True`` the turn already guarantees a complete calculation
    summary as that message; this fallback (summarize the last engine result, else
    '(no reply)') is defense-in-depth for the no-calculation / no-prose case."""
    for m in reversed(messages):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            print(m["content"])
            return
    result = agent._last_calculation_result(messages)
    print(agent.summarize_calculation_result(result) if result else "(no reply)")


def _run_one(client, model: str, messages: List[Dict[str, Any]], *,
             max_turns: int, cwd: str) -> None:
    """Run one user turn to completion and print the reply (one-shot mode; no
    interactive cancellation)."""
    agent.run_agent_turn(
        client, model, messages,
        tools=agent.DEFAULT_TOOLS, max_turns=max_turns, cwd=cwd,
        on_tool=_on_tool, on_tool_start=_on_tool_start, enforce_summary=True,
    )
    _print_reply(messages)


def _stdin_stop_watch(cancel: threading.Event, done: threading.Event,
                      exit_req: threading.Event) -> None:
    """Background watcher: read whole lines from stdin while a turn runs and, on
    'stop'/'exit'/'quit', request cancellation (and remember an exit request).
    Works whether or not stdin is a TTY (line-buffered read), so piped input can
    interrupt too. Exits when `done` is set. Never touches `messages`."""
    while not done.is_set():
        try:
            import select
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
        except (OSError, ValueError):
            # select() on stdin is unsupported (e.g. Windows) or stdin closed.
            # Fall back to no line-watching; Ctrl-C in the main thread still works.
            return
        if done.is_set():
            return
        if not ready:
            continue
        line = sys.stdin.readline()
        if line == "":            # EOF on the input stream
            return
        word = line.strip().lower()
        if word in ("stop", "exit", "quit"):
            if word in ("exit", "quit"):
                exit_req.set()
            cancel.set()
            _server.kill_active_engines()
            print("[stopping — aborting the current run…]", file=sys.stderr)
            return


def _run_turn_cancellable(client, model: str, messages: List[Dict[str, Any]], *,
                          max_turns: int, cwd: str) -> str:
    """Run one turn, interruptible by `stop`/`exit`/`quit` (typed while it runs)
    or Ctrl-C. The turn runs on a background thread over a PRIVATE COPY of the
    conversation; the copy is committed back onto ``messages`` ONLY if the turn
    finished cleanly. On cancel/error nothing is committed, so an aborted turn can
    never leave a half-finished (dangling tool-call) history or race the main
    thread's list — the caller keeps its pre-turn ``messages`` untouched.

    Returns 'done' | 'stopped' | 'exit' | 'error:<msg>'."""
    global _ACTIVE_SPINNER
    work = list(messages)                 # private copy the worker mutates
    cancel = threading.Event()
    done = threading.Event()
    exit_req = threading.Event()
    result: Dict[str, Any] = {"status": "done", "error": None}

    # "working…" indicator so the user knows assay is busy (TTY only). _on_tool
    # pauses it around its prints; we stop it before printing the reply/status.
    spinner = _Spinner()
    _ACTIVE_SPINNER = spinner
    spinner.start()

    def _worker():
        try:
            agent.run_agent_turn(
                client, model, work,
                tools=agent.DEFAULT_TOOLS, max_turns=max_turns, cwd=cwd,
                on_tool=_on_tool, on_tool_start=_on_tool_start, should_cancel=cancel.is_set,
                enforce_summary=True,
            )
        except agent.RunCancelled:
            result["status"] = "stopped"
        except Exception as exc:  # noqa: BLE001 - reported to the caller cleanly
            result["status"] = "error"
            result["error"] = str(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    # Only watch stdin for a typed `stop` on an interactive TTY. With piped /
    # redirected input, every line is already queued and belongs to the REPL's
    # own input() — a watcher would race it and consume the user's NEXT request
    # as if it were a stop word. (Ctrl-C below still interrupts in both modes.)
    watcher = None
    if sys.stdin.isatty():
        watcher = threading.Thread(
            target=_stdin_stop_watch, args=(cancel, done, exit_req), daemon=True)
        watcher.start()

    # Block on the worker; Ctrl-C here also cancels (and hard-kills the engine).
    try:
        while not done.wait(timeout=0.2):
            pass
    except KeyboardInterrupt:
        cancel.set()
        _server.kill_active_engines()
        print("\n[stopping — aborting the current run…]", file=sys.stderr)

    # Let the worker unwind at its next checkpoint; a killed engine call returns
    # quickly and the cancel flag then ends the loop. It is a daemon thread, so
    # even a pathological hang cannot block process exit.
    worker.join(timeout=15)
    done.set()   # stop the watcher
    spinner.stop()
    _ACTIVE_SPINNER = None

    if exit_req.is_set():
        return "exit"
    # A clean finish wins even if a cancel arrived a hair too late: only commit
    # (and report) when the worker actually completed normally.
    if result["status"] == "done" and not worker.is_alive():
        messages[:] = work            # commit the completed turn
        _print_reply(messages)
        return "done"
    if result["status"] == "error":
        return f"error:{result['error']}"
    return "stopped"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="assay",
        description="Interactive computational-chemistry agent (assay).",
    )
    ap.add_argument("--base-url", default=_DEFAULT_BASE_URL,
                    help=f"OpenAI-compatible endpoint (default: {_DEFAULT_BASE_URL})")
    ap.add_argument("--model", default=_DEFAULT_MODEL,
                    help=f"model id to drive the agent (default: {_DEFAULT_MODEL})")
    ap.add_argument("--api-key", default=_DEFAULT_API_KEY,
                    help="API key (default: $CHEMKIT_LLM_API_KEY)")
    ap.add_argument("--prompt", default=None,
                    help="run this single request and exit (omit for an "
                         "interactive REPL)")
    ap.add_argument("--no-rules", action="store_true",
                    help="do not inject the calculation/research standards rules")
    ap.add_argument("--max-turns", type=int, default=12,
                    help="max model/tool round-trips per user turn (default: 12)")
    ap.add_argument("--no-banner", action="store_true",
                    help="suppress the ASSAY ASCII banner at REPL launch")
    args = ap.parse_args(argv)

    client = _make_client(args.base_url, args.api_key)
    _require_model(client, args.model, args.base_url)

    sys_prompt = agent.system_prompt(rules=[] if args.no_rules else None)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": sys_prompt}]
    cwd = os.getcwd()

    # ── one-shot ────────────────────────────────────────────────────────────
    if args.prompt is not None:
        messages.append({"role": "user", "content": args.prompt})
        try:
            _run_one(client, args.model, messages,
                     max_turns=args.max_turns, cwd=cwd)
        except Exception as exc:  # noqa: BLE001 - clean message, not a traceback
            name = type(exc).__name__.lower()
            if "connect" in name or "timeout" in name or "connection" in name:
                raise SystemExit(f"error: cannot reach the LLM endpoint at "
                                 f"{args.base_url!r} ({exc}).")
            raise SystemExit(f"error: agent request failed: {exc}")
        return 0

    # ── REPL ────────────────────────────────────────────────────────────────
    # Show the ASSAY banner only on an interactive terminal (not when piped) and
    # unless suppressed, so scripted/redirected use stays clean.
    if not args.no_banner and sys.stdin.isatty():
        print(_BANNER, file=sys.stderr)
    print(f"[endpoint {args.base_url} · model {args.model}]", file=sys.stderr)
    print("[while a run is going, type 'stop' (or Ctrl-C) to abort it · "
          "'exit'/'quit' or Ctrl-D to leave]", file=sys.stderr)
    while True:
        try:
            line = input("assay> ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            break
        line = line.strip()
        if not line:
            continue
        # Only these leave the REPL from an idle prompt. `stop` is NOT an
        # idle-prompt exit word — it only interrupts a RUNNING turn.
        if line.lower() in ("exit", "quit"):
            break
        # The turn runs on a private copy of `messages`, committed back only on a
        # clean finish (see _run_turn_cancellable), so an aborted turn leaves the
        # session history exactly as it was — no snapshot/rollback needed here.
        turn_messages = messages + [{"role": "user", "content": line}]
        status = _run_turn_cancellable(
            client, args.model, turn_messages, max_turns=args.max_turns, cwd=cwd)
        if status == "done":
            messages[:] = turn_messages      # commit the completed turn
        elif status == "exit":
            break                            # exit/quit typed mid-run
        elif status == "stopped":
            print("[stopped — run aborted; session unchanged]", file=sys.stderr)
        elif status.startswith("error:"):
            msg = status[len("error:"):]
            lname = msg.lower()
            if "connect" in lname or "timeout" in lname or "connection" in lname:
                print(f"[error: cannot reach the LLM endpoint at "
                      f"{args.base_url!r} ({msg})]", file=sys.stderr)
            else:
                print(f"[error: {msg}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
