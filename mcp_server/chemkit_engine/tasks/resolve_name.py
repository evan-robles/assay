"""Name -> SMILES resolution task (no geometry build).

Resolves a plain molecule *name* (e.g. "caffeine", "L-alanine") to a SMILES
string using the chemkit resolver chain PubChem -> OPSIN -> NIST WebBook, and
reports which source answered with an ACS-format citation. This is the pure
lookup half of `build-from-smiles`: it does NOT generate a 3D geometry, run
Open Babel, or do any QM. Use `build-from-smiles` when you actually want an .xyz.

The heavy lifting lives in ``chemkit_engine.resolve.resolve_name_to_smiles``;
this task is a thin wrapper that shapes the result into the standard chemkit
JSON envelope and runs it through the shared ``finalize`` seam.
"""
from __future__ import annotations

from typing import Any, Dict

from ..integrity import finalize


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
    from ..resolve import resolve_name_to_smiles
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
