"""Live-`.out` logging + subprocess orchestration for a single skill run.

Lifted from `mcp_server/server.py::_run_engine` so that BOTH the MCP server and a
stand-alone skill/CLI run get the same guarantees (preservation matrix #10):

- a **live `.out` log** the user can `tail -f` while the calculation runs, with
  its path announced AT LAUNCH (calc-reporting non-negotiable #9), written in the
  CALLER's cwd next to their inputs/outputs;
- optional **remote execution** over ssh via `CHEMKIT_REMOTE_HOST` (login vs.
  compute node on clusters), assuming a shared filesystem;
- **structured JSON error envelopes** (timeout, non-zero exit) that PRESERVE a
  structured integrity result when the child produced one, instead of discarding
  it for a truncated stub.

The child process itself owns the fd-1→fd-2 redirect that keeps its result JSON
clean (that lives in `argkit.run_cli` / the CLI `main`); this module is the
PARENT side that captures the child's stderr into the live log.
"""
from __future__ import annotations

import datetime
import json
import os
import shlex
import subprocess
import sys
from typing import Callable, List, Optional, Sequence

_TIMEOUT_S = 3600


def build_live_out_path(label: str, run_cwd: str,
                        stamp: Optional[str] = None) -> str:
    """`<label>_<YYYYmmdd-HHMMSS>.out` in run_cwd (the caller's cwd)."""
    stamp = stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(run_cwd, f"{label}_{stamp}.out")


def write_live_out_header(fh, *, label: str, args, command: str,
                          cwd: str, stamp: str) -> None:
    """Write the standard self-contained `.out` header block. Shared so the
    parent (run_skill_subprocess) and the in-process spine (argkit.run_cli) emit
    an identical header."""
    fh.write("# assay live log\n")
    fh.write(f"# subcommand : {label}\n")
    fh.write(f"# args       : {' '.join(map(str, args))}\n")
    fh.write(f"# command    : {command}\n")
    fh.write(f"# cwd        : {cwd}\n")
    fh.write(f"# started    : {stamp}\n")
    fh.write("# " + "=" * 60 + "\n")
    fh.flush()


