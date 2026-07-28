"""Typed, canonicalizing result-schema layer for chemkit.

This is the structural counterpart to ``integrity.py``. Where integrity checks
*computation correctness* (did the SCF converge, is the geometry a minimum),
this module checks *result shape*: that every task emits the common header, and
that the one headline quantity a task reports is exposed under a single, stable,
machine-discoverable key — regardless of the per-task field name.

Why it exists
-------------
Each task historically hand-assembles its own dict, and the same physical
quantity is emitted under different names across tasks (a single-point energy is
``total_energy_eV``; the same electronic energy inside a frequency run is
``electronic_energy_eV``). Readers compensated with hand-kept fallback chains
(e.g. ``reaction_energy.py``: ``r.get("electronic_energy_eV") or
r.get("total_energy_eV")``), and the fidelity Layer-C scorer relied on a
per-spec ``report_value_field`` to know which key holds the number it must
compare. That makes "we scored the right field" rest on hand-maintained maps.

This layer makes the headline field *discoverable from the result itself*:
``canonicalize()`` stamps a stable ``headline_field`` / ``headline_value`` /
``headline_units`` pointer onto every result whose task reports a scalar
headline. It is **purely additive** — it never renames or removes a task's own
keys — so the 170 fidelity specs, the regression tests, and every archived run
keep reading exactly what they read before.

Design (mirrors integrity.py deliberately)
------------------------------------------
- Pure stdlib + ``typing`` (``TypedDict``). No pydantic: the per-call engine
  subprocess must stay import-light (core deps are mcp/ase/numpy).
- Wired into ``integrity.finalize()`` so it runs at the single seam every task
  already calls, alongside ``_promote_method_provenance``.
- Validation findings are emitted through the same ``IntegrityCheck`` shape and
  fold into the existing ``integrity`` block, at ``warning`` severity — turning
  the schema on cannot fail any existing run.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, TypedDict

# Bump when the canonical headline registry, alias table, or required-header set
# changes in a way a downstream reader would care about. Stamped onto every
# result via canonicalize(); archived results without it are schema v0.
SCHEMA_VERSION = 1


class BaseResultHeader(TypedDict, total=False):
    """The common header every task emits (mirrors schema.base_result()).

    `total=False` because composites/auxiliary results legitimately omit some
    fields (e.g. a name-to-smiles result has no n_atoms). The validator checks
    presence/typing without forcing every key on every task.
    """
    task: str
    method: str
    program: str
    input_file: str
    n_atoms: int
    atoms: List[str]
    charge: int
    multiplicity: int
    solvent: Optional[str]
    cli_invocation: str
    integrity: Dict[str, Any]
    schema_version: int
    headline_field: Optional[str]
    headline_value: Optional[float]
    headline_units: Optional[str]


# ---------------------------------------------------------------------------
# Canonical headline field per task.
#
# task name (the @register id, == result["task"]) -> (field_name, units)
#
# These field names are EXACTLY the values the 170 fidelity specs already declare
# in `report_value_field`, so nothing in the specs or tests needs to change. A
# task whose headline is not a single scalar (conformer-search, fukui,
# visualize-orbitals, build, name-to-smiles, IRC, scan) maps to None — those
# specs already use `report_value_field: null`, and Layer-C skips the value check
# for them.
#
# `redox_potential`'s field carries the reference electrode suffix
# (redox_potential_V_vs_SHE / _vs_Fc+/Fc), so it is resolved dynamically in
# _headline_for() rather than hard-coded.
# ---------------------------------------------------------------------------
HEADLINE: Dict[str, Optional[Tuple[str, str]]] = {
    "single_point":                 ("total_energy_eV", "eV"),
    "geometry_optimization":        ("total_energy_eV", "eV"),
    "vibrational_thermochemistry":  ("electronic_energy_eV", "eV"),
    "transition_state":             ("total_energy_eV", "eV"),
    "binding_energy":               ("binding_energy_eV", "eV"),
    "redox_potential":              None,  # resolved dynamically (electrode suffix)
    "pka":                          ("pKa", "dimensionless"),
    "logp":                         ("logp", "dimensionless"),
    "solvation":                    ("delta_G_solv_kcal_mol", "kcal/mol"),
    "reaction_energy":              ("delta_E_kcal_mol", "kcal/mol"),
    "reaction_profile":             ("delta_G_activation_kcal_mol", "kcal/mol"),
    "electrostatics":               ("dipole_debye", "debye"),
    "frontier_orbitals":            ("homo_lumo_gap_eV", "eV"),
    # Scalar-less / structure / lookup tasks: no single headline number.
    "fukui":                        None,
    "conformational_search":        None,
    "conformational_analysis":      None,
    "visualize_orbitals":           None,
    "intrinsic_reaction_coordinate": None,
    "build_from_smiles":            None,
    "name_to_smiles":               None,
}


# Genuinely-equivalent field names: the SAME physical quantity emitted under two
# names by different tasks. Applied ADDITIVELY (both keys end up present) so a
# reader can rely on the canonical key without breaking the legacy one. Each
# entry is verified to be the same quantity, NOT a lossy conflation:
#   electronic_energy_eV (freq's bare electronic energy, pre-ZPE) IS the same
#   electronic energy a single point reports as total_energy_eV.
ALIASES: Dict[str, str] = {
    "electronic_energy_eV": "total_energy_eV",
}


# Common-header keys whose presence/type the structural validator checks. Kept
# deliberately small — only the fields every real calculation task shares.
_REQUIRED_HEADER: Dict[str, type] = {
    "task": str,
    "method": str,
    "program": str,
}


def _headline_for(result: Dict[str, Any], task: str) -> Optional[Tuple[str, str]]:
    """Resolve (field, units) for a task's headline, handling the dynamic
    redox electrode suffix. Returns None for scalar-less tasks."""
    if task == "redox_potential":
        # field looks like redox_potential_V_vs_<ref> (ref in SHE / Fc+/Fc / ...)
        for k in result:
            if k.startswith("redox_potential_V_vs_"):
                return (k, "V")
        return None
    return HEADLINE.get(task)


def canonicalize(result: Dict[str, Any], task: str) -> Dict[str, Any]:
    """Additively stamp the stable headline pointer + field aliases + version.

    Never renames or deletes an existing key. Mutates and returns `result`.
      - schema_version: SCHEMA_VERSION
      - headline_field / headline_value / headline_units: a single discoverable
        pointer to THE number this task reports (None for scalar-less tasks).
      - aliases: for each (alias -> canonical) in ALIASES, if the alias key is
        present and the canonical is absent, copy it across (and vice-versa), so
        both names resolve to the one value.
    """
    result.setdefault("schema_version", SCHEMA_VERSION)

    # Additive field aliasing (both directions, never overwrite an existing key).
    for alias, canonical in ALIASES.items():
        if alias in result and canonical not in result:
            result[canonical] = result[alias]
        elif canonical in result and alias not in result:
            result[alias] = result[canonical]

    hf = _headline_for(result, task)
    if hf is not None:
        field, units = hf
        val = result.get(field)
        # Only stamp a pointer when the value is actually present and numeric;
        # a failed run may legitimately lack it (don't fabricate a headline).
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            result.setdefault("headline_field", field)
            result.setdefault("headline_value", val)
            result.setdefault("headline_units", units)
    return result


# ---------------------------------------------------------------------------
# Structural validation. Returns plain dicts shaped like IntegrityCheck so the
# caller (integrity.finalize) can fold them into the existing integrity block
# without importing this module's types. Severity is "warning" so enabling the
# schema cannot turn a passing run into a failing one (calculation-reporting
# can later promote selected checks to "error" once the engine is clean).
# ---------------------------------------------------------------------------
def _finding(name: str, ok: bool, detail: str, severity: str = "warning") -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "severity": severity, "detail": detail}


def validate_result(result: Dict[str, Any], task: str) -> List[Dict[str, Any]]:
    """Structural (shape) checks, distinct from integrity's correctness checks.

    Emits at most a few `schema_*` findings:
      - schema_header: required common-header keys present with the right type.
      - schema_headline: for a task that declares a scalar headline, that field
        is present and finite (a failed run lacking it is a legitimate warning,
        not an error — the integrity gate already governs that).
    """
    findings: List[Dict[str, Any]] = []

    missing = [k for k in _REQUIRED_HEADER if k not in result]
    bad_type = [
        k for k, t in _REQUIRED_HEADER.items()
        if k in result and not isinstance(result[k], t)
    ]
    findings.append(_finding(
        "schema_header",
        not missing and not bad_type,
        f"required header keys present and typed "
        f"(missing={missing}, wrong_type={bad_type})"
        if (missing or bad_type) else "common header present",
    ))

    hf = _headline_for(result, task)
    if hf is not None:
        field, _units = hf
        val = result.get(field)
        present_finite = isinstance(val, (int, float)) and not isinstance(val, bool)
        findings.append(_finding(
            "schema_headline",
            present_finite,
            f"headline field {field!r} = {val!r} "
            f"({'present and numeric' if present_finite else 'missing or non-numeric'})",
        ))

    return findings
