"""Computation-side integrity layer.

This is the second tower of chemkit's integrity machinery. The rest of the rule
set (research-/calculation-reporting-standards) governs *labeling* — provenance,
citations, screening-grade disclosure. This module governs *computation
correctness*: it runs convergence and physical-sanity checks on a result and,
under the default hard-abort gate, REFUSES to present the headline number as
trustworthy when a check fails.

Why it exists: an honestly-provenanced number sitting on a non-converged SCF or a
broken Hessian is more dangerous than either flaw alone, because the honest
method block buys trust the computation has not earned. Labeling a non-converged
energy `converged: false` is necessary but not sufficient — a consumer can still
read and quote it. This layer makes the failure *gate* the result.

Design — fail loudly, never destroy evidence:
  - Tasks write their `.xyz`/artifacts BEFORE assembling the result dict, and the
    live `.out` log is owned by the server, so by the time the gate raises, the
    evidence is already on disk.
  - `IntegrityError` CARRIES the partial (stamped) result so the CLI can still
    `write_result()` it before exiting nonzero. The on-disk JSON of a failed run
    is identical to a passing one except for the `integrity` block and exit code.
  - `--allow-unconverged` downgrades the abort to a stamped warning (status
    downgraded, `trustworthy=False`, `gate_bypassed=True`) for the legitimate
    "inspect the failed geometry" workflow.

Every result gets an `integrity` block stamped onto it (pass or fail):
    result["integrity"] = {
        "status": "ok" | "warning" | "failed",
        "trustworthy": bool,            # status != "failed"
        "checks": [{name, ok, severity, detail}, ...],
        "gate_bypassed": bool,          # present only when a failure was downgraded
    }
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

# Only "error" gates a hard-abort. "warning"/"info" are recorded but never raise.
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}


@dataclass
class IntegrityCheck:
    name: str       # stable id, e.g. "scf_converged", "n_imag_minimum"
    ok: bool
    severity: str   # "error" | "warning" | "info"
    detail: str     # human-readable; names the field + observed vs expected

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IntegrityError(Exception):
    """Raised at the end of a task.run() when one or more severity=='error'
    checks fail under the (default) hard-abort gate. Carries the partial result
    so the caller can still persist it (stamped status=failed) before exiting
    nonzero — fail loudly without destroying evidence."""

    def __init__(self, result: Dict[str, Any], failed: List[IntegrityCheck]):
        self.result = result
        self.failed = failed
        names = ", ".join(c.name for c in failed) or "(unknown)"
        super().__init__(f"integrity gate failed: {names}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _finite(x: Any) -> bool:
    """True iff x is a real, finite number (mirrors io._scrub's notion)."""
    if isinstance(x, bool):  # bool is an int subclass; not a numeric result value
        return False
    if not isinstance(x, (int, float)):
        return False
    return math.isfinite(float(x))


def _check(name: str, ok: bool, severity: str, detail: str) -> IntegrityCheck:
    return IntegrityCheck(name=name, ok=bool(ok), severity=severity, detail=detail)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Callable[[Dict[str, Any]], List[IntegrityCheck]]] = {}


def register(task_name: str):
    def deco(fn):
        _REGISTRY[task_name] = fn
        return fn
    return deco


def validate(result: Dict[str, Any], task_name: str) -> List[IntegrityCheck]:
    """Run the registered checks for `task_name`. Unknown/aux tasks (scan,
    frontier, confsearch, orbitals, build) have no registered checks and pass
    vacuously (empty list)."""
    fn = _REGISTRY.get(task_name)
    if fn is None:
        return []
    try:
        return fn(result) or []
    except Exception as exc:  # a buggy check must never crash a real calculation
        return [_check("integrity_self_check", False, "warning",
                       f"integrity check raised {type(exc).__name__}: {exc}")]


def rollup_status(checks: List[IntegrityCheck]) -> str:
    worst = max((SEVERITY_ORDER[c.severity] for c in checks if not c.ok), default=-1)
    if worst >= SEVERITY_ORDER["error"]:
        return "failed"
    if worst >= SEVERITY_ORDER["warning"]:
        return "warning"
    return "ok"


def integrity_block(checks: List[IntegrityCheck]) -> Dict[str, Any]:
    status = rollup_status(checks)
    return {
        "status": status,                   # ok | warning | failed
        "trustworthy": status != "failed",  # is the headline number safe to quote
        "checks": [c.as_dict() for c in checks],
    }


