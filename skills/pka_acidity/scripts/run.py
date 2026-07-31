#!/usr/bin/env python3
"""pka-acidity — self-contained assay skill.

Aqueous pKa of an acid HA from a thermodynamic cycle, absolute or reference-anchored.

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
SKILL_NAME = "pka-acidity"      # kebab display name (matches SKILL.md frontmatter)
SUBCOMMAND = "pka"      # engine subcommand this skill implements
import os
from typing import Any, Dict, List, Optional

from assay_core.calculators import program_label, method_label, build_calculator
from assay_core.io import read_geometry
from assay_core.integrity import finalize
from assay_core.schema import base_result, EV_TO_KCAL, SINGLE_CONFORMER_WARNING
from skills.vibrational_analysis.scripts import run as freq_task


# ---------------------------------------------------------------------------
# Thermodynamic constants (kcal/mol unless noted)
# ---------------------------------------------------------------------------

import math

# Gas constant in kcal/(mol·K) — used to scale RT ln 10 and the 1 atm → 1 M
# correction at user-supplied temperatures.
# CODATA 2022 (exact): R = 8.314 462 618 J/(mol·K); /4184 = 1.987 204 258e-3
# kcal/(mol·K). Value unchanged (R is an exact SI-defined constant).
# Ref: Mohr, Tiesinga, Newell, Taylor, CODATA 2022, NIST,
# https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15).
R_KCAL_MOL_K = 1.987204258e-3
# Molar volume of an ideal gas at 1 atm, in L/mol per K: V_m = (R/P)*T.
# Using R = 0.08205736 L·atm/(mol·K) so V_m(T) [L/mol] = R_LATM * T.
R_LATM_MOL_K = 0.08205736

# At 298.15 K these give 1.3643 and 1.894 kcal/mol — matching the historical
# constants used elsewhere in chemkit.
def rt_ln10_kcal_mol(T_K: float) -> float:
    """RT ln 10 at temperature T, in kcal/mol. The pKa denominator."""
    return R_KCAL_MOL_K * T_K * math.log(10.0)

def standard_state_1atm_to_1m_kcal_mol(T_K: float) -> float:
    """RT ln(V_m(T) / 1 L·mol⁻¹) — the correction for switching one species
    from the gas-phase 1 atm convention to the aqueous 1 M convention."""
    Vm_L = R_LATM_MOL_K * T_K          # ≈ 24.466 L/mol at 298.15 K
    return R_KCAL_MOL_K * T_K * math.log(Vm_L)

# Solvated proton free energy in water. Both reference values are tabulated at
# 298.15 K. Temperature scaling of G(H+,aq) is non-trivial (it involves
# d(ΔG_solv)/dT and the Sackur-Tetrode entropy of the gas-phase proton); we
# warn the user when temperature_K deviates and let them apply their own
# correction rather than silently using a 298 K number at 350 K.
G_HPLUS_AQUEOUS_KCAL_MOL = {
    "tissandier_1998": -270.28,
    "kelly_2006":      -265.9,
}
DEFAULT_HPLUS_REF = "tissandier_1998"

# Common reference acids — used when the user picks --mode reference and
# wants a "pick a sensible default" path. Experimental pKa in water at 298 K.
# Only included for documentation / sanity-check; actual --mode reference
# requires the user to supply --pka-ref since they're the one who must also
# provide the xyz files.
REFERENCE_ACIDS_KNOWN_PKA = {
    "acetic_acid":    4.76,
    "formic_acid":    3.75,
    "phenol":         9.99,
    "methanol":      15.5,
    "ammonium":       9.25,   # NH4+ → NH3 + H+
    "water":         15.7,    # H2O → OH- + H+
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    *,
    ha_xyz: str,
    a_minus_xyz: str,
    method: str,
    mode: str = "absolute",
    solvent: str = "water",
    ha_charge: int = 0,
    ha_multiplicity: int = 1,
    a_minus_charge: Optional[int] = None,   # default: ha_charge − 1
    a_minus_multiplicity: int = 1,
    temperature_K: float = 298.15,
    pressure_Pa: float = 101325.0,
    hplus_reference: str = DEFAULT_HPLUS_REF,
    # reference-mode args
    ref_ha_xyz: Optional[str] = None,
    ref_a_minus_xyz: Optional[str] = None,
    ref_pka: Optional[float] = None,
    ref_ha_charge: int = 0,
    ref_ha_multiplicity: int = 1,
    ref_a_minus_charge: Optional[int] = None,
    ref_a_minus_multiplicity: int = 1,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Compute pKa from a thermodynamic cycle.

    `ha_charge` and `a_minus_charge` must differ by exactly +1: removing one
    proton (H+, +1 charge) drops the molecular charge by 1. If
    `a_minus_charge` is None, we default to `ha_charge - 1`.
    """
    if mode not in ("absolute", "reference"):
        raise ValueError(f"mode must be 'absolute' or 'reference', got {mode!r}")
    if a_minus_charge is None:
        a_minus_charge = ha_charge - 1
    if a_minus_charge != ha_charge - 1:
        raise ValueError(
            f"a_minus_charge ({a_minus_charge}) must equal ha_charge ({ha_charge}) − 1; "
            "the deprotonated form has one less proton (charge drops by 1)."
        )

    if mode == "reference":
        if not (ref_ha_xyz and ref_a_minus_xyz and ref_pka is not None):
            raise ValueError(
                "mode='reference' requires --ref-ha, --ref-a-minus, and --pka-ref."
            )
        if ref_a_minus_charge is None:
            ref_a_minus_charge = ref_ha_charge - 1

    if hplus_reference not in G_HPLUS_AQUEOUS_KCAL_MOL:
        raise ValueError(
            f"Unknown hplus_reference {hplus_reference!r}. Options: "
            f"{sorted(G_HPLUS_AQUEOUS_KCAL_MOL)}"
        )

    # Run the four (or two) opt+freq calculations.
    common_kw = dict(
        method=method, solvent=solvent,
        temperature_K=temperature_K, pressure_Pa=pressure_Pa,
        tier=tier, functional=functional, basis=basis, density_fit=density_fit,
        solvent_model=solvent_model,
        gate_integrity=False,  # sub-calls stamp only; pka gates the whole result
    )

    ha_res = freq_task.run(
        ha_xyz, charge=ha_charge, multiplicity=ha_multiplicity,
        cli="(internal pka: HA)", **common_kw,
    )
    a_res = freq_task.run(
        a_minus_xyz, charge=a_minus_charge, multiplicity=a_minus_multiplicity,
        cli="(internal pka: A-)", **common_kw,
    )

    # Pull G(HA, aq) and G(A-, aq) — eV → kcal/mol.
    G_HA_kcal  = ha_res["gibbs_free_energy_eV"] * EV_TO_KCAL
    G_A_kcal   = a_res["gibbs_free_energy_eV"]  * EV_TO_KCAL

    species_blocks = {
        "HA": _species_summary(ha_xyz, ha_res, ha_charge, ha_multiplicity),
        "A_minus": _species_summary(a_minus_xyz, a_res, a_minus_charge, a_minus_multiplicity),
    }

    canonical_method = method_label(method)
    if method in ("dft", "hf"):
        any_calc = build_calculator(
            method, charge=0, multiplicity=1, solvent=solvent,
            tier=tier, functional=functional, basis=basis, density_fit=density_fit,
            solvent_model=solvent_model,
        )
        canonical_method = method_label(method, any_calc)

    result = base_result(
        task="pka",
        method=canonical_method,
        program=program_label(method),
        input_path=os.path.abspath(ha_xyz),
        n_atoms=len(read_geometry(ha_xyz)),
        atoms=read_geometry(ha_xyz).get_chemical_symbols(),
        charge=ha_charge,
        multiplicity=ha_multiplicity,
        solvent=solvent,
        cli=cli,
    )
    result["mode"] = mode
    result["temperature_K"] = temperature_K
    result["pressure_Pa"] = pressure_Pa
    result["G_HA_kcal_mol"] = G_HA_kcal
    result["G_A_minus_kcal_mol"] = G_A_kcal

    warnings: List[str] = []

    rt_ln10 = rt_ln10_kcal_mol(temperature_K)
    ss_corr = standard_state_1atm_to_1m_kcal_mol(temperature_K)

    if mode == "absolute":
        G_H = G_HPLUS_AQUEOUS_KCAL_MOL[hplus_reference]
        delta_G_kcal = G_A_kcal + G_H - G_HA_kcal + ss_corr
        pka = delta_G_kcal / rt_ln10

        result["hplus_reference"] = hplus_reference
        result["G_Hplus_aq_kcal_mol"] = G_H
        result["standard_state_correction_kcal_mol"] = ss_corr
        result["RT_ln10_kcal_mol"] = rt_ln10
        result["delta_G_dissociation_kcal_mol"] = delta_G_kcal
        result["pKa"] = pka

        if solvent.lower() not in {"water", "h2o"}:
            warnings.append(
                f"Absolute pKa uses an aqueous G(H+) reference but solvent is "
                f"{solvent!r}; predicted pKa is not on the aqueous scale."
            )
        if abs(temperature_K - 298.15) > 0.1:
            warnings.append(
                f"G(H+,aq) reference {hplus_reference!r} is tabulated at 298.15 K "
                f"but temperature_K={temperature_K:.2f}; the RT-dependent factors "
                "are scaled but the H+ reference itself is not. Add your own "
                "ΔG(H+,aq) temperature correction if you need T ≠ 298 K."
            )
        warnings.append(
            "Absolute pKa is highly sensitive to the G(H+,aq) reference "
            "(~1.4 unit shift between Tissandier 1998 and Kelly 2006). "
            "Prefer mode='reference' against a known acid in the same family."
        )

    else:  # reference mode
        ref_ha_res = freq_task.run(
            ref_ha_xyz, charge=ref_ha_charge, multiplicity=ref_ha_multiplicity,
            cli="(internal pka: ref_HA)", **common_kw,
        )
        ref_a_res = freq_task.run(
            ref_a_minus_xyz, charge=ref_a_minus_charge, multiplicity=ref_a_minus_multiplicity,
            cli="(internal pka: ref_A-)", **common_kw,
        )
        G_ref_HA_kcal = ref_ha_res["gibbs_free_energy_eV"] * EV_TO_KCAL
        G_ref_A_kcal  = ref_a_res["gibbs_free_energy_eV"]  * EV_TO_KCAL

        # Isodesmic correction: HA + Ref⁻ → A⁻ + HRef
        # ΔG_iso = G(A⁻) + G(HRef) − G(HA) − G(Ref⁻)
        # pKa(HA) = pKa(Ref) + ΔG_iso / (RT ln10)
        # No standard-state correction needed: same number of moles on both sides.
        dG_iso_kcal = (G_A_kcal + G_ref_HA_kcal) - (G_HA_kcal + G_ref_A_kcal)
        pka = ref_pka + dG_iso_kcal / rt_ln10
        result["RT_ln10_kcal_mol"] = rt_ln10

        species_blocks["ref_HA"] = _species_summary(
            ref_ha_xyz, ref_ha_res, ref_ha_charge, ref_ha_multiplicity,
        )
        species_blocks["ref_A_minus"] = _species_summary(
            ref_a_minus_xyz, ref_a_res, ref_a_minus_charge, ref_a_minus_multiplicity,
        )
        result["G_ref_HA_kcal_mol"] = G_ref_HA_kcal
        result["G_ref_A_minus_kcal_mol"] = G_ref_A_kcal
        result["reference_pka"] = ref_pka
        result["delta_G_isodesmic_kcal_mol"] = dG_iso_kcal
        result["pKa"] = pka

    result["species"] = species_blocks

    # Surface any imaginary modes from the underlying freq runs.
    for label, blk in species_blocks.items():
        n_imag = blk.get("n_imaginary_modes") or 0
        if n_imag > 0:
            warnings.append(
                f"{label}: {n_imag} imaginary mode(s) — not a true minimum; "
                "pKa is approximate."
            )

    warnings.append(SINGLE_CONFORMER_WARNING)

    if warnings:
        result["warnings"] = warnings

    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def _species_summary(xyz_path, freq_result, charge, mult) -> Dict[str, Any]:
    return {
        "input_file": os.path.abspath(xyz_path),
        "charge": charge,
        "multiplicity": mult,
        "G_kcal_mol": freq_result["gibbs_free_energy_eV"] * EV_TO_KCAL,
        "H_kcal_mol": (freq_result.get("enthalpy_eV") or 0.0) * EV_TO_KCAL,
        "E_kcal_mol": (freq_result.get("electronic_energy_eV") or 0.0) * EV_TO_KCAL,
        "ZPE_kcal_mol": freq_result.get("zpe_kcal_mol"),
        "n_imaginary_modes": freq_result.get("n_imaginary_modes"),
        # n_saddle = genuine (hard, |nu| > 50i) imaginary modes. The total
        # n_imaginary_modes also counts soft sub-50i rotor modes that the freq
        # task floors as real low-frequency vibrations; those do NOT mean the
        # species is a non-minimum, so the integrity gate keys on the saddle count.
        "n_saddle_imaginary_modes": freq_result.get("n_saddle_imaginary_modes"),
        "n_soft_imaginary_modes": freq_result.get("n_soft_imaginary_modes"),
        "optimized_xyz": (freq_result.get("preopt") or {}).get("optimized_xyz"),
    }


