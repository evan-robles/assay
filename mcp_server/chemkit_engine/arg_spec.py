"""Per-skill typed argument spec — the single source of truth for the MCP
server's per-tool signatures and the benchmark driver's tool schema.

Both the MCP server (``mcp_server/server.py``) and the fidelity benchmark driver
(``benchmarks/fidelity_driver.py``) used to hand-maintain their OWN copy of a
typed→argv converter, each flattening every skill to the same 8 common fields
(``xyz/method/charge/multiplicity/solvent/functional/basis/tier``) plus a
free-form ``extra_args`` escape hatch. That is exactly why agents fail on the
many-argument skills (redox, pka): the *required* skill-specific flags
(``--ox-charge``/``--red-charge``, ``--ha``/``--a-minus``) lived only in
``extra_args`` and were invisible to the model, while the wrapper injected
``xyz``/``--charge``/``--mult`` that those subcommands reject.

This module derives, for EACH subcommand, an ordered list of typed
:class:`Param` descriptors straight from the engine's argparse definitions (via
``chemkit_engine.cli.describe_subcommand``), so it can never drift from the CLI.
Callers use:

- :func:`skill_params` — the typed params for a subcommand (drives the MCP tool's
  synthesized signature and the driver's JSON schema).
- :func:`params_to_argv` — turn a dict of validated kwargs into the exact CLI
  token list the engine expects (the ONE converter both callers share).
- :func:`known_flags` — the set of valid ``--flags`` for a subcommand, for
  validating a slim ``extra_args`` escape hatch.

The typing rules:
- a positional argument (``input``/``smiles``/``name``) → a param whose CLI form
  is a bare token appended last;
- a store_true/store_false flag → a ``bool`` param (emitted as the flag when
  true / when it flips the default);
- an argparse ``choices=`` field (including the ``_norm_*`` normalizers, which
  all carry choices) → a string param carrying those choices as an ``enum``;
- an ``append`` action (``--monomer``/``--reactant``/``--product``) → a
  ``list`` param, emitted as the flag repeated once per element;
- everything else → an ``int`` / ``float`` / ``str`` scalar per its argparse
  ``type``.

Names are the argparse ``dest`` (already underscore-form, e.g. ``ox_charge``),
which is a valid Python identifier and never ``_``-prefixed — safe for a
synthesized function signature.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .cli import SUBCOMMAND_ALIASES, describe_subcommand, subcommand_names

# Parameters that are engine plumbing, not scientific inputs. They are handled
# out-of-band (the MCP layer sets --out/--stdout itself; cwd/args are wrapper
# concerns) and should NOT appear as typed tool params. Everything else — every
# real scientific knob — is exposed.
_PLUMBING = {"out", "stdout", "verbose"}

# Canonical scalar type names argparse hands us -> python types.
_SCALAR_TYPES = {"int": int, "float": float, "str": str}


@dataclass(frozen=True)
class Param:
    """One typed argument of a skill, derived from its argparse action."""
    name: str                      # argparse dest, e.g. "ox_charge" (python-safe)
    flag: Optional[str]            # "--ox-charge", or None for a positional
    py_type: Any                   # int / float / str / bool  (element type if is_list)
    default: Any                   # argparse default
    required: bool
    positional: bool
    is_bool: bool                  # store_true/store_false flag (no value)
    is_list: bool                  # append action -> list[py_type]
    choices: Optional[List[str]]   # enum choices (str), or None
    help: str

    @property
    def annotation_is_enum(self) -> bool:
        return bool(self.choices) and not self.is_bool


def _param_from_spec(s: Dict[str, Any]) -> Param:
    """Build a Param from one describe_subcommand() entry."""
    is_bool = s["type"] == "flag"
    is_list = bool(s.get("append"))
    if is_bool:
        py_type: Any = bool
    elif s.get("choices"):
        # _norm_* normalizers + explicit choices: the wire type is a string enum.
        py_type = str
    else:
        py_type = _SCALAR_TYPES.get(s["type"], str)
    return Param(
        name=s["name"],
        flag=s["flag"],
        py_type=py_type,
        default=s["default"],
        required=bool(s["required"]),
        positional=bool(s["positional"]),
        is_bool=is_bool,
        is_list=is_list,
        choices=[str(c) for c in s["choices"]] if s.get("choices") else None,
        help=s.get("help", ""),
    )


def _canonical(subcommand: str) -> str:
    """Resolve a tool/alias name to its canonical engine subcommand."""
    if subcommand in SUBCOMMAND_ALIASES:
        return subcommand
    for canon, aliases in SUBCOMMAND_ALIASES.items():
        if subcommand in aliases:
            return canon
    return subcommand  # already canonical (e.g. "sp", "pka") or unknown


def skill_params(subcommand: str, *, include_plumbing: bool = False) -> List[Param]:
    """Ordered typed params for a subcommand, positionals first.

    Excludes engine plumbing (--out/--stdout/--verbose) unless requested. The
    order is: positionals, then the skill's flags in argparse declaration order,
    which keeps required scientific inputs (e.g. --ha/--a-minus) near the top.
    """
    canon = _canonical(subcommand)
    spec = describe_subcommand(canon)
    params: List[Param] = []
    seen: set[str] = set()
    for s in spec:
        if not include_plumbing and s["name"] in _PLUMBING:
            continue
        # A --flag/--no-flag pair (e.g. transition-state --verify-freq /
        # --no-verify-freq, or --no-preopt) shares one argparse dest and so
        # appears twice. Collapse to a single bool param (keep the first entry;
        # the CLI accepts either spelling, params_to_argv emits when the value
        # flips the default). Dedupe by dest to keep the synthesized signature
        # free of duplicate parameter names.
        if s["name"] in seen:
            continue
        seen.add(s["name"])
        params.append(_param_from_spec(s))
    # Positionals first so a synthesized signature lists them before optionals
    # (and so required-before-optional is naturally satisfied for the SDK).
    params.sort(key=lambda p: (not p.positional, not p.required))
    return params


def known_flags(subcommand: str) -> set[str]:
    """All valid --flags for a subcommand (every option string, incl. aliases
    like --mult/--multiplicity and --no-gate). Used to validate extra_args."""
    canon = _canonical(subcommand)
    import argparse as _ap
    from .cli import build_parser
    parser = build_parser()
    sub = None
    for action in parser._actions:
        if isinstance(action, _ap._SubParsersAction):
            sub = action.choices.get(canon)
            break
    flags: set[str] = set()
    if sub is not None:
        for a in sub._actions:
            flags.update(a.option_strings)
    return flags


# Solvent synonyms that mean "gas phase" -> omit --solvent entirely. Mirrors the
# historical behavior of both _typed_args_to_argv copies this module replaces.
_GAS_SYNONYMS = {"none", "gas", "gas phase", "gas-phase", "vacuum", ""}


def params_to_argv(subcommand: str, values: Dict[str, Any],
                   *, extra_args: Optional[List[str]] = None) -> List[str]:
    """Turn a dict of {param_name: value} into the engine CLI token list.

    This is the ONE converter shared by the MCP server and the benchmark driver.
    Only params that exist for this subcommand are emitted; a value for a param
    the skill does not have is ignored (so nothing gets injected that the
    subcommand would reject — the core fix). Positionals are appended last.
    ``extra_args`` (already-validated raw tokens) are appended before the
    positional, matching the historical ordering.
    """
    params = {p.name: p for p in skill_params(subcommand)}
    argv: List[str] = []
    positionals: List[str] = []

    for name, p in params.items():
        if name not in values:
            continue
        val = values[name]
        if val is None:
            continue
        if p.positional:
            positionals.append(str(val))
            continue
        if p.is_bool:
            # Emit the flag only when the value flips the argparse default.
            if bool(val) != bool(p.default):
                argv.append(p.flag)  # type: ignore[arg-type]
            continue
        if p.is_list:
            items = val if isinstance(val, (list, tuple)) else [val]
            for item in items:
                if item is None:
                    continue
                argv += [p.flag, str(item)]  # type: ignore[list-item]
            continue
        # Scalar. Special-case --solvent: gas-phase synonyms are dropped so
        # "gas phase" means "omit the flag" (the engine's gas-phase default).
        if p.name == "solvent" and str(val).strip().lower() in _GAS_SYNONYMS:
            continue
        argv += [p.flag, str(val)]  # type: ignore[list-item]

    if extra_args:
        argv += [str(a) for a in extra_args]
    argv += positionals
    return argv


def all_subcommands() -> List[str]:
    """Canonical subcommand names (for iterating over every skill)."""
    return list(subcommand_names())
