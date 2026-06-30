"""Shared JSON result schema used by every task."""
from __future__ import annotations
from typing import Any, Dict, List, Optional


# Physical-constant conversions — CODATA 2022 recommended values.
#   Hartree energy in eV       : 27.211 386 245 981(30) eV
#   Hartree energy in kJ/mol   : 2625.499 639 5(40) kJ/mol  -> /4.184 = kcal/mol
# Ref: Mohr, P. J.; Tiesinga, E.; Newell, D. B.; Taylor, B. N. CODATA Recommended
#   Values of the Fundamental Physical Constants: 2022. National Institute of
#   Standards and Technology. https://physics.nist.gov/cuu/Constants/
#   (accessed 2026-06-15). [verified: NIST allascii.txt 200 via curl, values read]
# (1 thermochemical calorie = 4.184 J exactly, by definition.)
HARTREE_TO_EV = 27.211386245981
HARTREE_TO_KCAL = 627.5094740629
EV_TO_HARTREE = 1.0 / HARTREE_TO_EV
EV_TO_KCAL = HARTREE_TO_KCAL / HARTREE_TO_EV   # ≈ 23.0605478... kcal/mol per eV
KCAL_TO_EV = 1.0 / EV_TO_KCAL                  # eV per kcal/mol
CAL_TO_EV = KCAL_TO_EV / 1000.0                # eV per cal/mol
# These are the SINGLE definitions of the energy-unit conversions. Every task
# imports them from here — never redefine `1/23.0605...` locally (that path
# diverges from the CODATA-derived value at ~1e-13 and reintroduces two
# constants for one quantity in a reproducibility-focused engine).


def base_result(
    *,
    task: str,
    method: str,
    program: str,
    input_path: str,
    n_atoms: int,
    atoms: List[str],
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    cli: str = "",
) -> Dict[str, Any]:
    """Construct the common header for any chemkit result."""
    return {
        "task": task,
        "method": method,
        "program": program,
        "input_file": input_path,
        "n_atoms": n_atoms,
        "atoms": atoms,
        "charge": charge,
        "multiplicity": multiplicity,
        "solvent": solvent,
        "cli_invocation": cli,
        # Task-specific keys are added by each task.
    }


def energy_block_from_eV(energy_eV: float) -> Dict[str, float]:
    """Convert an eV energy into the standard three-unit block."""
    return {
        "total_energy_eV": energy_eV,
        "total_energy_hartree": energy_eV * EV_TO_HARTREE,
        "total_energy_kcal_mol": energy_eV * EV_TO_KCAL,
    }


# ---------------------------------------------------------------------------
# Solvent tables — single documented home for all backends' solvent data.
#
# These are deliberately THREE DISTINCT tables, NOT one merged map, because the
# backends model solvation differently and their per-solvent values differ;
# merging would silently change results:
#   * xtb (ALPB) uses NAMED solvents, not a dielectric: XTB_SOLVENT_MAP maps a
#     user alias -> the ALPB solvent name xtb understands.
#   * PySCF (ddCOSMO / PCM) takes a numeric static dielectric ε. The values in
#     PYSCF_SOLVENT_EPS are the Gaussian SCRF/PCM default static dielectrics —
#     every entry matches the Gaussian solvent list exactly (water 78.3553,
#     acetonitrile 35.688, DMSO 46.826, THF 7.4257, ...). This is the
#     authoritative, higher-precision source.
#     Source: Gaussian SCRF solvent list, https://gaussian.com/scrf/
#     [verified: page HTTP 200 + all 14 chemkit ε matched the listed values,
#     2026-06-30]. Gaussian cautions ε is only one of several solvent
#     parameters, but ε is what the continuum models here consume.
#   * MOPAC (COSMO) takes ε too, but MOPAC_SOLVENT_EPS holds ROUNDED ~25 °C
#     reference dielectrics (CRC-Handbook-style, e.g. water 78.4, acetonitrile
#     37.5), NOT the Gaussian set. They are kept as-is to preserve historical
#     MOPAC-solvated results; treat them as approximate. (A future change could
#     align MOPAC to the verified Gaussian set, but that would shift existing
#     MOPAC ΔG_solv numbers, so it is intentionally NOT done here.)
# The resolver (calculators.resolve_dielectric) takes the table as a parameter,
# so co-locating them here changes no behavior.
# ---------------------------------------------------------------------------
XTB_SOLVENT_MAP = {
    # ALPB solvents understood by xtb (alias -> ALPB name)
    "water": "water", "h2o": "water",
    "methanol": "methanol", "meoh": "methanol",
    "ethanol": "ethanol", "etoh": "ethanol",
    "acetone": "acetone",
    "acetonitrile": "acetonitrile", "mecn": "acetonitrile",
    "dmso": "dmso",
    "thf": "thf",
    "dcm": "ch2cl2", "ch2cl2": "ch2cl2",
    "chloroform": "chcl3", "chcl3": "chcl3",
    "toluene": "toluene",
    "benzene": "benzene",
    "hexane": "hexane",
    "ether": "ether",
    "octanol": "octanol", "1-octanol": "octanol",
}