def gate(result: Dict[str, Any], task_name: str, *, allow_unconverged: bool = False) -> Dict[str, Any]:
    """The single seam every task.run() calls at its end.

    Stamps result["integrity"] in place. If any severity=="error" check failed:
      - default: raise IntegrityError carrying the stamped result.
      - allow_unconverged: downgrade status failed->warning, trustworthy=False,
        gate_bypassed=True, and return without raising.
    """
    checks = validate(result, task_name)
    block = integrity_block(checks)
    failed_errors = [c for c in checks if (not c.ok and c.severity == "error")]

    if failed_errors and allow_unconverged:
        if block["status"] == "failed":
            block["status"] = "warning"
        block["trustworthy"] = False
        block["gate_bypassed"] = True

    result["integrity"] = block

    if failed_errors and not allow_unconverged:
        raise IntegrityError(result, failed_errors)
    return result


# Method-provenance fields that the calculation-reporting standard wants in the
# top-level method block, but which the PySCF backend only stashes under
# `code_specific`. We copy them up (additively) so any consumer can read
# result["basis"] / result["functional"] without reaching into the per-backend
# block. solvent is NOT listed: schema.base_result already sets it top-level
# (gas phase = None is its real value), so promoting would risk clobbering it.
_PROMOTE_FROM_CODE_SPECIFIC = ("functional", "basis")


def _promote_method_provenance(result: Dict[str, Any]) -> None:
    """Additively copy method-provenance fields from code_specific to top level.

    Idempotent and non-destructive: a key is promoted ONLY when it is absent or
    None at top level, so an authoritative top-level value is never overwritten.
    Semi-empirical backends (xtb/PM7) carry no functional/basis in
    code_specific, so nothing is promoted for them -- correct, since they have
    no basis set. Never raises; a malformed code_specific is simply skipped.
    """
    cs = result.get("code_specific")
    if not isinstance(cs, dict):
        return
    for key in _PROMOTE_FROM_CODE_SPECIFIC:
        if result.get(key) is None and cs.get(key) is not None:
            result[key] = cs[key]


def finalize(result: Dict[str, Any], *, gate_integrity: bool = True,
             allow_unconverged: bool = False) -> Dict[str, Any]:
    """Single end-of-run() seam for every task.

    Always stamps result["integrity"]. When gate_integrity is True, applies the
    hard-abort gate (may raise IntegrityError under the default). When False
    (used by composites for their SUB-task calls), only stamps — never raises —
    so a sub-result carries its own integrity status without aborting mid-
    composite; the composite then runs its own gate over the aggregated result.

    The task name is read from result["task"] (set by schema.base_result).
    """
    _promote_method_provenance(result)
    task_name = result.get("task", "")
    if gate_integrity:
        return gate(result, task_name, allow_unconverged=allow_unconverged)
    result["integrity"] = integrity_block(validate(result, task_name))
    return result


# ===========================================================================
# Per-task checks. Each reads ONLY fields already present in the result dict.
# Thresholds match the scattered checks they consolidate.
# ===========================================================================

def _energy_finite_check(result: Dict[str, Any]) -> Optional[IntegrityCheck]:
    e = result.get("total_energy_eV")
    if e is None:
        e = result.get("electronic_energy_eV")
    return _check(
        "energy_finite", _finite(e), "error",
        f"total/electronic energy = {e!r} (must be a finite number)",
    )


def _scf_converged_check(result: Dict[str, Any]) -> Optional[IntegrityCheck]:
    """Only when the calculation actually has an SCF flag (dft/hf). The flag may
    sit top-level (frontier/orbitals copy it up) or under code_specific (sp via
    pack_scf_result). xtb/mopac have no SCF flag -> None."""
    if "scf_converged" in result:
        flag = result.get("scf_converged")
    else:
        cs = result.get("code_specific")
        if not isinstance(cs, dict) or "scf_converged" not in cs:
            return None  # xtb/mopac: no SCF flag to check
        flag = cs.get("scf_converged")
    ok = flag is True
    return _check(
        "scf_converged", ok, "error",
        f"scf_converged = {flag!r} "
        f"(SCF must converge for the energy/orbitals to be meaningful)",
    )


