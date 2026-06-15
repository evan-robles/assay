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
EV_TO_KCAL = HARTREE_TO_KCAL / HARTREE_TO_EV


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
