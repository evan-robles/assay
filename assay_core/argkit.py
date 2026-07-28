"""Shared argparse spine for assay skills and the engine CLI.

This module is the SINGLE home of the cross-cutting command-line machinery that
every skill must share so that 20 independent per-skill parsers cannot drift
(DESIGN.md §10.1). It holds:

- **Forgiving-input normalizers** (`_norm_method` / `_norm_tier` / `_norm_mode`
  / `_norm_redox_ref`) — argparse `type=` callables that map reasonable synonyms
  to the canonical token BEFORE `choices=` runs (#4 in the preservation matrix).
- **Shared option builders** (`_add_chem_options` / `_add_gate_option` /
  `_add_stdout_option`) — the `choices=`-guarded flags every calc skill exposes,
  so a skill composes them instead of re-listing choices (#3).
- **The level-of-theory gate** (`enforce_level_of_theory_gate`) — refuse a silent
  dft/hf default unless `--accept-defaults` (#2; calc-reporting non-negotiable
  #10).
- **Gas-phase synonym normalization** (`resolve_gas_phase_synonyms`).
- **The `--stdout` channel** (`_add_stdout_option` + `_compact_pointer`) and the
  `<stem>_<task>_<method>.json` default-out helper (#7, #13).
- **`run_cli(parser, run_fn)`** — the one mandatory `__main__` entrypoint every
  self-contained skill calls, wiring the fd-1→fd-2 redirect (#9), the gate (#2),
  the integrity catch + `--allow-unconverged` (#8), `input_configs.yaml`
  persistence (#12), and the stdout emit (#7). A skill is *physically unable* to
  bypass these because `run_cli` is its only `__main__` path.

`cli.py` imports these same names, so the monolithic engine CLI and each
stand-alone skill share ONE implementation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, List, Optional


# ── Forgiving-input normalizers (argparse `type=`) ────────────────────────────
# An agent may pass reasonable-but-non-canonical spellings of a choices-guarded
# flag (case variants, common synonyms). Without normalization, argparse rejects
# them with a hard error before the task runs — the same class of brittleness as
# the "--solvent gas" crash. These `type=` callables map synonyms/case to the
# canonical value BEFORE argparse's `choices=` check, so the whole engine accepts
# the obvious intent. Unknown values still fail `choices` with a clear message.
# Applied to --method, --tier, --ref, and the various --mode flags.

def _norm_method(v):
    """xtb/mopac/dft/hf, accepting case + common synonyms."""
    s = str(v).strip().lower().replace("_", "-")
    aliases = {
        "gfn2": "xtb", "gfn2-xtb": "xtb", "gfn2xtb": "xtb", "gfn-xtb": "xtb",
        "xtb": "xtb", "tblite": "xtb",
        "pm7": "mopac", "mopac": "mopac", "semiempirical": "mopac",
        "dft": "dft", "ks": "dft", "kohn-sham": "dft",
        "hf": "hf", "hartree-fock": "hf", "hartreefock": "hf", "scf": "hf",
    }
    return aliases.get(s, s)  # unknown -> pass through, choices= will reject it


def _norm_tier(v):
    """fast/standard/accurate, accepting case + synonyms."""
    s = str(v).strip().lower()
    aliases = {
        "fast": "fast", "quick": "fast", "cheap": "fast", "low": "fast",
        "standard": "standard", "default": "standard", "medium": "standard", "std": "standard",
        "accurate": "accurate", "high": "accurate", "best": "accurate", "tight": "accurate",
    }
    return aliases.get(s, s)


def _norm_redox_ref(v):
    """SHE / Ag/AgCl / Fc+/Fc reference electrode, accepting case + variants."""
    s = str(v).strip().lower().replace(" ", "").replace("_", "")
    aliases = {
        "she": "SHE", "nhe": "SHE", "standardhydrogen": "SHE",
        "ag/agcl": "Ag/AgCl", "agagcl": "Ag/AgCl", "agcl": "Ag/AgCl",
        "fc+/fc": "Fc+/Fc", "fc/fc+": "Fc+/Fc", "fcfc": "Fc+/Fc",
        "ferrocene": "Fc+/Fc", "fc": "Fc+/Fc",
    }
    return aliases.get(s, v)  # unknown -> original, choices= rejects it


def _norm_mode(v):
    """Generic --mode normalizer: case + common synonyms across tasks
    (adiabatic/vertical/freq, absolute/reference, sp/opt/freq). Maps to the
    canonical token; unknown values pass through to the per-task choices= check."""
    s = str(v).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    aliases = {
        # redox
        "adiabatic": "adiabatic", "adiab": "adiabatic", "relaxed": "adiabatic",
        "vertical": "vertical", "vert": "vertical",
        # pka
        "absolute": "absolute", "abs": "absolute", "direct": "absolute",
        "reference": "reference", "ref": "reference", "relative": "reference", "anchored": "reference",
        # reaction_energy
        "sp": "sp", "singlepoint": "sp", "single": "sp", "energy": "sp",
        "opt": "opt", "optimize": "opt", "optimise": "opt", "geometry": "opt",
        "freq": "freq", "frequency": "freq", "thermo": "freq", "thermochemistry": "freq",
    }
    return aliases.get(s, v)  # unknown -> original, choices= rejects it


# Gas-phase spellings an agent might pass to --solvent when it means "no solvent".
# Gas phase is expressed by OMITTING --solvent (None); normalizing these to None
# keeps a forgiving interpretation instead of an "unknown solvent 'gas'" crash.
_GAS_PHASE_SYNONYMS = {
    "gas", "gas phase", "gas-phase", "gasphase",
    "none", "vacuum", "vac", "no solvent", "no-solvent", "",
}


def resolve_gas_phase_synonyms(args) -> None:
    """Map a gas-phase spelling of --solvent to None, in place. No-op if the
    subcommand has no --solvent or it is already None/a real solvent."""
    if hasattr(args, "solvent") and isinstance(args.solvent, str):
        if args.solvent.strip().lower() in _GAS_PHASE_SYNONYMS:
            args.solvent = None


def _add_chem_options(p, *, with_input: bool = True, with_solvent: bool = True):
    """Shared CLI options. Set `with_solvent=False` for tasks where the
    solvent is fixed by the task itself (e.g. logp pins water + octanol)."""
    if with_input:
        p.add_argument("input", help="Path to input geometry (.xyz, .sdf, .pdb).")
    p.add_argument("--method", type=_norm_method, choices=["xtb", "mopac", "dft", "hf"], required=True)
    p.add_argument("--charge", type=int, default=0)
    p.add_argument("--mult", "--multiplicity", dest="multiplicity",
                   type=int, default=1, help="Spin multiplicity 2S+1 (default 1).")
    if with_solvent:
        p.add_argument("--solvent", default=None,
                       help="Implicit solvent: either a known name (e.g. water, "
                            "methanol, dmso, hexane) OR a numeric dielectric "
                            "constant (e.g. 2.0) for a custom solvent. Gas phase "
                            "if omitted. Note: xtb (ALPB) requires a named "
                            "solvent — a numeric dielectric needs --method "
                            "dft, hf, or mopac.")
        p.add_argument("--solvent-model", dest="solvent_model",
                       choices=["ddcosmo", "cpcm", "iefpcm"], default="ddcosmo",
                       help="PySCF continuum solvation model (DFT/HF only): "
                            "ddcosmo (default, domain-decomposition COSMO), cpcm "
                            "(C-PCM), or iefpcm (IEF-PCM). MOPAC uses COSMO and "
                            "xtb uses ALPB regardless; a non-default value with "
                            "those methods (and a solvent set) is an error.")
    # PySCF-only knobs; silently ignored for xtb/mopac.
    p.add_argument("--tier", type=_norm_tier, choices=["fast", "standard", "accurate"], default=None,
                   help="DFT tier preset (fast=r2SCAN/def2-SVP, standard=B3LYP/def2-TZVP, "
                        "accurate=wB97M-V/def2-QZVPP). Ignored unless --method dft.")
    p.add_argument("--functional", default=None,
                   help="DFT functional override, libxc name (e.g. b3lyp, pbe0, wb97x_v, "
                        "wb97m_v, wb97x-d3bj). Ignored unless --method dft.")
    p.add_argument("--basis", default=None,
                   help="Basis-set override for DFT/HF (e.g. def2-tzvp, cc-pvtz). "
                        "Ignored unless --method dft or --method hf.")
    p.add_argument("--density-fit", dest="density_fit", action="store_true",
                   default=False,
                   help="Enable density fitting (the RI/resolution-of-identity "
                        "approximation to the two-electron integrals) for DFT/HF: "
                        "~3-10x faster SCF for a ~0.1-0.8 mEh error. OFF BY "
                        "DEFAULT — assay uses exact four-center integrals "
                        "(plain RKS/UKS/RHF/UHF, matching a hand-written PySCF "
                        "run); pass this flag to opt into the RI speedup. "
                        "Ignored for xtb/mopac.")
    p.add_argument("--out", default=None,
                   help="Output JSON path. Default: <input-stem>_<task>_<method>.json")
    p.add_argument("--accept-defaults", dest="accept_defaults", action="store_true",
                   help="Explicitly consent to assay's silent defaults for "
                        "consequential knobs the user did not set (DFT "
                        "tier=standard -> B3LYP/def2-TZVP; HF basis=def2-tzvp; "
                        "gas phase when no --solvent). Without this flag, a DFT/HF "
                        "run that omits those knobs is REFUSED so the level of "
                        "theory is never chosen silently "
                        "(calculation-reporting-standards non-negotiable #10).")
    _add_stdout_option(p)
    _add_gate_option(p)
    p.add_argument("--verbose", type=int, default=4,
                   help="PySCF log verbosity (0=silent .. 4=default rich SCF/opt "
                        "detail .. 5=debug). Streamed to the live .out log; "
                        "ignored for xtb/mopac.")


def _add_gate_option(p):
    """Add the integrity-gate escape hatch to a subparser.

    By default a result that fails its computation-side integrity checks
    (non-converged SCF/opt, wrong imaginary-mode count, charge mismatch, …)
    hard-aborts: the partial result is still written to --out (evidence
    preserved) but the CLI exits nonzero and the headline number is marked
    untrustworthy. --allow-unconverged downgrades that abort to a stamped
    warning and exits 0, for the legitimate 'inspect the failed geometry'
    workflow. The number is still flagged trustworthy=false."""
    p.add_argument(
        "--allow-unconverged", "--no-gate", dest="allow_unconverged",
        action="store_true", default=False,
        help="Downgrade the integrity hard-abort to a stamped warning and exit "
             "0 (result marked status=warning, trustworthy=false, "
             "gate_bypassed=true). Use ONLY to inspect a failed geometry "
             "(collapsed TS, non-converged opt) — the headline number is NOT "
             "trustworthy.",
    )


def _add_stdout_option(p):
    """Add the --stdout channel selector to a subparser.

    Controls what the CLI prints to fd 1 (which the MCP server / skill client
    captures into the agent's context). The result JSON is ALWAYS written to
    the --out file regardless of this flag; --stdout only governs the stdout
    copy, so choosing 'path' avoids re-ingesting the full blob into context
    (calculation-reporting-standards §9.1).

      json  full indented result JSON on stdout (legacy default; verbose)
      path  a compact one-line pointer ({"out":...,"converged":...,"warnings":[...]})
      none  nothing on stdout (the file is still written; paths still go to stderr)
    """
    p.add_argument(
        "--stdout", choices=["json", "path", "none"], default="json",
        help="What to print on stdout. 'json' = full result (default), "
             "'path' = compact one-line pointer to the --out file plus "
             "convergence/warnings, 'none' = silent. The --out JSON file is "
             "always written either way. Use 'path' to keep the full blob out "
             "of an agent's context and read back fields with jq.",
    )


def _compact_pointer(result: dict, out_path: str) -> str:
    """Build the one-line stdout summary for --stdout path.

    Surfaces only the fields an agent needs to decide what to read back with
    jq: where the file is, whether it converged, and any warnings (which must
    NEVER be dropped, per calculation-reporting-standards §7/§9.1). Everything
    else stays in the on-disk JSON.
    """
    summary: dict = {"out": out_path}
    # Integrity verdict first — the headline "is this number safe to quote" bit.
    integ = result.get("integrity")
    if isinstance(integ, dict):
        summary["status"] = integ.get("status")
        summary["trustworthy"] = integ.get("trustworthy")
        failed = [c.get("name") for c in integ.get("checks", []) if not c.get("ok")]
        if failed:
            summary["failed_checks"] = failed
    # Convergence may live under different keys depending on the task.
    for key in ("converged", "scf_converged"):
        if key in result:
            summary[key] = result[key]
            break
    cs = result.get("code_specific")
    if "converged" not in summary and "scf_converged" not in summary and isinstance(cs, dict):
        if "scf_converged" in cs:
            summary["scf_converged"] = cs["scf_converged"]
    # Always carry warnings through so a caveat is never silently lost.
    warnings = result.get("warnings")
    if warnings:
        summary["warnings"] = warnings
        # Also carry the copy-ready warnings_block (a single verbatim,
        # "surface these to the user" string) so an agent reading only the
        # compact pointer can relay all warnings in one paste — the affordance
        # that keeps weak models from dropping them. Derived at write_result
        # time; recomputed here from `warnings` if this result predates it.
        block = result.get("warnings_block")
        if not block:
            from .io import _warnings_block
            block = _warnings_block(warnings)
        if block:
            summary["warnings_block"] = block
    # A couple of cheap, near-universal headline numbers when present, so the
    # pointer is informative without the agent needing a second read.
    for key in ("total_energy_eV",):
        if key in result:
            summary[key] = result[key]
    return json.dumps(summary, default=str)


def _default_out(input_path: str, task: str, method: str) -> str:
    """The `<input-stem>_<task>_<method>.json` default result path (#13)."""
    stem = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.abspath(f"{stem}_{task}_{method}.json")


def _split_combined_flag_tokens(argv):
    """Split a single '--flag value' token into ['--flag', 'value'].

    A model calling the CLI (esp. via the subprocess path) sometimes emits a
    combined ['--method dft', ...] element; argparse would treat the whole thing
    as one flag name and fail. Splitting on the first space repairs the obvious
    intent while leaving normal tokens untouched.
    """
    if argv is None:
        return None
    out = []
    for tok in argv:
        if isinstance(tok, str) and tok.startswith("--") and " " in tok:
            flag, _, val = tok.partition(" ")
            out.append(flag)
            if val.strip():
                out.append(val.strip())
        else:
            out.append(tok)
    return out


def enforce_level_of_theory_gate(args, parser) -> None:
    """Refuse a dft/hf run that would SILENTLY choose the level of theory.

    Vendor-/harness-proof guard living in the engine (the Python every calc must
    pass through), so it protects calculations under ANY model or harness — not
    just those behind the Claude Code PreToolUse hook. When --method dft/hf is
    requested without the consequential knobs, refuse via parser.error unless the
    caller explicitly consented with --accept-defaults.
    (calculation-reporting-standards non-negotiable #10; preservation matrix #2.)
    """
    method = getattr(args, "method", None)
    accepted = getattr(args, "accept_defaults", False)
    if method not in ("dft", "hf") or accepted:
        return
    has_tier = getattr(args, "tier", None) is not None
    has_func = getattr(args, "functional", None) is not None
    has_basis = getattr(args, "basis", None) is not None
    if method == "dft" and not (has_tier or has_func or has_basis):
        parser.error(
            "--method dft was given without --tier/--functional/--basis. "
            "assay would SILENTLY default to tier=standard "
            "(B3LYP/def2-TZVP, density-fit) — do not choose the level of "
            "theory silently. Either pass an explicit --tier/--functional/"
            "--basis, or, only after confirming with the user, pass "
            "--accept-defaults to consciously accept tier=standard. "
            "(calculation-reporting-standards non-negotiable #10)"
        )
    if method == "hf" and not has_basis:
        parser.error(
            "--method hf was given without --basis. assay would SILENTLY "
            "default to basis=def2-tzvp — do not choose the level of theory "
            "silently. Either pass an explicit --basis, or, only after "
            "confirming with the user, pass --accept-defaults to consciously "
            "accept def2-tzvp. (calculation-reporting-standards "
            "non-negotiable #10)"
        )


def pyscf_kwargs_from_args(args) -> dict:
    """Collect the PySCF/gate knobs every task.run() accepts, from parsed args.

    Also sets CHEMKIT_PYSCF_VERBOSE (the calculator factory reads it) as a side
    effect, mirroring the engine CLI. getattr-guarded so non-QM subcommands that
    don't declare these knobs still work (None is the right 'no level of theory'
    default for them)."""
    os.environ["CHEMKIT_PYSCF_VERBOSE"] = str(getattr(args, "verbose", 4))
    return {
        "tier": getattr(args, "tier", None),
        "functional": getattr(args, "functional", None),
        "basis": getattr(args, "basis", None),
        "density_fit": getattr(args, "density_fit", False),
        "solvent_model": getattr(args, "solvent_model", "ddcosmo"),
        "allow_unconverged": getattr(args, "allow_unconverged", False),
    }


def run_cli(parser: argparse.ArgumentParser,
            run_fn: Callable[..., dict],
            *,
            task: str,
            argv: Optional[List[str]] = None,
            build_run_kwargs: Optional[Callable[[argparse.Namespace, str, dict], dict]] = None,
            emit_artifact_paths: Optional[Callable[[dict], None]] = None) -> int:
    """The mandatory `__main__` spine for a self-contained single-input skill.

    Every guardrail a stand-alone skill run must not bypass is wired here, in the
    same order and with the same behavior as the engine CLI's main():

      parse (choices #3 + normalizers #4 already applied via type=)
        -> gas-phase synonym normalize
        -> level-of-theory gate (#2)
        -> fd-1 -> fd-2 redirect (#9, protects the result JSON from backend banners)
        -> call run_fn under the integrity catch (#8, --allow-unconverged, --out on failure)
        -> input_configs.yaml persistence (#12)
        -> restore stdout, emit per --stdout mode (#7) + artifact paths to stderr
        -> exit nonzero iff the integrity gate hard-aborted (result still written)

    Args:
      parser: the skill's build_parser() (must compose the shared option builders).
      run_fn: the skill's typed run(); called with (input_path, **kwargs).
      task: the task id used for the default --out name and integrity naming.
      build_run_kwargs: optional (args, cli, pyscf_kwargs) -> kwargs mapper for
        skills whose run() takes extra positional/keyword args beyond the common
        set. Defaults to the single-input calc convention.
      emit_artifact_paths: optional (result) -> None hook to print extra artifact
        paths (plots, cubes, trajectories) to stderr, mirroring main()'s per-task
        stderr lines.
    """
    from .io import write_result, cli_invocation
    from .integrity import IntegrityError
    from . import ledger

    argv = _split_combined_flag_tokens(argv if argv is not None else sys.argv[1:])
    parser._chemkit_argv = argv  # type: ignore[attr-defined]
    args = parser.parse_args(argv)

    resolve_gas_phase_synonyms(args)
    enforce_level_of_theory_gate(args, parser)

    cli = cli_invocation()
    pyscf_kwargs = pyscf_kwargs_from_args(args)

    if build_run_kwargs is not None:
        run_kwargs = build_run_kwargs(args, cli, pyscf_kwargs)
        input_path = getattr(args, "input", None)
    else:
        input_path = args.input
        run_kwargs = dict(
            method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            cli=cli, **pyscf_kwargs,
        )

    out_path = args.out or _default_out(input_path, task, getattr(args, "method", "na"))

    # Protect the result JSON: redirect fd 1 -> fd 2 for the whole calculation so
    # stray backend banners (MOPAC/PySCF) land on stderr (the live .out), then
    # restore the real stdout just before printing the JSON. fd-level dup2 is
    # required because child processes inherit fd 1, not Python's sys.stdout.
    _real_stdout_fd = os.dup(1)
    os.dup2(2, 1)

    integrity_failed = False
    try:
        result = run_fn(input_path, **run_kwargs)
        write_result(result, out_path)
    except IntegrityError as e:
        result = e.result                 # the stamped partial result
        write_result(result, out_path)    # EVIDENCE PRESERVED on disk
        integrity_failed = True

    # Parameter persistence (skill-standards): full effective params next to --out.
    try:
        ledger.write_input_configs(args, out_path, task=task)
    except Exception:  # noqa: BLE001 - persistence must never fail the calc
        pass

    sys.stdout.flush()
    os.dup2(_real_stdout_fd, 1)
    os.close(_real_stdout_fd)

    stdout_mode = getattr(args, "stdout", "json")
    if stdout_mode == "json":
        print(json.dumps(result, indent=2, default=str))
    elif stdout_mode == "path":
        print(_compact_pointer(result, out_path))
    print(f"\n# result written to: {out_path}", file=sys.stderr)
    if emit_artifact_paths is not None:
        try:
            emit_artifact_paths(result)
        except Exception:  # noqa: BLE001
            pass
    return 1 if integrity_failed else 0
