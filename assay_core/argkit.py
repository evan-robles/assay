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

      json  full indented result JSON on stdout (default)
      path  a compact one-line pointer ({"out":...,"converged":...,"warnings":[...]})
      none  nothing on stdout (the file is still written; paths still go to stderr)
    """
    p.add_argument(
        "--outdir", default=None, metavar="DIR",
        help="Directory to write EVERY artifact of this run into (result JSON, "
             "live .out log, input_configs.yaml, and all sidecars: optimized "
             ".xyz, .molden, .cube, trajectories, plots). Created if missing. "
             "Default: the current working directory (unchanged behavior). "
             "Input geometry paths are resolved against your ORIGINAL cwd, so "
             "`--outdir runs/a mol.xyz` reads ./mol.xyz and writes runs/a/*.",
    )
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
    # Both spellings during the assay -> ASSAY rename window, so a child
    # process reading either one sees the requested verbosity (see env.py).
    os.environ["ASSAY_PYSCF_VERBOSE"] = str(getattr(args, "verbose", 4))
    os.environ["CHEMKIT_PYSCF_VERBOSE"] = str(getattr(args, "verbose", 4))
    return {
        "tier": getattr(args, "tier", None),
        "functional": getattr(args, "functional", None),
        "basis": getattr(args, "basis", None),
        "density_fit": getattr(args, "density_fit", False),
        "solvent_model": getattr(args, "solvent_model", "ddcosmo"),
        "allow_unconverged": getattr(args, "allow_unconverged", False),
    }


def _absolutize_path_args(args, base: str) -> None:
    """Rewrite every args value that names an EXISTING file/dir into an absolute
    path, resolved against `base` (the caller's original cwd).

    This runs immediately before `run_cli` chdirs into --outdir. Skills name
    their inputs with many different flags (`input`, `--monomer`, `--ha`,
    `--a-minus`, `--reactant`, `--product`, `--ts-guess`, ...), so rather than
    maintain a list that silently rots when a skill adds one, we rewrite any
    string (or list-of-strings) that actually resolves on disk. A value that is
    NOT an existing path — a SMILES, a molecule name, a solvent, an unwritten
    --out — is left untouched, so it stays relative to the new cwd (the outdir),
    which is what an output path should do.
    """
    def _looks_like_path(v: str) -> bool:
        """Path-shaped: has a separator or a file extension.

        This guard matters for the SMILES/name skills: `build-from-smiles O`
        passes the SMILES for water, and a stray file named `O` in the cwd must
        NOT turn that argument into a path. Real geometry inputs always carry an
        extension (.xyz/.sdf/.pdb/.mol) or a directory component, so requiring
        one separates inputs from chemistry strings cleanly. An extensionless
        geometry file used with --outdir fails loudly (file not found) rather
        than silently reinterpreting a SMILES.
        """
        seps = [c for c in (os.sep, os.altsep) if c]
        return any(c in v for c in seps) or bool(os.path.splitext(v)[1])

    def _resolved(v: str):
        if not v or not _looks_like_path(v):
            return None
        cand = v if os.path.isabs(v) else os.path.join(base, v)
        return os.path.abspath(cand) if os.path.exists(cand) else None

    for name, value in list(vars(args).items()):
        if name == "outdir":
            continue
        if isinstance(value, str):
            got = _resolved(value)
            if got:
                setattr(args, name, got)
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            setattr(args, name, [(_resolved(v) or v) for v in value])
    # An explicit --out is an OUTPUT path and will not exist yet, so the loop
    # above leaves it relative. Anchor it to the original cwd: a user who typed
    # `--out results.json` means "next to me", not "inside the outdir".
    out = getattr(args, "out", None)
    if isinstance(out, str) and out and not os.path.isabs(out):
        setattr(args, "out", os.path.abspath(os.path.join(base, out)))


def run_cli(parser: argparse.ArgumentParser,
            run_fn: Callable[..., dict],
            *,
            task: str,
            argv: Optional[List[str]] = None,
            call_run: Optional[Callable[[argparse.Namespace, str, dict], dict]] = None,
            build_run_kwargs: Optional[Callable[[argparse.Namespace, str, dict], dict]] = None,
            resolve_out: Optional[Callable[[argparse.Namespace, dict], str]] = None,
            post_write: Optional[Callable[[argparse.Namespace, dict, str], None]] = None,
            emit_artifact_paths: Optional[Callable[[dict], None]] = None) -> int:
    """The mandatory `__main__` spine for a self-contained skill.

    Every guardrail a stand-alone skill run must not bypass is wired here, in the
    same order and with the same behavior as the engine CLI's main():

      parse (choices #3 + normalizers #4 already applied via type=)
        -> gas-phase synonym normalize
        -> level-of-theory gate (#2)
        -> fd-1 -> fd-2 redirect + live .out (#9/#10, protects the result JSON)
        -> call run_fn under the integrity catch (#8, --allow-unconverged, --out on failure)
        -> input_configs.yaml persistence (#12)
        -> restore stdout, emit per --stdout mode (#7) + artifact paths to stderr
        -> exit nonzero iff the integrity gate hard-aborted (result still written)

    Args:
      parser: the skill's build_parser() (must compose the shared option builders).
      run_fn: the skill's typed run().
      task: the task id used for the default --out name and integrity naming.
      call_run: optional (args, cli, pyscf_kwargs) -> result. Full control of HOW
        run_fn is invoked, for skills whose run() does not take a leading
        input_path (e.g. build takes molecule=, resolve takes name=). Overrides
        build_run_kwargs. Defaults to the single-input calc convention:
        run_fn(args.input, method=..., charge=..., ..., **pyscf_kwargs).
      build_run_kwargs: optional (args, cli, pyscf_kwargs) -> kwargs for the
        single-input convention (run_fn(args.input, **kwargs)) when a skill just
        needs a few extra keyword args (e.g. opt's fmax/steps).
      resolve_out: optional (args, result) -> out path, for skills with bespoke
        naming (build, resolve). Defaults to args.out or <stem>_<task>_<method>.json.
      post_write: optional (args, result, out_path) -> None, run AFTER the result
        JSON is written, for extra artifacts (e.g. confsearch's ensemble xyz). May
        mutate result + is expected to re-write it if so.
      emit_artifact_paths: optional (result) -> None hook to print extra artifact
        paths (plots, cubes, trajectories) to stderr, mirroring main()'s per-task
        stderr lines.
    """
    from .io import write_result, cli_invocation
    from .integrity import IntegrityError
    from . import ledger

    argv = _split_combined_flag_tokens(argv if argv is not None else sys.argv[1:])

    # Discovery: `run.py --help-json` — machine-readable arg spec for THIS skill,
    # handled BEFORE parse_args (which would otherwise reject the missing required
    # positional/--method). Mirrors the engine dispatcher's --help-json branch so
    # the `skill_help` tool and a stand-alone `python run.py --help-json` return
    # the same spec instead of an argparse exit-2.
    if argv and "--help-json" in argv:
        from .cli import describe_parser
        sys.stdout.write(json.dumps(
            {"subcommand": task, "arguments": describe_parser(parser)},
            indent=2) + "\n")
        return 0

    parser._assay_argv = argv  # type: ignore[attr-defined]
    args = parser.parse_args(argv)

    resolve_gas_phase_synonyms(args)
    enforce_level_of_theory_gate(args, parser)

    cli = cli_invocation()
    pyscf_kwargs = pyscf_kwargs_from_args(args)

    # Decide how run_fn is invoked. `call_run` gives full control (build/resolve);
    # otherwise use the single-input convention with optional extra kwargs.
    if call_run is None:
        if build_run_kwargs is not None:
            _run_kwargs = build_run_kwargs(args, cli, pyscf_kwargs)
        else:
            _run_kwargs = dict(
                method=args.method, charge=args.charge,
                multiplicity=args.multiplicity, solvent=args.solvent,
                cli=cli, **pyscf_kwargs,
            )

        def call_run(a, c, pk):  # noqa: ARG001 - uniform signature
            return run_fn(a.input, **_run_kwargs)

    from . import runlog
    import datetime

    # --outdir: one directory for EVERY artifact this run produces. Artifact
    # paths are derived in two unrelated ways across the skills -- some off the
    # result JSON path (_default_out), others straight off the input basename
    # (geometry_optimize's out_xyz, reaction_profile's diagram .png) -- so there
    # is no single path chokepoint to thread a directory through. Changing the
    # process cwd is the one lever that catches all of them, including any a
    # future skill adds. Inputs are absolutized against the ORIGINAL cwd first
    # so relative geometry paths still resolve the way the user typed them.
    caller_cwd = os.getcwd()
    outdir = getattr(args, "outdir", None)
    if outdir:
        outdir = os.path.abspath(os.path.join(caller_cwd, outdir))
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as e:
            print(f"{parser.prog or task}: error: cannot create --outdir "
                  f"{outdir!r}: {e}", file=sys.stderr)
            return 1
        _absolutize_path_args(args, caller_cwd)
        os.chdir(outdir)

    run_cwd = os.getcwd()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    live_out = runlog.build_live_out_path(task, run_cwd, stamp)

    # When the server/CLI front door runs this skill AS A SUBPROCESS, the PARENT
    # (runlog.run_skill_subprocess) already tees a live `.out`; the child must not
    # write a SECOND one. The parent sets ASSAY_SUPPRESS_LIVE_OUT=1 for that case.
    _suppress_live_out = os.environ.get("ASSAY_SUPPRESS_LIVE_OUT") == "1"

    # Live `.out` log the user can `tail -f` while the calc runs — the same
    # artifact the server path produces (calc-reporting non-negotiable #9/#10), so
    # a stand-alone `python run.py` run is equally observable. We tee ALL calc
    # output (stdout + stderr, incl. MOPAC/PySCF banners) into it at the fd level:
    #   - save the real stdout (fd 1) and real stderr (fd 2);
    #   - point BOTH fd 1 and fd 2 at the log for the calc (this also gives #9 —
    #     stray banners on fd 1 can't corrupt the result JSON, which is printed
    #     only AFTER fd 1 is restored);
    #   - restore both afterward; append the result banner; emit the JSON.
    _real_stdout_fd = os.dup(1)
    _real_stderr_fd = os.dup(2)
    log_fh = None
    if not _suppress_live_out:
        try:
            log_fh = open(live_out, "w", buffering=1, encoding="utf-8")
        except OSError:
            log_fh = None

    if log_fh is not None:
        runlog.write_live_out_header(
            log_fh, label=task, args=(argv or []),
            command=" ".join([sys.executable, *sys.argv]),
            cwd=run_cwd, stamp=stamp,
        )
        # Announce the path on the REAL stderr, at launch, before the calc blocks.
        os.write(_real_stderr_fd, f"# assay live log: {live_out}\n".encode())
        os.dup2(log_fh.fileno(), 1)
        os.dup2(log_fh.fileno(), 2)
    else:
        # No log (e.g. read-only cwd): still protect the result JSON via fd1->fd2.
        os.dup2(2, 1)

    def _resolve_out(result: dict) -> str:
        if resolve_out is not None:
            return resolve_out(args, result)
        if getattr(args, "out", None):
            return args.out
        return _default_out(args.input, task, getattr(args, "method", "na"))

    def _restore_fds():
        try:
            sys.stdout.flush(); sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os.dup2(_real_stdout_fd, 1)
        os.dup2(_real_stderr_fd, 2)

    integrity_failed = False
    try:
        try:
            result = call_run(args, cli, pyscf_kwargs)
            out_path = _resolve_out(result)
            write_result(result, out_path)
        except IntegrityError as e:
            result = e.result                 # the stamped partial result
            out_path = _resolve_out(result)
            write_result(result, out_path)    # EVIDENCE PRESERVED on disk
            integrity_failed = True

        # Optional post-write artifacts (e.g. confsearch ensemble xyz); may
        # mutate + re-write result.
        if post_write is not None:
            try:
                post_write(args, result, out_path)
            except Exception:  # noqa: BLE001 - side-writes must not fail the calc
                pass

        # Parameter persistence (skill-standards): full effective params by --out.
        try:
            ledger.write_input_configs(args, out_path, task=task)
        except Exception:  # noqa: BLE001 - persistence must never fail the calc
            pass
    except Exception as exc:  # noqa: BLE001
        # A NON-integrity error from run() (e.g. invalid SMILES, missing binary,
        # unreadable geometry): restore the real fds so the message reaches the
        # caller's stderr (not just the .out log), record it in the log, then
        # exit nonzero — matching the pre-inversion CLI behavior.
        _restore_fds()
        os.close(_real_stdout_fd); os.close(_real_stderr_fd)
        if log_fh is not None:
            try:
                log_fh.write(f"\n# ERROR: {type(exc).__name__}: {exc}\n")
                log_fh.flush(); log_fh.close()
            except Exception:  # noqa: BLE001
                pass
        print(f"{parser.prog or task}: error: {exc}", file=sys.stderr)
        if log_fh is not None:
            print(f"# assay live log: {live_out}", file=sys.stderr)
        return 1

    _restore_fds()
    os.close(_real_stdout_fd)
    os.close(_real_stderr_fd)

    # Make the .out self-contained: append the result JSON under a banner.
    if log_fh is not None:
        try:
            log_fh.write("\n# " + "=" * 60 + "\n")
            log_fh.write("# ===== RESULT JSON (stdout) =====\n")
            log_fh.write(json.dumps(result, default=str) + "\n")
            log_fh.flush()
            log_fh.close()
        except Exception:  # noqa: BLE001
            pass

    stdout_mode = getattr(args, "stdout", "json")
    if stdout_mode == "json":
        print(json.dumps(result, indent=2, default=str))
    elif stdout_mode == "path":
        print(_compact_pointer(result, out_path))
    print(f"\n# result written to: {out_path}", file=sys.stderr)
    if log_fh is not None:
        print(f"# assay live log: {live_out}", file=sys.stderr)
    if emit_artifact_paths is not None:
        try:
            emit_artifact_paths(result)
        except Exception:  # noqa: BLE001
            pass
    return 1 if integrity_failed else 0
