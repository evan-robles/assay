#!/usr/bin/env python3
"""redox-potential — self-contained assay skill.

One- or multi-electron oxidation/reduction potential against a chosen reference electrode.

Owns its workflow; depends only on the shared `assay_core` physics
library (composites use sibling skills in-process via their task shims).
Runnable stand-alone via scripts/run.py.
"""
from __future__ import annotations

import os as _os, sys as _sys
# Ensure the repo root is importable so `from skills.<sibling>...` resolves when
# this file is run directly as a script (python skills/<pkg>/scripts/run.py),
# where only the script's own dir is on sys.path.
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

# Skill discovery manifest (read by assay_core.discovery / the MCP server).
SKILL_NAME = "redox-potential"      # kebab display name (matches SKILL.md frontmatter)
SUBCOMMAND = "redox"      # engine subcommand this skill implements
import os
from typing import Any, Dict, Optional

from skills.single_point_energy.scripts import run as sp_task
from skills.geometry_optimize.scripts import run as opt_task
from skills.vibrational_analysis.scripts import run as freq_task
from assay_core.calculators import program_label
from assay_core.io import read_geometry
from assay_core.integrity import finalize
from assay_core.schema import base_result, EV_TO_KCAL, SINGLE_CONFORMER_WARNING

VALID_MODES = ("adiabatic", "vertical", "freq")

# Absolute potential of the Standard Hydrogen Electrode, used to convert a
# computed absolute electrode potential to the conventional SHE scale:
#     E°(vs SHE) = E_abs(computed) − E_abs(SHE).
# IUPAC-recommended value (Trasatti 1986): 4.44 V at 298.15 K with a stated
# uncertainty, but the widely used "4.28 V" arises from a different surface-
# potential convention. The literature therefore spans ~4.28–4.44 V depending
# on convention, and that ~0.16 V spread is a SYSTEMATIC offset on every
# absolute redox potential — larger than many of the chemical effects studied.
# We keep 4.281 V as the default (the convention chemkit has used) but expose it
# as an override (`e_abs_she`) and surface the chosen value in every result so
# the convention is auditable.
# Ref: Trasatti, S. The Absolute Electrode Potential: An Explanatory Note
#   (Recommendations 1986). Pure Appl. Chem. 1986, 58, 955–966.
#   https://doi.org/10.1351/pac198658070955.
#   [verified: DOI 202 via curl + Crossref title/year/journal match, 2026-06-15]
DEFAULT_E_ABS_SHE_V = 4.281

# Offsets of common reference electrodes vs SHE (aqueous, 298 K). These are
# SOLVENT-DEPENDENT — Fc+/Fc in particular shifts substantially between solvents
# (it is an IUPAC-recommended internal reference precisely because its absolute
# value is reported per-solvent). The offsets below are aqueous approximations;
# redox warns when a non-SHE reference is combined with a non-aqueous solvent.
#   Ag/AgCl (sat. KCl) ≈ +0.197–0.222 V vs SHE; we use 0.222 (3 M KCl).
#   Fc+/Fc ≈ +0.40 V vs SHE in water is only approximate; in MeCN it is ~+0.40 V
#   vs SHE by a different convention. Treat as a rough anchor, not a precise tie.
REFERENCE_OFFSETS_VS_SHE_V = {
    "SHE": 0.0,
    "Ag/AgCl": 0.222,
    "Fc+/Fc": 0.40,
}


def _reference_potentials(e_abs_she: float) -> Dict[str, float]:
    """Build the absolute reference potentials from a chosen E_abs(SHE)."""
    return {
        ref: e_abs_she + off for ref, off in REFERENCE_OFFSETS_VS_SHE_V.items()
    }


# Back-compat: a module-level table at the default convention (some callers /
# tests import this name). Prefer _reference_potentials(e_abs_she) internally.
REFERENCE_POTENTIALS_V = _reference_potentials(DEFAULT_E_ABS_SHE_V)