@register("single_point")
def _check_sp(result):
    checks = [_energy_finite_check(result)]
    scf = _scf_converged_check(result)
    if scf is not None:
        checks.append(scf)
    return checks


@register("geometry_optimization")
def _check_opt(result):
    checks = []
    # Zero-DOF systems (a single atom, or anything with no internal coordinates
    # to relax) have nothing to converge — treat as vacuously converged.
    n_atoms = result.get("n_atoms") or 0
    zero_dof = n_atoms <= 1
    converged = bool(result.get("converged"))
    checks.append(_check(
        "opt_converged", converged or zero_dof, "error",
        f"converged = {result.get('converged')!r}"
        + (" (zero-DOF system: vacuously converged)" if zero_dof else ""),
    ))
    checks.append(_energy_finite_check(result))
    scf = _scf_converged_check(result)
    if scf is not None:
        checks.append(scf)
    return checks


@register("vibrational_thermochemistry")
def _check_freq(result):
    # Stationary-point classification by imaginary-mode count:
    #   0  -> a true minimum (the usual vibrational-analysis target);
    #   1  -> a valid first-order saddle (a transition state). This is a
    #         legitimate, correctly-characterized stationary point — NOT an error.
    #         Its ideal-gas thermochemistry (G/H/S) is undefined (one mode is
    #         missing from the 3N-6 set), which is EXPECTED for a saddle, so the
    #         gibbs_finite check is relaxed to a warning in this case rather than
    #         marking the whole result untrustworthy.
    #   >=2 -> a higher-order saddle: neither a clean minimum nor a clean TS, so
    #         the geometry/thermochemistry is genuinely unreliable -> error.
    # (A genuine reaction TS with exactly 1 imaginary mode should ideally be found
    # via the transition-state task, but running freq directly on a saddle is a
    # valid, honestly-characterized result — see _check_ts for the TS-task gate.)
    n_imag = result.get("n_imaginary_modes")
    is_saddle = (n_imag == 1)
    checks = [_check(
        "n_imag_stationary_point", (n_imag in (0, 1)), "error",
        f"n_imaginary_modes = {n_imag!r} (0 = minimum, 1 = valid first-order "
        "saddle/TS; >=2 = higher-order saddle, not a usable stationary point)",
    )]
    g = result.get("gibbs_free_energy_eV")
    # For a 1-imag saddle, undefined thermochemistry is expected, not a failure.
    gibbs_sev = "warning" if is_saddle else "error"
    gibbs_detail = (
        f"gibbs_free_energy_eV = {g!r} (undefined for a first-order saddle — "
        "expected; the electronic energy and frequencies remain valid)"
        if is_saddle else
        f"gibbs_free_energy_eV = {g!r} (must be finite)"
    )
    checks.append(_check("gibbs_finite", _finite(g), gibbs_sev, gibbs_detail))
    return checks


# TS imaginary-mode band — same constants the ts task already uses.
_TS_IMAG_MIN_CM = 100.0
_TS_IMAG_MAX_CM = 3500.0


@register("transition_state")
def _check_ts(result):
    checks = [_check(
        "ts_converged", bool(result.get("converged")), "error",
        f"converged = {result.get('converged')!r}",
    )]
    vf = result.get("verify_freq")
    if isinstance(vf, dict) and "n_imaginary_modes" in vf:
        n_imag = vf.get("n_imaginary_modes")
        mag = vf.get("imaginary_mode_magnitude_cm-1")
        in_band = (
            n_imag == 1
            and isinstance(mag, (int, float))
            and _TS_IMAG_MIN_CM <= abs(mag) <= _TS_IMAG_MAX_CM
        )
        checks.append(_check(
            "ts_one_imag_in_band", bool(in_band), "error",
            f"n_imaginary_modes={n_imag!r}, |imag|={mag!r} cm^-1 "
            f"(a reaction TS needs exactly 1 imaginary mode in "
            f"{_TS_IMAG_MIN_CM:.0f}-{_TS_IMAG_MAX_CM:.0f}i cm^-1)",
        ))
    return checks


@register("electrostatics")
def _check_electrostatics(result):
    sum_q = result.get("sum_of_charges")
    charge = result.get("charge", 0)
    if sum_q is None:
        # Some backends may not report partial charges; nothing to enforce.
        return []
    ok = _finite(sum_q) and abs(sum_q - charge) < 0.05
    return [_check(
        "charge_consistency", ok, "error",
        f"sum_of_charges = {sum_q!r}, molecular charge = {charge!r} "
        "(partial charges must sum to the total charge within 0.05 e)",
    )]