MOPAC_SOLVENT_EPS = {
    "water": 78.4, "h2o": 78.4,
    "methanol": 32.6, "meoh": 32.6,
    "ethanol": 24.5, "etoh": 24.5,
    "acetone": 20.7,
    "acetonitrile": 37.5, "mecn": 37.5,
    "dmso": 46.7,
    "thf": 7.58,
    "dcm": 8.93, "ch2cl2": 8.93,
    "chloroform": 4.81, "chcl3": 4.81,
    "toluene": 2.38,
    "benzene": 2.27,
    "hexane": 1.88,
    "ether": 4.33,
    "octanol": 10.30, "1-octanol": 10.30,  # 1-octanol, ε at 25 °C
}

PYSCF_SOLVENT_EPS = {
    "water": 78.3553, "h2o": 78.3553,
    "methanol": 32.613, "meoh": 32.613,
    "ethanol": 24.852, "etoh": 24.852,
    "acetone": 20.493,
    "acetonitrile": 35.688, "mecn": 35.688,
    "dmso": 46.826,
    "thf": 7.4257,
    "dcm": 8.93, "ch2cl2": 8.93,
    "chloroform": 4.7113, "chcl3": 4.7113,
    "toluene": 2.3741,
    "benzene": 2.2706,
    "hexane": 1.8819,
    "ether": 4.2400,
    "octanol": 9.8629, "1-octanol": 9.8629,
}


# Element coverage warnings — flag transition metals etc. that semi-empiricals
# treat marginally. GFN2-xTB covers Z=1..86 with no PM7-style gaps, so only
# the MOPAC/PM7 set is needed here.
PM7_WEAK_ELEMENTS = {"Fe", "Ru", "Os", "Co", "Rh", "Ir", "Mn", "Tc", "Re",
                     "Cr", "Mo", "W", "V", "Nb", "Ta", "Sc", "Y"}


def element_warnings(symbols: List[str], method: str) -> List[str]:
    warns = []
    s = set(symbols)
    if method == "mopac":
        bad = s & PM7_WEAK_ELEMENTS
        if bad:
            warns.append(
                f"PM7 has poorly validated parameters for: {sorted(bad)}. "
                "Treat absolute energies and barriers with skepticism."
            )
    return warns


SINGLE_CONFORMER_WARNING = (
    "Single-conformer result: every species was evaluated at ONE geometry, with "
    "no conformational/Boltzmann averaging. For a flexible molecule the "
    "Boltzmann-averaged free energy over conformers can differ from a single "
    "conformer by several kcal/mol — shifting pKa by >1 unit, E° by >0.1 V, and "
    "logP by >0.5. If the molecule has rotatable bonds, run conformer-search "
    "first and average, or treat this as a screening estimate tied to the input "
    "geometry."
)


def scf_convergence_warnings(method: str, extras: Optional[Dict[str, Any]]) -> List[str]:
    """Promote a non-converged PySCF SCF to a prominent top-level warning.

    The PySCF backend always stashes `scf_converged` (and `scf_cycles`) into the
    extras dict returned by `collect_calc_extras` / `pack_scf_result`. The
    ASE-driven calculator path (PySCFCalculator) returns the last-iteration
    energy even when the SCF did NOT converge, with no flag promoted above
    `code_specific` — so a non-converged DFT/HF energy otherwise reads exactly
    like a converged one. calculation-reporting-standards #6/#7 require the
    non-convergence be surfaced loudly, next to the affected value. This helper
    turns the already-computed flag into a top-level warning string.

    Only applies to dft/hf (PySCF). Returns [] for xtb/mopac, when extras is
    missing the flag, or when the SCF converged.
    """
    if (method or "").lower() not in ("dft", "hf"):
        return []
    if not extras:
        return []
    # `scf_converged` may sit either directly in extras or nested under a
    # `code_specific` block, depending on whether the caller passed the raw
    # collect_calc_extras() dict or an assembled result.
    converged = extras.get("scf_converged")
    cycles = extras.get("scf_cycles")
    if converged is None and isinstance(extras.get("code_specific"), dict):
        cs = extras["code_specific"]
        converged = cs.get("scf_converged")
        cycles = cs.get("scf_cycles")
    if converged is None:
        # No flag available — say nothing rather than guess.
        return []
    if converged:
        return []
    cyc = f" after {cycles} cycles" if cycles else ""
    return [
        f"{(method or '').upper()} SCF did NOT converge{cyc}; the reported energy "
        "is the last-iteration value and is UNRELIABLE. Tighten the geometry, "
        "raise --max-cycle, or try a different tier before trusting this number."
    ]