def build_parser():
    import argparse
    from assay_core import argkit
    p = argparse.ArgumentParser(prog="pka-acidity",
        description="pKa via the HA(aq) → A⁻(aq) + H⁺(aq) thermodynamic cycle.")
    p.add_argument("--ha", required=True, help="xyz of the protonated form (HA).")
    p.add_argument("--a-minus", dest="a_minus", required=True,
                   help="xyz of the deprotonated form (A⁻).")
    p.add_argument("--method", type=argkit._norm_method,
                   choices=["xtb", "mopac", "dft", "hf"], required=True,
                   help="Same method is applied to every species in the cycle.")
    p.add_argument("--mode", type=argkit._norm_mode,
                   choices=["absolute", "reference"], default="absolute")
    p.add_argument("--solvent", default="water",
                   help="Implicit solvent (default 'water').")
    p.add_argument("--ha-charge", type=int, default=0)
    p.add_argument("--ha-mult", type=int, default=1)
    p.add_argument("--a-minus-mult", type=int, default=1)
    p.add_argument("--temperature", type=float, default=298.15)
    p.add_argument("--pressure", type=float, default=101325.0)
    p.add_argument("--hplus-reference", default="tissandier_1998",
                   choices=["tissandier_1998", "kelly_2006"])
    p.add_argument("--ref-ha", default=None)
    p.add_argument("--ref-a-minus", default=None)
    p.add_argument("--pka-ref", type=float, default=None)
    p.add_argument("--ref-ha-charge", type=int, default=0)
    p.add_argument("--ref-ha-mult", type=int, default=1)
    p.add_argument("--ref-a-minus-mult", type=int, default=1)
    p.add_argument("--tier", type=argkit._norm_tier,
                   choices=["fast", "standard", "accurate"], default=None)
    p.add_argument("--functional", default=None)
    p.add_argument("--basis", default=None)
    p.add_argument("--density-fit", dest="density_fit", action="store_true", default=False)
    p.add_argument("--out", default=None)
    p.add_argument("--accept-defaults", dest="accept_defaults", action="store_true")
    argkit._add_stdout_option(p)
    argkit._add_gate_option(p)
    return p


def _call_run(args, cli, pyscf_kwargs):
    return run(ha_xyz=args.ha, a_minus_xyz=args.a_minus, method=args.method,
               mode=args.mode, solvent=args.solvent, ha_charge=args.ha_charge,
               ha_multiplicity=args.ha_mult, a_minus_multiplicity=args.a_minus_mult,
               temperature_K=args.temperature, pressure_Pa=args.pressure,
               hplus_reference=args.hplus_reference, ref_ha_xyz=args.ref_ha,
               ref_a_minus_xyz=args.ref_a_minus, ref_pka=args.pka_ref,
               ref_ha_charge=args.ref_ha_charge, ref_ha_multiplicity=args.ref_ha_mult,
               ref_a_minus_multiplicity=args.ref_a_minus_mult, cli=cli, **pyscf_kwargs)


def _resolve_out(args, result):
    import os
    from assay_core import argkit
    if getattr(args, "out", None):
        return args.out
    return argkit._default_out(args.ha, "pka", args.method)


if __name__ == "__main__":
    from assay_core import argkit
    raise SystemExit(argkit.run_cli(build_parser(), run, task="pka",
                                    call_run=_call_run, resolve_out=_resolve_out))