@register("binding_energy")
def _check_binding(result):
    charge = result.get("charge", 0)
    mcs = result.get("monomer_charge_sum")
    checks = []
    if mcs is not None:
        checks.append(_check(
            "charge_conservation", (mcs == charge), "error",
            f"monomer_charge_sum = {mcs!r}, complex charge = {charge!r} "
            "(fragment charges must sum to the complex charge)",
        ))
    be = result.get("binding_energy_eV")
    checks.append(_check(
        "binding_finite", _finite(be), "error",
        f"binding_energy_eV = {be!r} (must be finite)",
    ))
    return checks


def _state_converged(block: Dict[str, Any], mode: str, label: str) -> IntegrityCheck:
    """Convergence of one redox oxidation-state block, by mode.

    Zero-DOF states (a single atom, e.g. H / H⁺ in an H→H⁺ couple) have no
    geometry to relax and no vibrational modes, so non-convergence is vacuous —
    mirror _check_opt's carve-out so the redox gate stays consistent with the
    opt/freq gates the same sub-calculation already passed.
    """
    zero_dof = (block.get("n_atoms") or 0) <= 1
    if mode == "freq":
        n_imag = block.get("n_imaginary_modes")
        return _check(
            f"{label}_converged", (n_imag == 0) or zero_dof, "error",
            f"{label}.n_imaginary_modes = {n_imag!r} (freq mode: each state "
            "must be a minimum)"
            + (" (zero-DOF system: vacuously converged)" if zero_dof else ""),
        )
    if mode == "adiabatic":
        return _check(
            f"{label}_converged", bool(block.get("converged")) or zero_dof, "error",
            f"{label}.converged = {block.get('converged')!r} (adiabatic mode)"
            + (" (zero-DOF system: vacuously converged)" if zero_dof else ""),
        )
    # vertical: no geometry relaxation; nothing to converge geometrically.
    return _check(f"{label}_converged", True, "info",
                  f"{label}: vertical mode, no geometry relaxation")


@register("redox_potential")
def _check_redox(result):
    mode = result.get("mode", "adiabatic")
    checks = []
    ox = result.get("oxidized_state")
    red = result.get("reduced_state")
    if isinstance(ox, dict):
        checks.append(_state_converged(ox, mode, "oxidized_state"))
    if isinstance(red, dict):
        checks.append(_state_converged(red, mode, "reduced_state"))
    # The reference-suffixed potential key (redox_potential_V_vs_SHE, etc.).
    pot_key = next((k for k in result if k.startswith("redox_potential_V_vs_")), None)
    pot = result.get(pot_key) if pot_key else None
    checks.append(_check(
        "potential_finite", _finite(pot), "error",
        f"{pot_key or 'redox_potential_V_vs_*'} = {pot!r} (must be finite)",
    ))
    return checks


@register("pka")
def _check_pka(result):
    checks = []
    species = result.get("species")
    if isinstance(species, dict):
        # A pKa species must be a true MINIMUM — but only a GENUINE (hard,
        # |nu| > 50i) imaginary mode means it isn't one. The freq sub-task floors
        # soft sub-50i rotor modes (floppy methyl/carboxylate torsions that
        # finite-difference noise dips marginally negative) and treats them as
        # real low-frequency vibrations, so they do NOT disqualify a minimum.
        # Gate on the saddle count; fall back to the total only if the saddle
        # count is unavailable (older species records).
        hard_bad, soft_seen = [], []
        for label, blk in species.items():
            if not isinstance(blk, dict):
                continue
            n_saddle = blk.get("n_saddle_imaginary_modes")
            n_total = blk.get("n_imaginary_modes")
            n_hard = n_saddle if n_saddle is not None else n_total
            if n_hard not in (0, None):
                hard_bad.append(f"{label}={n_hard}")
            elif (blk.get("n_soft_imaginary_modes") or 0) > 0:
                soft_seen.append(label)
        checks.append(_check(
            "species_converged", (len(hard_bad) == 0), "error",
            f"species with genuine (saddle) imaginary modes: {hard_bad or 'none'} "
            "(every pKa species must be a minimum; soft sub-50i rotor modes are "
            "floored as real and do not count)",
        ))
        if soft_seen:
            checks.append(_check(
                "species_soft_modes", True, "warning",
                f"species with soft (floored, sub-50i) imaginary modes: {soft_seen} "
                "— treated as floppy real low-frequency rotors, not saddles; the "
                "pKa is fine but these soft modes add thermochemical uncertainty.",
            ))
    pka = result.get("pKa")
    checks.append(_check(
        "pka_finite", _finite(pka), "error",
        f"pKa = {pka!r} (must be finite)",
    ))
    return checks


