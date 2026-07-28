#!/usr/bin/env python3
"""name-to-smiles — self-contained assay skill.

Resolve a plain molecule name to a SMILES string from online sources (PubChem ->
OPSIN -> NIST WebBook), reporting the answering source and an ACS citation. Pure
lookup — no 3D geometry, no level of theory. Runnable stand-alone:

    python skills/name_to_smiles/scripts/run.py caffeine
"""
from __future__ import annotations

# Skill discovery manifest (read by assay_core.discovery / the MCP server).
SKILL_NAME = "name-to-smiles"      # kebab display name (matches SKILL.md frontmatter)
SUBCOMMAND = "resolve"      # engine subcommand this skill implements
import argparse
import os
import re as _re

from typing import Any, Dict

from assay_core.integrity import finalize


def run(
    name: str,
    *,
    cli: str = "",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Resolve a molecule name to a SMILES string and report its provenance.

    Args:
      name: a plain molecule name (common, trade, or systematic IUPAC). It is
        resolved online via PubChem -> OPSIN -> NIST WebBook; the first source
        that answers wins and is recorded with an ACS-format citation.
      cli: the literal CLI invocation, echoed into the result for reproducibility.
      gate_integrity / allow_unconverged: passed through to ``finalize`` for a
        uniform end-of-run seam. (This task has no SCF/convergence to gate; the
        gate simply stamps an integrity block.)

    Returns a result dict carrying the resolved ``smiles`` and the full
    ``smiles_source`` provenance (source label, stereochemistry flavor, ACS
    citation, source URL, identifier such as a PubChem CID, cache flag, and any
    resolver warnings).

    Raises:
      LookupError: if every source fails to resolve the name. The CLI/MCP layer
        turns this into a normal error response (same path as ``build``).
    """
    name = name.strip()

    # Full PubChem -> OPSIN -> NIST chain (cached, fail-soft per source). Raises
    # LookupError only if all three fail; the caller surfaces that as an error.
    from assay_core.resolve import resolve_name_to_smiles
    resolution = resolve_name_to_smiles(name)
    src = resolution.as_dict()

    result: Dict[str, Any] = {
        "task": "name_to_smiles",
        # `program` records which database/parser actually answered (PubChem /
        # OPSIN / NIST WebBook), mirroring how other tasks name their backend.
        "program": resolution.source,
        "input": name,
        "smiles": resolution.smiles,
        # Full provenance: source, smiles_kind (isomeric/connectivity/
        # unspecified), citation (ACS), url, identifier (e.g. "CID 2519"),
        # from_cache, warnings.
        "smiles_source": src,
        "cli_invocation": cli,
        "warnings": list(src.get("warnings") or []),
    }
    # Drop an empty warnings list so a clean result stays clean (matches build).
    if not result["warnings"]:
        del result["warnings"]

    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def build_parser() -> argparse.ArgumentParser:
    from assay_core import argkit
    p = argparse.ArgumentParser(
        prog="name-to-smiles",
        description="Resolve a molecule NAME to a SMILES string (online lookup).",
    )
    p.add_argument("name",
                   help="Plain molecule name, e.g. 'caffeine', 'L-alanine', or a "
                        "systematic IUPAC name.")
    p.add_argument("--out", default=None, help="Result JSON path.")
    argkit._add_stdout_option(p)
    argkit._add_gate_option(p)
    return p


def _call_run(args, cli, pyscf_kwargs):
    return run(name=args.name, cli=cli,
               allow_unconverged=pyscf_kwargs.get("allow_unconverged", False))


def _resolve_out(args, result):
    if getattr(args, "out", None):
        return args.out
    safe = _re.sub(r"[^A-Za-z0-9_-]", "_", str(getattr(args, "name", "molecule")))[:60] or "molecule"
    return os.path.abspath(f"{safe}_smiles.json")


if __name__ == "__main__":
    from assay_core import argkit
    raise SystemExit(argkit.run_cli(build_parser(), run, task="resolve",
                                    call_run=_call_run, resolve_out=_resolve_out))
