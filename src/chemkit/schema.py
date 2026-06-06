"""Shared JSON result schema used by every task."""
from __future__ import annotations
from typing import Any, Dict, List, Optional


HARTREE_TO_EV = 27.211386245988
HARTREE_TO_KCAL = 627.5094740631
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
# treat marginally.
PM7_WEAK_ELEMENTS = {"Fe", "Ru", "Os", "Co", "Rh", "Ir", "Mn", "Tc", "Re",
                     "Cr", "Mo", "W", "V", "Nb", "Ta", "Sc", "Y"}
XTB_WEAK_ELEMENTS = set()  # GFN2-xTB covers Z=1..86


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