def _rxn_species_ok(block: Dict[str, Any]) -> bool:
    """A reaction_energy species block converged per its mode.

    Zero-DOF species (a single atom, e.g. atomic H in H2 -> 2 H) have no
    geometry to relax and no vibrational modes, so non-convergence is vacuous —
    mirror the opt/freq gates' carve-out so the reaction gate stays consistent
    with the per-species sub-calculations.
    """
    if not isinstance(block, dict):
        return False
    if "opt" in block:
        zero_dof = (block["opt"].get("n_atoms") or block.get("n_atoms") or 0) <= 1
        return bool(block["opt"].get("converged")) or zero_dof
    if "freq" in block:
        zero_dof = (block["freq"].get("n_atoms") or block.get("n_atoms") or 0) <= 1
        if zero_dof:
            return True
        # freq species: preopt converged AND a minimum.
        preopt_ok = (block["freq"].get("preopt_converged") in (True, None))
        return preopt_ok and (block.get("n_imaginary_modes") in (0, None))
    # sp mode: nothing to converge.
    return True


@register("reaction_energy")
def _check_reaction_energy(result):
    checks = []
    blocks = (result.get("reactants") or []) + (result.get("products") or [])
    bad = [b.get("spec", "?") for b in blocks if not _rxn_species_ok(b)]
    checks.append(_check(
        "species_converged", (len(bad) == 0), "error",
        f"unconverged species: {bad or 'none'}",
    ))
    methods = {b.get("method") for b in blocks if b.get("method") is not None}
    checks.append(_check(
        "same_method", (len(methods) <= 1), "error",
        f"species methods = {sorted(methods)} (energies subtracted across a "
        "reaction must come from one level of theory)",
    ))
    dE = result.get("delta_E_kcal_mol")
    finite = _finite(dE)
    if result.get("mode") == "freq":
        finite = finite and _finite(result.get("delta_G_kcal_mol"))
    checks.append(_check(
        "delta_finite", finite, "error",
        f"delta_E_kcal_mol = {dE!r}"
        + (f", delta_G_kcal_mol = {result.get('delta_G_kcal_mol')!r}"
           if result.get("mode") == "freq" else "")
        + " (reaction energy must be finite)",
    ))
    return checks


@register("reaction_profile")
def _check_reaction_profile(result):
    verdict = result.get("verdict") or {}
    return [
        _check("reactant_minimum", bool(verdict.get("reactant_is_minimum")), "error",
               f"verdict.reactant_is_minimum = {verdict.get('reactant_is_minimum')!r}"),
        _check("product_minimum", bool(verdict.get("product_is_minimum")), "error",
               f"verdict.product_is_minimum = {verdict.get('product_is_minimum')!r}"),
        _check("ts_first_order", bool(verdict.get("ts_is_first_order_saddle")), "error",
               f"verdict.ts_is_first_order_saddle = {verdict.get('ts_is_first_order_saddle')!r}"),
    ]


@register("logp")
def _check_logp(result):
    v = result.get("logp")
    return [_check("logp_finite", _finite(v), "error",
                   f"logp = {v!r} (must be finite)")]


@register("solvation")
def _check_solvation(result):
    v = result.get("delta_G_solv_eV")
    return [_check("dgsolv_finite", _finite(v), "error",
                   f"delta_G_solv_eV = {v!r} (must be finite)")]


@register("fukui")
def _check_fukui(result):
    """Σf± ≈ 1 is a charge-conservation proxy for the N±1 states converging.
    Warning severity (does not gate) — a drift here flags a likely SCF problem
    in a charged state without hard-aborting a screening descriptor."""
    checks = []
    for key, label in (("fukui_plus", "f+"), ("fukui_minus", "f-")):
        vals = result.get(key)
        if isinstance(vals, list) and vals and all(_finite(v) for v in vals):
            s = sum(vals)
            checks.append(_check(
                f"fukui_charge_conservation_{label}",
                abs(s - 1.0) < 0.05, "warning",
                f"sum({label}) = {s:.4f} (should be ~1.0; large drift suggests "
                "an N±1 state did not converge)",
            ))
    return checks