def run(
    input_path: str,
    *,
    method: str,
    oxidized_charge: int,
    reduced_charge: int,
    oxidized_multiplicity: int = 1,
    reduced_multiplicity: int = 2,
    solvent: Optional[str] = None,
    reference: str = "SHE",
    n_electrons: int = 1,
    mode: str = "adiabatic",
    e_abs_she: float = DEFAULT_E_ABS_SHE_V,
    fmax: float = 0.05,
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
    reference_potentials = _reference_potentials(e_abs_she)
    if reference not in reference_potentials:
        raise ValueError(
            f"Unknown reference {reference!r}. "
            f"Choose from: {list(reference_potentials)}"
        )
    mode = (mode or "adiabatic").lower()
    if mode not in VALID_MODES:
        raise ValueError(
            f"Unknown redox mode {mode!r}. Choose from: {list(VALID_MODES)} "
            "(adiabatic = relax each state then ΔE [default]; vertical = ΔE at "
            "the input geometry; freq = full ΔG with opt+freq)."
        )

    # The reduction Ox + n e⁻ → Red implies reduced_charge − oxidized_charge = −n.
    # Reject obvious mismatches: passing n_electrons=2 with Δcharge=−1 (or any
    # combination where they disagree) produces a meaningless E°.
    expected_dq = -int(n_electrons)
    actual_dq = int(reduced_charge) - int(oxidized_charge)
    if actual_dq != expected_dq:
        raise ValueError(
            f"redox: n_electrons={n_electrons} but reduced_charge - oxidized_charge "
            f"= {actual_dq} (expected {expected_dq}). The reduced form must have "
            f"exactly n more electrons than the oxidized form (one less unit of charge "
            "per electron added)."
        )
    # Spin parity: each unpaired-electron count changes by ±1 per added electron,
    # so the multiplicities must differ by exactly n_electrons modulo 2.
    expected_parity = n_electrons % 2
    actual_parity = abs(int(reduced_multiplicity) - int(oxidized_multiplicity)) % 2
    if actual_parity != expected_parity:
        raise ValueError(
            f"redox: |reduced_mult - oxidized_mult| has parity {actual_parity}, "
            f"expected {expected_parity} for {n_electrons}-electron transfer. "
            "Adding n electrons flips spin parity n times; check that your "
            "ox/red multiplicities are consistent (e.g. neutral singlet ↔ "
            "anion-radical doublet for n=1)."
        )

    # Acquire the per-state energy (and any sub-warnings) according to the mode.
    # `_state_energy_eV` returns (energy_eV, energy_kind, state_block, warnings).
    ox_E, energy_kind, ox_block, ox_warns = _state_energy_eV(
        input_path, method=method, charge=oxidized_charge,
        multiplicity=oxidized_multiplicity, solvent=solvent, mode=mode, fmax=fmax,
        temperature_K=temperature_K, pressure_Pa=pressure_Pa, cli=cli,
        tier=tier, functional=functional, basis=basis, density_fit=density_fit,
        solvent_model=solvent_model,
        label="oxidized",
    )
    red_E, _, red_block, red_warns = _state_energy_eV(
        input_path, method=method, charge=reduced_charge,
        multiplicity=reduced_multiplicity, solvent=solvent, mode=mode, fmax=fmax,
        temperature_K=temperature_K, pressure_Pa=pressure_Pa, cli=cli,
        tier=tier, functional=functional, basis=basis, density_fit=density_fit,
        solvent_model=solvent_model,
        label="reduced",
    )

    delta_eV = red_E - ox_E
    # E°(red/ox) = -(ΔG/nF) - E_ref(abs). With energies in eV and the Faraday
    # cancellation, (ΔG in eV)/n = volts directly.
    E_redox_V = -(delta_eV / n_electrons) - reference_potentials[reference]

    atoms = read_geometry(input_path)
    result = base_result(
        task="redox_potential",
        method=ox_block.get("method", method),
        program=program_label(method),
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=atoms.get_chemical_symbols(),
        charge=oxidized_charge, multiplicity=oxidized_multiplicity,
        solvent=solvent, cli=cli,
    )
    # Surface the mode prominently — it determines what the number means.
    result["mode"] = mode
    result["energy_kind"] = energy_kind  # "ΔE (relaxed)" / "ΔE (vertical)" / "ΔG"
    result["redox_potential_V_vs_" + reference] = E_redox_V
    # Name the difference by what it actually is, so a vertical ΔE is never read
    # as a relaxed one or as a ΔG.
    diff_key = "delta_G_redox" if mode == "freq" else "delta_E_redox"
    result[diff_key + "_eV"] = delta_eV
    result[diff_key + "_kcal_mol"] = delta_eV * EV_TO_KCAL
    result["n_electrons"] = n_electrons
    result["reference_potential_V_abs"] = reference_potentials[reference]
    result["e_abs_she_V"] = e_abs_she
    result["reference_electrode"] = reference
    result["oxidized_state"] = {
        "charge": oxidized_charge, "multiplicity": oxidized_multiplicity,
        **ox_block,
    }
    result["reduced_state"] = {
        "charge": reduced_charge, "multiplicity": reduced_multiplicity,
        **red_block,
    }

    # Mode-specific honesty about what was (and wasn't) included.
    if mode == "vertical":
        mode_warn = (
            "mode=vertical: both oxidation states evaluated as single points on "
            "the SAME input geometry. This is a VERTICAL ΔE (Franck-Condon), NOT "
            "an equilibrium E° — it omits geometric reorganization on electron "
            "transfer. Use mode=adiabatic (default) or mode=freq for an "
            "equilibrium potential."
        )
    elif mode == "adiabatic":
        mode_warn = (
            "mode=adiabatic: each oxidation state was geometry-optimized "
            "separately and the potential uses ΔE between the relaxed geometries "
            "(includes reorganization). It does NOT include ZPE/thermal/entropy "
            "(ΔE used in place of ΔG); use mode=freq for a true ΔG."
        )
    else:  # freq
        mode_warn = (
            "mode=freq: potential uses ΔG (electronic + ZPE + thermal − TΔS) from "
            "an opt+freq on each state. Note the freq thermochemistry uses "
            "gas-phase ideal-gas rotational/translational entropy even under an "
            "implicit solvent (see per-state warnings)."
        )
    common_warn = (
        "Solvation is implicit/continuum only (or gas phase) — no explicit "
        "solvent shell and no Born–Haber solvation cycle. Semi-empirical redox "
        "potentials are screening-grade (≈±0.3–0.5 V); even DFT here omits the "
        "explicit-solvation correction. Treat as a screening estimate."
    )
    ref_warns = []
    # The absolute SHE potential is convention-dependent (≈4.28–4.44 V); the
    # chosen value is a systematic offset on every potential reported here.
    ref_warns.append(
        f"E°(vs {reference}) uses E_abs(SHE) = {e_abs_she:.3f} V. The absolute SHE "
        "potential is convention-dependent (literature ≈4.28–4.44 V); this is a "
        "systematic offset on the reported potential. Override with --e-abs-she "
        "to match your reference convention."
    )
    # Reference-electrode offsets are aqueous; flag non-aqueous + non-SHE.
    aqueous = (solvent or "").lower() in ("", "water", "h2o")
    if reference != "SHE" and not aqueous:
        ref_warns.append(
            f"Reference {reference} uses an AQUEOUS offset vs SHE "
            f"({REFERENCE_OFFSETS_VS_SHE_V[reference]:+.3f} V), but the solvent is "
            f"{solvent!r}. Reference-electrode potentials (especially Fc+/Fc) are "
            "solvent-dependent; the reported value carries an unquantified offset "
            "error. Report vs SHE, or supply a solvent-appropriate offset."
        )
    result["warnings"] = (
        result.get("warnings", [])
        + [mode_warn, common_warn] + ref_warns + [SINGLE_CONFORMER_WARNING]
        + ox_warns + red_warns
    )

    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def _state_energy_eV(
    input_path, *, method, charge, multiplicity, solvent, mode, fmax,
    temperature_K, pressure_Pa, cli, tier, functional, basis, density_fit,
    solvent_model, label,
):
    """Compute the energy used for one oxidation state under `mode`.

    Returns (energy_eV, energy_kind_str, state_block_dict, warnings_list).
    The state block carries the method label, the energy, the geometry source,
    and (for freq) the ΔG components, so the result JSON documents exactly what
    each state contributed. Per-state non-convergence/freq warnings are prefixed
    with the state label and bubbled up rather than swallowed.
    """
    warns = []
    if mode == "vertical":
        st = sp_task.run(
            input_path, method=method, charge=charge, multiplicity=multiplicity,
            solvent=solvent, cli=cli, tier=tier, functional=functional, basis=basis,
            density_fit=density_fit,
            solvent_model=solvent_model,
            gate_integrity=False,
        )
        for w in st.get("warnings", []) or []:
            warns.append(f"[{label}] {w}")
        block = {
            "method": st.get("method", method),
            "energy_eV": st["total_energy_eV"],
            "geometry_source": "input geometry (no relaxation)",
            "n_atoms": st.get("n_atoms"),
        }
        return st["total_energy_eV"], "ΔE (vertical, same geometry)", block, warns

    if mode == "adiabatic":
        st = opt_task.run(
            input_path, method=method, charge=charge, multiplicity=multiplicity,
            solvent=solvent, fmax=fmax, cli=cli,
            tier=tier, functional=functional, basis=basis,
            density_fit=density_fit,
            solvent_model=solvent_model,
            gate_integrity=False,
        )
        for w in st.get("warnings", []) or []:
            warns.append(f"[{label}] {w}")
        # A single-atom state (e.g. H / H⁺) has no geometry to relax — MOPAC/ASE
        # report converged=False vacuously. Don't raise a misleading
        # "did NOT converge / unreliable" alarm for that zero-DOF case.
        _zero_dof = (st.get("n_atoms") or 0) <= 1
        if not st.get("converged", False) and not _zero_dof:
            warns.append(
                f"[{label}] geometry optimization did NOT converge; the relaxed "
                "energy (and hence the redox potential) is unreliable."
            )
        block = {
            "method": st.get("method", method),
            "energy_eV": st["total_energy_eV"],
            "geometry_source": "relaxed at this method",
            "optimized_xyz": st.get("optimized_xyz"),
            "converged": st.get("converged"),
            "n_atoms": st.get("n_atoms"),
        }
        return st["total_energy_eV"], "ΔE (relaxed/adiabatic)", block, warns

    # mode == "freq": full ΔG (opt+freq inside the freq task).
    st = freq_task.run(
        input_path, method=method, charge=charge, multiplicity=multiplicity,
        solvent=solvent, temperature_K=temperature_K, pressure_Pa=pressure_Pa,
        preopt=True, preopt_fmax=fmax, cli=cli,
        tier=tier, functional=functional, basis=basis,
        density_fit=density_fit,
        solvent_model=solvent_model,
        gate_integrity=False,
    )
    for w in st.get("warnings", []) or []:
        warns.append(f"[{label}] {w}")
    g_eV = st.get("gibbs_free_energy_eV")
    if g_eV is None:
        raise RuntimeError(
            f"redox mode=freq: freq task did not return gibbs_free_energy_eV for "
            f"the {label} state."
        )
    block = {
        "method": st.get("method", method),
        "gibbs_free_energy_eV": g_eV,
        "electronic_energy_eV": st.get("electronic_energy_eV"),
        "zpe_eV": st.get("zpe_eV"),
        "geometry_source": "relaxed at this method (opt+freq)",
        "n_imaginary_modes": st.get("n_imaginary_modes"),
        # Propagate the saddle/soft split so the integrity gate can distinguish a
        # genuine saddle from a floored soft torsional mode (see integrity
        # ._hard_imaginary_count / _check_pka).
        "n_saddle_imaginary_modes": st.get("n_saddle_imaginary_modes"),
        "n_soft_imaginary_modes": st.get("n_soft_imaginary_modes"),
        "n_atoms": st.get("n_atoms"),
    }
    return g_eV, "ΔG (opt+freq)", block, warns


def build_parser():
    import argparse
    from assay_core import argkit
    p = argparse.ArgumentParser(prog="redox-potential",
        description="Redox potential vs SHE / Ag-AgCl / Fc+-Fc.")
    argkit._add_chem_options(p)
    p.add_argument("--ox-charge", type=int, required=True)
    p.add_argument("--red-charge", type=int, required=True)
    p.add_argument("--ox-mult", type=int, default=1)
    p.add_argument("--red-mult", type=int, default=2)
    p.add_argument("--ref", type=argkit._norm_redox_ref,
                   choices=["SHE", "Ag/AgCl", "Fc+/Fc"], default="SHE")
    p.add_argument("--n-electrons", type=int, default=1)
    p.add_argument("--mode", type=argkit._norm_mode,
                   choices=["adiabatic", "vertical", "freq"], default="adiabatic")
    p.add_argument("--fmax", type=float, default=0.05,
                   help="Force convergence (eV/Å) for per-state opt in adiabatic/freq.")
    p.add_argument("--temperature", type=float, default=298.15)
    p.add_argument("--pressure", type=float, default=101325.0)
    p.add_argument("--e-abs-she", type=float, default=None,
                   help="Absolute potential of SHE in volts (default 4.281).")
    return p


def _call_run(args, cli, pyscf_kwargs):
    extra = {"e_abs_she": args.e_abs_she} if args.e_abs_she is not None else {}
    return run(args.input, method=args.method,
               oxidized_charge=args.ox_charge, reduced_charge=args.red_charge,
               oxidized_multiplicity=args.ox_mult, reduced_multiplicity=args.red_mult,
               solvent=args.solvent, reference=args.ref, n_electrons=args.n_electrons,
               mode=args.mode, fmax=args.fmax, temperature_K=args.temperature,
               pressure_Pa=args.pressure, cli=cli, **extra, **pyscf_kwargs)


if __name__ == "__main__":
    from assay_core import argkit
    raise SystemExit(argkit.run_cli(build_parser(), run, task="redox",
                                    call_run=_call_run))
