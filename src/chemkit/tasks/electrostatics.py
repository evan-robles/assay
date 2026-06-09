"""Molecular electrostatics: dipole, atomic partial charges, optional quadrupole.

Single-point on the supplied geometry — no optimization. For an electrostatics
analysis on a relaxed structure, run `chemkit opt` first and pass the optimized
xyz here.

xtb backend (GFN2-xTB via xtb-python):
  - Mulliken-style partial charges (xtb.singlepoint().get_charges())
  - Dipole vector in atomic units, converted to Debye

MOPAC backend (PM7):
  - ATOM_CHARGES from the .aux file (Mulliken on PM7's NDDO partitioning)
  - DIPOLE keyword adds the dipole vector + magnitude to the .out
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

import numpy as np

from ..calculators import build_calculator, apply_calc_to_atoms, MOPAC_SOLVENT_EPS
from ..io import read_geometry
from ..schema import base_result, energy_block_from_eV, element_warnings
from ._mopac_parsers import parse_mopac_extras, _parse_aux_array, _find_with_ext

# 1 ea0 = 2.541746... Debye
AU_TO_DEBYE = 2.541746229


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    cli: str = "",
) -> Dict[str, Any]:
    """Electrostatics single-point on the supplied geometry."""
    method = method.lower()
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    result = base_result(
        task="electrostatics",
        method=("GFN2-xTB" if method == "xtb" else "PM7"),
        program=method,
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=symbols,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )

    if method == "xtb":
        body = _run_xtb(atoms, charge=charge, multiplicity=multiplicity, solvent=solvent)
    elif method == "mopac":
        body = _run_mopac(atoms, symbols, charge=charge, multiplicity=multiplicity,
                          solvent=solvent)
    else:
        raise ValueError(f"Unknown method {method!r}")

    result.update(body)
    warns = element_warnings(symbols, method)
    if warns:
        existing = result.get("warnings") or []
        result["warnings"] = existing + warns
    return result


# ---------------------------------------------------------------------------
# xtb backend
# ---------------------------------------------------------------------------

def _run_xtb(atoms, *, charge: int, multiplicity: int,
             solvent: Optional[str]) -> Dict[str, Any]:
    try:
        from xtb.interface import Calculator, Param
        from xtb.libxtb import VERBOSITY_MUTED
    except ImportError as e:
        raise RuntimeError(
            "xtb-python is required for `chemkit electrostatics --method xtb`. "
            "Install with `conda install -c conda-forge xtb-python` or `pip install xtb`."
        ) from e

    numbers = np.array(atoms.get_atomic_numbers())
    positions_bohr = atoms.get_positions() * 1.8897259886
    uhf = max(0, multiplicity - 1)
    calc = Calculator(Param.GFN2xTB, numbers, positions_bohr,
                      charge=float(charge), uhf=uhf)
    calc.set_verbosity(VERBOSITY_MUTED)
    if solvent:
        from ..calculators import XTB_SOLVENT_MAP
        sol = XTB_SOLVENT_MAP.get(solvent.lower())
        if sol is None:
            raise ValueError(f"xtb: unknown solvent {solvent!r}")
        try:
            calc.set_solvent(Param.GFN2xTB, sol)
        except Exception:
            # Older xtb-python lacks set_solvent — solvent is silently dropped
            pass

    res = calc.singlepoint()
    energy_eV = res.get_energy() * 27.211386245988
    charges = res.get_charges().tolist()
    dipole_au = res.get_dipole()
    dipole_debye_vec = (dipole_au * AU_TO_DEBYE).tolist()
    dipole_debye_mag = float(np.linalg.norm(dipole_au) * AU_TO_DEBYE)

    out: Dict[str, Any] = {}
    out.update(energy_block_from_eV(energy_eV))
    out["dipole_debye"] = dipole_debye_mag
    out["dipole_vector_debye"] = dipole_debye_vec
    out["partial_charges"] = charges
    out["partial_charges_scheme"] = "Mulliken (GFN2-xTB)"
    out["sum_of_charges"] = float(sum(charges))
    return out


# ---------------------------------------------------------------------------
# MOPAC backend
# ---------------------------------------------------------------------------

def _run_mopac(atoms, symbols, *, charge: int, multiplicity: int,
               solvent: Optional[str]) -> Dict[str, Any]:
    mopac_exe = shutil.which("mopac")
    if mopac_exe is None:
        raise FileNotFoundError("mopac executable not found in PATH.")

    workdir = tempfile.mkdtemp(prefix="chemkit_elst_")
    mop_path = os.path.join(workdir, "mopac.mop")

    keywords = ["PM7", "1SCF", "AUX", "GEO-OK"]
    if charge != 0:
        keywords.append(f"CHARGE={charge}")
    if multiplicity > 1:
        names = {2: "DOUBLET", 3: "TRIPLET", 4: "QUARTET", 5: "QUINTET", 6: "SEXTET"}
        spin = names.get(multiplicity)
        if spin:
            keywords.append(spin)
        keywords.append("UHF")
    if solvent:
        eps = MOPAC_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(f"mopac: unknown solvent {solvent!r}")
        keywords.append(f"EPS={eps}")
    keywords += ["MULLIK", "THREADS=1"]

    with open(mop_path, "w") as f:
        f.write(" ".join(keywords) + "\n")
        f.write("chemkit electrostatics\n\n")
        for sym, (x, y, z) in zip(symbols, atoms.get_positions()):
            f.write(f"{sym:<3s} {x:15.8f} 1 {y:15.8f} 1 {z:15.8f} 1\n")

    subprocess.run([mopac_exe, "mopac.mop"], cwd=workdir,
                   capture_output=True, text=True, timeout=600)

    extras = parse_mopac_extras(workdir)
    # Energy: PM7 reports HoF (kcal/mol); convert
    hof = extras.get("heat_of_formation_kcal_mol")
    energy_eV = hof / 23.060547830619026 if hof is not None else None

    # Pull partial charges from AUX (ATOM_CHARGES[N])
    aux_path = _find_with_ext(workdir, ".aux")
    charges: List[float] = []
    dipole_vec_debye: Optional[List[float]] = None
    if aux_path and os.path.isfile(aux_path):
        with open(aux_path) as f:
            aux_text = f.read()
        charges = _parse_aux_array(aux_text, "ATOM_CHARGES")
        # DIPOLE vector: AUX has "DIP_VEC:DEBYE[3]=" lines on newer MOPAC builds,
        # else fall back to parsing the .out file.
        vec = _parse_aux_array(aux_text, "DIP_VEC")
        if len(vec) == 3:
            dipole_vec_debye = vec

    if dipole_vec_debye is None:
        # Fall back: parse "DIPOLE" block in .out file
        out_path = _find_with_ext(workdir, ".out")
        if out_path:
            with open(out_path) as f:
                txt = f.read()
            # The DIPOLE block looks like:
            #     DIPOLE           X         Y         Z       TOTAL
            # POINT-CHG.     ...
            # HYBRID         ...
            # SUM           x.xxxx   y.yyyy   z.zzzz   t.tttt
            m = re.search(
                r"^\s*SUM\s+([-+]?\d+\.\d+)\s+([-+]?\d+\.\d+)\s+([-+]?\d+\.\d+)\s+([-+]?\d+\.\d+)\s*$",
                txt, re.MULTILINE,
            )
            if m:
                dipole_vec_debye = [float(m.group(i)) for i in (1, 2, 3)]

    out: Dict[str, Any] = {}
    if energy_eV is not None:
        out.update(energy_block_from_eV(energy_eV))
    if hof is not None:
        out["final_heat_of_formation_kcal_mol"] = hof
    if dipole_vec_debye is not None:
        out["dipole_vector_debye"] = dipole_vec_debye
        out["dipole_debye"] = float(np.linalg.norm(dipole_vec_debye))
    elif "dipole_debye" in extras:
        out["dipole_debye"] = extras["dipole_debye"]
    if charges:
        out["partial_charges"] = charges
        out["partial_charges_scheme"] = "Mulliken (PM7)"
        out["sum_of_charges"] = float(sum(charges))
    if "ionization_potential_eV" in extras:
        out["ionization_potential_eV"] = extras["ionization_potential_eV"]
    return out