@register("frontier_orbitals")
def _check_frontier(result):
    """Frontier orbitals are read off a single SCF, so gate exactly the SCF the
    same way `single_point` does: finite total energy, and (dft/hf) a converged
    SCF. Deliberately does NOT gate on a missing LUMO — a basis-saturated anion
    (e.g. F⁻ in GFN2's minimal valence basis) legitimately has no virtual
    orbital and returns a valid partial result."""
    checks = [_energy_finite_check(result)]
    scf = _scf_converged_check(result)
    if scf is not None:
        checks.append(scf)
    return checks


@register("visualize_orbitals")
def _check_orbitals(result):
    """The deliverable is a wavefunction file; gate that it was actually written
    and (dft/hf) that the SCF behind it converged — a molden built from a
    non-converged SCF would visualize meaningless orbitals."""
    import os
    checks = []
    molden = result.get("molden_path")
    checks.append(_check(
        "molden_written", bool(molden) and os.path.isfile(molden), "error",
        f"molden_path = {molden!r} (the orbital file must exist on disk)",
    ))
    scf = _scf_converged_check(result)  # only present on the dft/hf path
    if scf is not None:
        checks.append(scf)
    return checks


@register("build_from_smiles")
def _check_build(result):
    """A build must have produced a structure (>0 atoms). If an optional QM
    refinement was run, it must have converged — a build that returns a
    non-converged 'optimized' geometry is silently wrong."""
    checks = []
    n_atoms = result.get("n_atoms")
    checks.append(_check(
        "structure_built", isinstance(n_atoms, int) and n_atoms > 0, "error",
        f"n_atoms = {n_atoms!r} (3D embedding must yield at least one atom)",
    ))
    qm = result.get("qm_optimization")
    if isinstance(qm, dict) and "converged" in qm:
        checks.append(_check(
            "qm_refinement_converged", bool(qm.get("converged")), "error",
            f"qm_optimization.converged = {qm.get('converged')!r} "
            "(the QM refinement geometry did not converge)",
        ))
    return checks


@register("conformational_search")
def _check_confsearch(result):
    """A conformer search that found zero conformers failed. Finding any is
    enough — the count itself (and dedup) is the science, not a gate."""
    n_found = result.get("n_conformers_found")
    return [_check(
        "conformers_found", isinstance(n_found, int) and n_found >= 1, "error",
        f"n_conformers_found = {n_found!r} (a search must return ≥1 conformer)",
    )]


@register("conformational_analysis")
def _check_scan(result):
    """Per-point optimizations may individually fail (reported via n_converged)
    — that is normal and NOT gated. But a dihedral where EVERY scanned point
    failed to converge yields a meaningless profile; gate that total failure.
    An empty scan (no rotatable dihedral found) is a warning, not an error."""
    dihedrals = result.get("dihedrals")
    if not dihedrals:
        return [_check(
            "scan_has_dihedral", False, "warning",
            "no dihedral was scanned (no rotatable bond found or supplied)",
        )]
    dead = [
        d.get("atoms_1based")
        for d in dihedrals
        if (d.get("n_points") or 0) > 0 and (d.get("n_converged") or 0) == 0
    ]
    return [_check(
        "scan_points_converged", len(dead) == 0, "error",
        f"dihedral(s) with ZERO converged points: {dead or 'none'} "
        "(every point of a scanned dihedral failed to optimize)",
    )]


@register("intrinsic_reaction_coordinate")
def _check_irc(result):
    """IRC has no hard convergence invariant — its endpoints are steepest-descent
    walk termini, not optimized minima, and isoenergetic endpoints
    (distinct_endpoints=False) are legitimate for symmetric reactions. So the
    only gateable floor is that the reported endpoint energies are finite; the
    connectivity verdict stays informational (surfaced, not gated)."""
    checks = []
    for key in ("forward_endpoint_energy_eV", "reverse_endpoint_energy_eV"):
        if key in result:
            v = result.get(key)
            checks.append(_check(
                f"{key}_finite", _finite(v), "error",
                f"{key} = {v!r} (IRC endpoint energy must be finite)",
            ))
    return checks
