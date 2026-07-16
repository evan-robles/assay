"""Reaction energy: ΔE / ΔH / ΔG for a stoichiometrically balanced reaction.

Composes the existing single-point, optimization, and frequency tasks. The
heart of the skill is bookkeeping: same method/basis/solvent on every species,
correct stoichiometric weighting, and clear surfacing of which species
contributed what.

Modes:
  sp    — single-point on each xyz as supplied (geometry is the user's
          responsibility). Returns ΔE only.
  opt   — optimize each species first, then single-point. Returns ΔE on
          relaxed geometries.
  freq  — full opt + freq on each species. Returns ΔE, ΔH(T), ΔG(T) plus a
          minimum check (no imaginary modes on any species).

The "energy_zero" convention has to be uniform across every species or the
subtraction is meaningless. Each backend exposes a different absolute reference
(xtb: isolated atoms at ∞; PM7: elements in standard states; DFT/HF: bare
nuclei + electrons), but since we use the *same* method for every species in
the cycle, the references cancel exactly.

Species spec parsing — see _parse_species_spec for the grammar.
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ..calculators import program_label, method_label
from ..io import read_geometry
from ..integrity import finalize
from ..schema import base_result, EV_TO_HARTREE, EV_TO_KCAL
from . import sp as sp_task
from . import opt as opt_task
from . import freq as freq_task


# ---------------------------------------------------------------------------
# Species spec parsing
# ---------------------------------------------------------------------------

# A single --reactant / --product argument has the form:
#
#   [COEF*]PATH[,charge=Q][,mult=M]
#
# Examples:
#   h2.xyz                       coef=1, q=0, m=1
#   2*h2.xyz                     coef=2
#   3*h2o.xyz,charge=0,mult=1    coef=3, q=0, m=1
#   acetate.xyz,charge=-1        coef=1, q=-1, m=1
#
# We use ',' rather than ':' for the kv tail because ':' collides with Windows
# drive letters (e.g. C:\foo) and isn't unambiguous if a path contains one.

_SPEC_RE = re.compile(
    r"^\s*(?:(?P<coef>\d+(?:\.\d+)?)\s*\*\s*)?"
    r"(?P<path>[^,]+?)"
    r"(?P<tail>(?:\s*,\s*[a-zA-Z_]+\s*=\s*-?\d+)*)\s*$"
)


def _parse_species_spec(spec: str) -> Tuple[str, float, int, int]:
    """Parse one species spec into (path, coef, charge, mult)."""
    m = _SPEC_RE.match(spec)
    if not m:
        raise ValueError(
            f"Could not parse species spec {spec!r}. "
            "Expected '[COEF*]PATH[,charge=Q][,mult=M]'."
        )
    coef = float(m.group("coef")) if m.group("coef") else 1.0
    path = m.group("path").strip()
    charge = 0
    mult = 1
    tail = m.group("tail") or ""
    for part in [p.strip() for p in tail.split(",") if p.strip()]:
        key, val = [s.strip() for s in part.split("=", 1)]
        key = key.lower()
        try:
            ival = int(val)
        except ValueError:
            raise ValueError(f"Invalid integer in spec {spec!r}: {part!r}")
        if key == "charge":
            charge = ival
        elif key in ("mult", "multiplicity"):
            mult = ival
        else:
            raise ValueError(f"Unknown key {key!r} in spec {spec!r}")
    return path, coef, charge, mult


def parse_species_list(specs: List[str]) -> List[Dict[str, Any]]:
    """Parse a list of species spec strings."""
    out = []
    for spec in specs:
        path, coef, q, m = _parse_species_spec(spec)
        out.append({
            "spec": spec, "path": path, "coef": coef,
            "charge": q, "multiplicity": m,
        })
    return out


# ---------------------------------------------------------------------------
# Per-species evaluation
# ---------------------------------------------------------------------------

def _evaluate_species(
    species: Dict[str, Any], *, mode: str, method: str, solvent: Optional[str],
    temperature_K: float, pressure_Pa: float,
    tier: Optional[str], functional: Optional[str], basis: Optional[str],
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
) -> Dict[str, Any]:
    """Run sp / opt+sp / opt+freq on one species, return a normalized block."""
    path = species["path"]
    if not os.path.isfile(path):
        raise FileNotFoundError(f"species file not found: {path}")

    common = dict(
        method=method, charge=species["charge"],
        multiplicity=species["multiplicity"], solvent=solvent,
        tier=tier, functional=functional, basis=basis,
        density_fit=density_fit,
        solvent_model=solvent_model,
        cli=f"(internal reaction_energy: {species['spec']})",
        gate_integrity=False,  # sub-call: stamp only; rxn-energy gates the result
    )

    block: Dict[str, Any] = {
        "spec": species["spec"], "path": os.path.abspath(path),
        "coef": species["coef"], "charge": species["charge"],
        "multiplicity": species["multiplicity"],
    }

    if mode == "sp":
        r = sp_task.run(path, **common)
        block["energy_eV"] = r["total_energy_eV"]
        block["method"] = r.get("method")
        block["sp"] = {"converged": True}
    elif mode == "opt":
        r_opt = opt_task.run(path, **common)
        block["energy_eV"] = r_opt["total_energy_eV"]
        block["method"] = r_opt.get("method")
        _n_atoms = r_opt.get("n_atoms")
        block["opt"] = {
            "converged": bool(r_opt.get("converged")),
            "optimized_xyz": r_opt.get("optimized_xyz"),
            "n_steps": r_opt.get("n_steps"),
            "n_atoms": _n_atoms,
        }
        # A single-atom species (e.g. atomic H in H2 -> 2 H) has no geometry to
        # relax; converged=False is vacuous, not a real failure (mirrors the
        # opt task's own zero-DOF carve-out). Don't warn for that case.
        if not r_opt.get("converged") and (_n_atoms or 0) > 1:
            block["warning"] = "opt did not converge"
    elif mode == "freq":
        # freq does its own preopt by default. Use the freq result's
        # electronic_energy_eV for ΔE and gibbs_free_energy_eV for ΔG.
        r_freq = freq_task.run(
            path, **common, temperature_K=temperature_K,
            pressure_Pa=pressure_Pa,
        )
        E = r_freq.get("electronic_energy_eV") or r_freq.get("total_energy_eV")
        block["energy_eV"] = E
        block["method"] = r_freq.get("method")
        block["enthalpy_eV"] = r_freq.get("enthalpy_eV")
        block["entropy_eV_per_K"] = r_freq.get("entropy_eV_per_K")
        block["gibbs_free_energy_eV"] = r_freq.get("gibbs_free_energy_eV")
        block["zpe_eV"] = r_freq.get("zpe_eV")
        block["n_imaginary_modes"] = r_freq.get("n_imaginary_modes")
        # Propagate the saddle/soft split so the integrity gate distinguishes a
        # genuine saddle from a floored soft torsional mode (see integrity
        # ._hard_imaginary_count).
        block["n_saddle_imaginary_modes"] = r_freq.get("n_saddle_imaginary_modes")
        block["n_soft_imaginary_modes"] = r_freq.get("n_soft_imaginary_modes")
        block["n_atoms"] = r_freq.get("n_atoms")
        block["freq"] = {
            "preopt_converged": (r_freq.get("preopt") or {}).get("converged"),
            "optimized_xyz": (r_freq.get("preopt") or {}).get("optimized_xyz"),
            "n_atoms": r_freq.get("n_atoms"),
        }
        if (r_freq.get("n_imaginary_modes") or 0) > 0:
            block["warning"] = (
                f"{r_freq['n_imaginary_modes']} imaginary mode(s) — not a "
                "true minimum; ΔG is approximate."
            )
    else:
        raise ValueError(f"Unknown mode {mode!r}")
    return block


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    *,
    reactants: List[str],
    products: List[str],
    method: str,
    mode: str = "sp",
    solvent: Optional[str] = None,
    temperature_K: float = 298.15,
    pressure_Pa: float = 101325.0,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Compute ΔE (and ΔH, ΔG if mode='freq') for reactants → products.

    Args:
      reactants, products: lists of species spec strings, see _parse_species_spec.
      method: chemkit method label (xtb / mopac / dft / hf). Same method is
              used for every species in the cycle.
      mode: 'sp' (default), 'opt', or 'freq'.
      solvent: implicit solvent (must match the backend's vocabulary).
      temperature_K, pressure_Pa: only used when mode='freq'.
    """
    if mode not in ("sp", "opt", "freq"):
        raise ValueError(f"mode must be 'sp'/'opt'/'freq', got {mode!r}")
    if not reactants:
        raise ValueError("at least one --reactant is required")
    if not products:
        raise ValueError("at least one --product is required")

    r_species = parse_species_list(reactants)
    p_species = parse_species_list(products)

    eval_kw = dict(
        mode=mode, method=method, solvent=solvent,
        temperature_K=temperature_K, pressure_Pa=pressure_Pa,
        tier=tier, functional=functional, basis=basis,
        density_fit=density_fit,
        solvent_model=solvent_model,
    )
    r_blocks = [_evaluate_species(s, **eval_kw) for s in r_species]
    p_blocks = [_evaluate_species(s, **eval_kw) for s in p_species]

    # ΔE always; ΔH/ΔG only when every species was freq'd
    def _weighted_sum(blocks, key):
        vals = [b.get(key) for b in blocks]
        if any(v is None for v in vals):
            return None
        return sum(b["coef"] * b[key] for b in blocks)

    dE_eV = _weighted_sum(p_blocks, "energy_eV") - _weighted_sum(r_blocks, "energy_eV")

    dH_eV = None
    dG_eV = None
    if mode == "freq":
        rH = _weighted_sum(r_blocks, "enthalpy_eV")
        pH = _weighted_sum(p_blocks, "enthalpy_eV")
        rG = _weighted_sum(r_blocks, "gibbs_free_energy_eV")
        pG = _weighted_sum(p_blocks, "gibbs_free_energy_eV")
        if rH is not None and pH is not None:
            dH_eV = pH - rH
        if rG is not None and pG is not None:
            dG_eV = pG - rG

    # Charge balance (warn-only — many real reactions are balanced via the
    # solvent or counter-ion but we still want this surfaced).
    r_charge = sum(s["coef"] * s["charge"] for s in r_species)
    p_charge = sum(s["coef"] * s["charge"] for s in p_species)

    # Spin balance is harder to check rigorously (multiplicity 2S+1 doesn't
    # add linearly), so we surface the totals as a hint but don't warn.

    # Atom-count balance per element. Warn if mismatched. Use a tolerance for
    # comparison since float coefficients (e.g. 1.5*X) can produce sums that
    # equal each other only to within numerical noise (0.3 + 0.3 + 0.4 != 1.0
    # exactly in IEEE 754).
    ATOM_BALANCE_TOL = 1e-6
    def _atom_counts(blocks):
        counts: Dict[str, float] = {}
        for b in blocks:
            atoms = read_geometry(b["path"])
            for s in atoms.get_chemical_symbols():
                counts[s] = counts.get(s, 0.0) + b["coef"]
        return counts
    r_atoms = _atom_counts(r_blocks)
    p_atoms = _atom_counts(p_blocks)
    all_elements = set(r_atoms) | set(p_atoms)
    atom_balance_ok = all(
        abs(r_atoms.get(el, 0.0) - p_atoms.get(el, 0.0)) < ATOM_BALANCE_TOL
        for el in all_elements
    )

    # The method label comes from one of the species' result blocks — every
    # species used the same calculator, so any of them carries the canonical
    # label string (e.g. "wb97x_v/def2-tzvp"). Build one quickly.
    canonical_method = method_label(method)
    # If we did DFT/HF, the per-species runs picked up a more specific label.
    # Pull it from one of the underlying SP/opt/freq calls via a fresh
    # build_calculator — keeps the result schema in sync with sibling tasks.
    if method in ("dft", "hf"):
        from ..calculators import build_calculator
        any_calc = build_calculator(
            method, charge=0, multiplicity=1, solvent=solvent,
            tier=tier, functional=functional, basis=basis,
            density_fit=density_fit,
            solvent_model=solvent_model,
        )
        canonical_method = method_label(method, any_calc)

    # Synthesize a base_result. "input_file" doesn't quite fit; use the first
    # reactant's path so users can find one starting point.
    first_path = r_blocks[0]["path"]
    n_atoms_total = sum(int(b["coef"] * len(read_geometry(b["path"])))
                        for b in r_blocks + p_blocks)
    # `charge`/`multiplicity` aren't meaningful for a composite reaction
    # (the cycle aggregates several species). Pass None rather than 0 — 0 is a
    # valid singlet-multiplicity value that downstream consumers may treat as
    # a real charge state. int(r_charge) would also silently truncate a float
    # total (e.g. 1.5 from 1.5*X coefficients). For the composite, the
    # per-species charges live in the `balance` block.
    result = base_result(
        task="reaction_energy",
        method=canonical_method,
        program=program_label(method),
        input_path=first_path,
        n_atoms=n_atoms_total,
        atoms=[],  # composite reaction; per-species symbols are in the blocks
        charge=None,
        multiplicity=None,
        solvent=solvent,
        cli=cli,
    )
    result["mode"] = mode
    result["temperature_K"] = temperature_K if mode == "freq" else None
    result["pressure_Pa"] = pressure_Pa if mode == "freq" else None

    result["delta_E_eV"] = dE_eV
    result["delta_E_hartree"] = dE_eV * EV_TO_HARTREE
    result["delta_E_kcal_mol"] = dE_eV * EV_TO_KCAL
    if dH_eV is not None:
        result["delta_H_eV"] = dH_eV
        result["delta_H_kcal_mol"] = dH_eV * EV_TO_KCAL
    if dG_eV is not None:
        result["delta_G_eV"] = dG_eV
        result["delta_G_kcal_mol"] = dG_eV * EV_TO_KCAL

    result["reactants"] = r_blocks
    result["products"] = p_blocks

    result["balance"] = {
        "reactant_total_charge": r_charge,
        "product_total_charge": p_charge,
        "charge_balanced": (r_charge == p_charge),
        "reactant_atom_counts": r_atoms,
        "product_atom_counts": p_atoms,
        "atom_balanced": atom_balance_ok,
    }

    warns: List[str] = []
    if r_charge != p_charge:
        warns.append(
            f"Charge imbalance: reactants total {r_charge}, products total "
            f"{p_charge}. Adjust stoichiometry or include counter-ions."
        )
    if not atom_balance_ok:
        diffs = []
        for el in set(r_atoms) | set(p_atoms):
            d = p_atoms.get(el, 0) - r_atoms.get(el, 0)
            if d != 0:
                diffs.append(f"{el}: {d:+g}")
        warns.append(
            "Atom count not balanced (products − reactants): " + ", ".join(diffs)
        )
    for b in r_blocks + p_blocks:
        if b.get("warning"):
            warns.append(f"[{b['spec']}] {b['warning']}")
    if warns:
        result["warnings"] = warns

    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)
