"""pKa estimation via the thermodynamic cycle HA(aq) → A⁻(aq) + H⁺(aq).

Two modes:

  absolute   pKa = (G(A⁻,aq) + G(H⁺,aq) − G(HA,aq)) / (RT ln10)
             + standard-state correction (1 atm gas → 1 M aq)
             G(H⁺,aq) is taken from the literature (Tissandier 1998:
             ΔG_solv(H⁺) = −264.0 kcal/mol; G_gas(H⁺) = −6.28 kcal/mol;
             total G(H⁺,aq) ≈ −270.28 kcal/mol). The choice of this
             reference is the single biggest source of systematic error
             in absolute pKa; switching to the Kelly 2006 value
             (-265.9 kcal/mol) shifts every predicted pKa by ~1.4 units.

  reference  pKa(HA) = pKa(ref) +
             (G(A⁻) + G(HRef) − G(HA) − G(Ref⁻)) / (RT ln10)
             Cancels most systematic errors (basis-set + solvation-model
             biases mostly subtract). Strongly recommended over absolute.
             User must supply (a) `ref-acid` xyz, (b) `ref-base` xyz,
             (c) `--pka-ref` value, and the charges on each.

Both modes require: input HA xyz, A⁻ xyz, --solvent (defaults to water).
A full opt + freq is run in solvent on every species at the same method.

Pre-conditions on the user side:
  - HA and A⁻ xyz files must have charges differing by exactly +1/0 with the
    A⁻ being the deprotonated form. (We don't auto-build A⁻ from HA — the
    user picks which proton to remove.)
  - Multiplicities must match (closed-shell HA and closed-shell A⁻ for
    typical organic acids; this is what the defaults assume).
  - The solvent should be water for the most predictable absolute reference
    constants; the absolute mode warns if a non-water solvent is used.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional

from ..calculators import program_label, method_label, build_calculator
from ..io import read_geometry
from ..schema import base_result, EV_TO_KCAL
from . import freq as freq_task


# ---------------------------------------------------------------------------
# Thermodynamic constants (kcal/mol unless noted)
# ---------------------------------------------------------------------------

# RT ln(10) at 298.15 K, in kcal/mol. (Same constant as in solvation.py)
RT_LN10_KCAL_MOL_298K = 1.3643

# Solvated proton free energy at 298.15 K, in water.
# Tissandier 1998: ΔG_solv(H+) = -264.0 kcal/mol; with G_gas(H+) = -6.28 kcal/mol
# (Sackur-Tetrode at 298 K, 1 atm), the total is -270.28 kcal/mol.
# This is the most-cited value; Kelly/Cramer/Truhlar 2006 gives -265.9 kcal/mol
# (cluster-continuum approach). Switching adds a ~1.4 pKa-unit systematic shift.
G_HPLUS_AQUEOUS_KCAL_MOL = {
    "tissandier_1998": -270.28,
    "kelly_2006":      -265.9,
}
DEFAULT_HPLUS_REF = "tissandier_1998"

# Standard-state correction for going from 1 atm (gas-phase thermochemistry
# convention) to 1 M (aqueous-phase convention) at 298 K:
#   ΔG = RT ln(V_atm / V_1M) = RT ln(24.46) = +1.89 kcal/mol per species.
# For HA → A⁻ + H⁺ with all three species in solution at 1 M, this enters
# once (one extra mole appears on the RHS).
STANDARD_STATE_1ATM_TO_1M_KCAL_MOL = 1.89

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
        tier=tier, functional=functional, basis=basis,
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
            tier=tier, functional=functional, basis=basis,
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

    if mode == "absolute":
        G_H = G_HPLUS_AQUEOUS_KCAL_MOL[hplus_reference]
        delta_G_kcal = G_A_kcal + G_H - G_HA_kcal + STANDARD_STATE_1ATM_TO_1M_KCAL_MOL
        pka = delta_G_kcal / RT_LN10_KCAL_MOL_298K

        result["hplus_reference"] = hplus_reference
        result["G_Hplus_aq_kcal_mol"] = G_H
        result["standard_state_correction_kcal_mol"] = STANDARD_STATE_1ATM_TO_1M_KCAL_MOL
        result["delta_G_dissociation_kcal_mol"] = delta_G_kcal
        result["pKa"] = pka

        if solvent.lower() not in {"water", "h2o"}:
            warnings.append(
                f"Absolute pKa uses an aqueous G(H+) reference but solvent is "
                f"{solvent!r}; predicted pKa is not on the aqueous scale."
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
        pka = ref_pka + dG_iso_kcal / RT_LN10_KCAL_MOL_298K

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

    if warnings:
        result["warnings"] = warnings
    return result


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
        "optimized_xyz": (freq_result.get("preopt") or {}).get("optimized_xyz"),
    }