def run_skill_subprocess(
    cmd: Sequence[str],
    *,
    label: str,
    args: Sequence[str],
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    default_cwd: Optional[str] = None,
    on_start: Optional[Callable[[subprocess.Popen], None]] = None,
    on_end: Optional[Callable[[subprocess.Popen], None]] = None,
) -> str:
    """Run `cmd` as an isolated subprocess with a live `.out` log; return its
    JSON stdout (augmented with `out_log`), or a JSON error envelope.

    Args:
      cmd: the full argv to execute (e.g. ['python','-m','assay_core.cli','sp',...]
        or ['python', '.../skills/<name>/scripts/run.py', ...]).
      label: prefix for the live `.out` file (typically the subcommand/skill).
      args: the skill args (for the header + error envelopes; display only).
      cwd: the CALLER's working directory; relative inputs/--out resolve here.
      env: environment for the child (PYTHONPATH etc.); defaults to os.environ.
      default_cwd: fallback cwd if `cwd` is missing/not a dir.
      on_start/on_end: optional Popen hooks (the server registers the live
        process so an interactive `stop` can SIGTERM it).
    """
    env = dict(env if env is not None else os.environ)
    run_cwd = cwd if (cwd and os.path.isdir(cwd)) else (default_cwd or os.getcwd())
    cmd = list(cmd)

    # --- Optional remote execution (CHEMKIT_REMOTE_HOST) -----------------------
    # On clusters the agent + server can run on a LOGIN node while the chemistry
    # must run on a COMPUTE node. If CHEMKIT_REMOTE_HOST is set, run the same cmd
    # on that host via ssh, reproducing cwd + PYTHONPATH. ASSUMES a shared
    # $HOME/filesystem so paths resolve identically on both sides; the result
    # JSON returns on ssh stdout and the live .out is written locally from the
    # tee'd stderr, so no file copy-back is needed.
    remote_host = os.environ.get("CHEMKIT_REMOTE_HOST", "").strip()
    if remote_host:
        remote_inner = "cd {cwd} && PYTHONPATH={pp} {run}".format(
            cwd=shlex.quote(run_cwd),
            pp=shlex.quote(env.get("PYTHONPATH", "")),
            run=" ".join(shlex.quote(c) for c in cmd),
        )
        ssh_opts = shlex.split(os.environ.get("CHEMKIT_REMOTE_SSH_OPTS", ""))
        cmd = ["ssh", *ssh_opts, remote_host, remote_inner]

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = build_live_out_path(label, run_cwd, stamp)

    def _augment(d: dict) -> dict:
        """Stamp the live-log path and, when the run executed on a remote compute
        node, the remote host — so the agent can see (and report) that the result
        came from e.g. an Aurora compute node, not the local machine."""
        d.setdefault("out_log", out_path)
        if remote_host:
            d.setdefault("remote_host", remote_host)
            ssh_opts_str = os.environ.get("CHEMKIT_REMOTE_SSH_OPTS", "").strip()
            if ssh_opts_str:
                d.setdefault("remote_ssh_opts", ssh_opts_str)
        return d

    def _write_header(fh):
        fh.write("# assay live log\n")
        fh.write(f"# subcommand : {label}\n")
        fh.write(f"# args       : {' '.join(map(str, args))}\n")
        fh.write(f"# command    : {' '.join(map(str, cmd))}\n")
        fh.write(f"# cwd        : {run_cwd}\n")
        fh.write(f"# started    : {stamp}\n")
        fh.write("# " + "=" * 60 + "\n")
        fh.flush()

    timed_out = False
    returncode = 0
    stdout_data = ""
    stderr_data = ""
    proc = None
    try:
        log_fh = open(out_path, "w", buffering=1, encoding="utf-8")
    except OSError:
        log_fh = None

    try:
        if log_fh is not None:
            _write_header(log_fh)
            proc = subprocess.Popen(
                cmd, cwd=run_cwd, env=env, text=True, bufsize=1,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if on_start is not None:
                on_start(proc)

            stderr_chunks: List[str] = []
            import threading

            def _pump_stderr():
                assert proc.stderr is not None
                for line in proc.stderr:
                    stderr_chunks.append(line)
                    log_fh.write(line)
                    log_fh.flush()

            t = threading.Thread(target=_pump_stderr, daemon=True)
            t.start()

            # Announce the live-log path AT LAUNCH (before the blocking read), so
            # the caller learns where to tail -f WHILE the calc is still running.
            sys.stderr.write(f"# assay live log: {out_path}\n")
            sys.stderr.flush()

            try:
                stdout_data = proc.stdout.read() if proc.stdout else ""
                proc.wait(timeout=_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                timed_out = True
            t.join(timeout=5)
            stderr_data = "".join(stderr_chunks)

            log_fh.write("\n# " + "=" * 60 + "\n")
            log_fh.write("# ===== RESULT JSON (stdout) =====\n")
            log_fh.write(stdout_data.strip() + "\n")
            log_fh.flush()
        else:
            try:
                proc_run = subprocess.run(
                    cmd, cwd=run_cwd, env=env,
                    capture_output=True, text=True, timeout=_TIMEOUT_S,
                )
                stdout_data, stderr_data = proc_run.stdout, proc_run.stderr
                returncode = proc_run.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        if on_end is not None and proc is not None:
            try:
                on_end(proc)
            except Exception:  # noqa: BLE001
                pass
        if log_fh is not None:
            log_fh.close()

    if timed_out:
        return json.dumps(_augment({"error": f"calculation timed out ({_TIMEOUT_S} s)",
                                    "subcommand": label, "args": list(args)}))

    if log_fh is not None and proc is not None:
        returncode = proc.returncode

    if returncode != 0:
        # An integrity hard-abort exits nonzero but STILL prints the full
        # structured result. Preserve that structured result (augmented with an
        # `error` key) instead of discarding it for a truncated stub, so the
        # integrity verdict/warnings/out-path stay reachable by the agent.
        parsed = None
        try:
            parsed = json.loads(stdout_data.strip())
        except ValueError:
            parsed = None
        is_integrity_result = isinstance(parsed, dict) and (
            isinstance(parsed.get("integrity"), dict)
            or ("trustworthy" in parsed and "status" in parsed)
        )
        if is_integrity_result:
            parsed["error"] = "integrity gate failed (result is not trustworthy)"
            parsed["returncode"] = returncode
            return json.dumps(_augment(parsed))
        return json.dumps(_augment({
            "error": "assay engine exited non-zero",
            "returncode": returncode,
            "subcommand": label, "args": list(args),
            "stderr": stderr_data.strip()[-4000:],
            "stdout": stdout_data.strip()[-2000:],
        }))

    # Success: inject the live-log path (and remote host, if any) so the caller
    # learns where to tail -f it and whether it ran remotely — the server's own
    # stderr does not reach a stdio MCP caller.
    out = stdout_data.strip()
    try:
        parsed = json.loads(out)
        if isinstance(parsed, dict):
            # Only stamp out_log when we actually wrote a live log locally.
            if log_fh is not None:
                parsed.setdefault("out_log", out_path)
            if remote_host:
                parsed.setdefault("remote_host", remote_host)
            return json.dumps(parsed)
        return out
    except ValueError:
        wrapped = {"raw_stdout": out, "stderr": stderr_data.strip()}
        if log_fh is not None:
            wrapped["out_log"] = out_path
        if remote_host:
            wrapped["remote_host"] = remote_host
        return json.dumps(wrapped)
