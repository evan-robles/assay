"""MOPAC .out / .aux scrapers for properties ASE doesn't surface."""
from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Optional, Tuple

NUM = r"[-+]?\d+\.\d+(?:[DdEe][-+]?\d+)?"


def _ff(s: str) -> float:
    return float(s.replace("D", "E").replace("d", "e"))


def parse_mopac_extras(workdir: str) -> Dict[str, Any]:
    """Return HOMO/LUMO, dipole, heat of formation, IP, ENPART components."""
    out_path = _find_with_ext(workdir, ".out")
    aux_path = _find_with_ext(workdir, ".aux")
    extras: Dict[str, Any] = {}
    if out_path is None:
        return extras

    with open(out_path) as f:
        out_text = f.read()

    # AUX file: structured KEY:UNIT=value entries
    if aux_path is not None and os.path.isfile(aux_path):
        with open(aux_path) as f:
            aux_text = f.read()
        aux_vals = {}
        for m in re.finditer(
            rf"^\s*([A-Z_][A-Z0-9_]*)(?::([A-Z/]+))?=\s*({NUM})\s*$",
            aux_text, re.MULTILINE,
        ):
            aux_vals[(m.group(1), m.group(2))] = _ff(m.group(3))
        if ("HEAT_OF_FORMATION", "KCAL/MOL") in aux_vals:
            extras["heat_of_formation_kcal_mol"] = aux_vals[("HEAT_OF_FORMATION", "KCAL/MOL")]
        if ("IONIZATION_POTENTIAL", "EV") in aux_vals:
            extras["ionization_potential_eV"] = aux_vals[("IONIZATION_POTENTIAL", "EV")]
        if ("DIPOLE", "DEBYE") in aux_vals:
            extras["dipole_debye"] = aux_vals[("DIPOLE", "DEBYE")]

    for line in out_text.split("\n"):
        upper = line.upper()
        if "ETOT (EONE + ETWO)" in upper:
            m = re.search(rf"({NUM})\s*EV", line)
            if m:
                extras["electronic_total_energy_eV"] = _ff(m.group(1))
        elif upper.lstrip().startswith("ELECTRON-NUCLEAR") and "EV" in upper and "ATTRACTION" not in upper:
            m = re.search(rf"({NUM})\s*EV", line)
            if m:
                extras["electron_nuclear_energy_eV"] = _ff(m.group(1))
        elif upper.lstrip().startswith("ELECTRON-ELECTRON") and "EV" in upper and "REPULSION" not in upper:
            m = re.search(rf"({NUM})\s*EV", line)
            if m:
                extras["electron_electron_energy_eV"] = _ff(m.group(1))
        elif "NUCLEAR-NUCLEAR REPULSION" in upper and "EV" in upper:
            m = re.search(rf"({NUM})\s*EV", line)
            if m:
                extras["nuclear_nuclear_repulsion_eV"] = _ff(m.group(1))
        elif "HOMO LUMO ENERGIES" in upper:
            nums = re.findall(NUM, line)
            if len(nums) >= 2:
                extras["homo_eV"] = float(nums[0])
                extras["lumo_eV"] = float(nums[1])
                extras["homo_lumo_gap_eV"] = float(nums[1]) - float(nums[0])
        elif "FINAL HEAT OF FORMATION" in upper and "heat_of_formation_kcal_mol" not in extras:
            m = re.search(rf"=\s*({NUM})\s*KCAL", upper)
            if m:
                extras["heat_of_formation_kcal_mol"] = _ff(m.group(1))
    return extras


def _find_with_ext(workdir: str, ext: str):
    for name in os.listdir(workdir):
        if name.lower().endswith(ext):
            return os.path.join(workdir, name)
    return None


def _parse_n_atoms(aux_text: str) -> Optional[int]:
    m = re.search(r"^\s*NUM_ATOMS\s*=\s*(\d+)", aux_text, re.MULTILINE)
    if m:
        return int(m.group(1))
    m = re.search(r"^\s*ATOM_EL\s*\[\s*(\d+)\s*\]", aux_text, re.MULTILINE)
    if m:
        return int(m.group(1))
    return None


def _is_linear(aux_text: str) -> bool:
    m = re.search(
        rf"^\s*PRI_MOM_OF_I[^=]*=\s*({NUM})\s+({NUM})\s+({NUM})",
        aux_text, re.MULTILINE,
    )
    if not m:
        return False
    moms = [abs(_ff(m.group(i))) for i in (1, 2, 3)]
    # A linear molecule has one principal moment ≈ 0 (much smaller than the others).
    return min(moms) < 1e-3 * max(moms)


def _parse_aux_array(aux_text: str, key: str) -> List[float]:
    """Pull a multi-line numeric array out of a MOPAC .aux file.

    AUX arrays look like:
        KEY:UNIT[count]=
          v1 v2 v3 ...
          v4 v5 v6 ...
        NEXT_KEY...
    """
    pattern = rf"^\s*{re.escape(key)}(?::[A-Z()/0-9\-]+)?\s*\[\d+\]\s*=\s*$"
    lines = aux_text.splitlines()
    out: List[float] = []
    in_block = False
    for ln in lines:
        if re.match(pattern, ln):
            in_block = True
            continue
        if not in_block:
            continue
        # End of block: a new KEY[...]=... line, or a non-numeric line
        if re.match(r"^\s*[A-Z_][A-Z0-9_]*", ln) and "=" in ln:
            break
        nums = re.findall(NUM, ln)
        if not nums and ln.strip():
            # Some entries include a header before the numbers; skip non-numeric
            continue
        for n in nums:
            try:
                out.append(_ff(n))
            except ValueError:
                pass
    return out


def parse_mopac_force(workdir: str) -> Dict[str, Any]:
    """Parse a MOPAC FORCE/THERMO run (PM7) — frequencies + thermo at 298 K.

    Returns:
      frequencies_cm: list of floats (negative = imaginary)
      zpe_kcal_mol: zero-point vibrational energy
      heat_of_formation_kcal_mol: HoF at the geometry passed in (no thermal correction)
      enthalpy_cal_mol_298, entropy_cal_K_mol_298, heat_capacity_cal_K_mol_298,
      gibbs_kcal_mol_298, h_of_T_kcal_mol_298 (HoF + thermal corrections at 298 K)
      temperature_K, n_imaginary_modes, n_real_vib_modes
    """
    aux_path = _find_with_ext(workdir, ".aux")
    out_path = _find_with_ext(workdir, ".out")
    result: Dict[str, Any] = {}

    if aux_path and os.path.isfile(aux_path):
        with open(aux_path) as f:
            aux_text = f.read()

        all_freqs = _parse_aux_array(aux_text, "VIB._FREQ")
        # MOPAC AUX writes 3N modes total in this order: the 3N-6 (or 3N-5 for
        # linear) genuine vibrational modes FIRST, then the 5 or 6 translational
        # /rotational modes at the end (often appearing as small numbers, but
        # not necessarily near zero — for larger molecules they can be -150+).
        # Slice by position rather than magnitude.
        natoms = _parse_n_atoms(aux_text)
        if natoms and len(all_freqs) == 3 * natoms:
            linear = _is_linear(aux_text)
            n_genuine = 3 * natoms - (5 if linear else 6)
            genuine = all_freqs[:n_genuine]
            drop = all_freqs[n_genuine:]
            result["vibrational_frequencies_cm-1"] = genuine
            result["mopac_dropped_trans_rot_cm-1"] = drop
        else:
            genuine = all_freqs
            result["vibrational_frequencies_cm-1"] = genuine

        result["n_imaginary_modes"] = sum(1 for f in genuine if f < -20.0)
        result["n_real_vib_modes"] = sum(1 for f in genuine if f > 20.0)

        m = re.search(rf"^\s*ZERO_POINT_ENERGY:KCAL/MOL\s*=\s*({NUM})",
                      aux_text, re.MULTILINE)
        if m:
            result["zpe_kcal_mol"] = _ff(m.group(1))

        m = re.search(rf"^\s*HEAT_OF_FORMATION:KCAL/MOL\s*=\s*({NUM})",
                      aux_text, re.MULTILINE)
        if m:
            result["heat_of_formation_kcal_mol"] = _ff(m.group(1))

        # Thermo arrays — first entry is at 298 K (the input temperature)
        temps = _parse_aux_array(aux_text, "THERMODYNAMIC_PROPERTIES_TEMPS")
        H_arr = _parse_aux_array(aux_text, "ENTHALPY_TOT")
        S_arr = _parse_aux_array(aux_text, "ENTROPY_TOT")
        Cp_arr = _parse_aux_array(aux_text, "HEAT_CAPACITY_TOT")
        HofT_arr = _parse_aux_array(aux_text, "H_O_F(T)")

        # MOPAC writes 298 K first by default
        if temps and H_arr:
            T = temps[0]
            result["temperature_K"] = T
            result["enthalpy_correction_cal_mol"] = H_arr[0]
            if S_arr:
                result["entropy_cal_K_mol"] = S_arr[0]
            if Cp_arr:
                result["heat_capacity_cal_K_mol"] = Cp_arr[0]
            if HofT_arr:
                result["heat_of_formation_T_kcal_mol"] = HofT_arr[0]
                # Gibbs free energy of formation at T:
                # G(T) = ΔHf(T) - T·S(T)
                if S_arr:
                    G_kcal = HofT_arr[0] - T * S_arr[0] / 1000.0
                    result["gibbs_free_energy_of_formation_kcal_mol"] = G_kcal

    if out_path and "vibrational_frequencies_cm-1" not in result:
        # AUX missing — fall back to scraping the .out file
        with open(out_path) as f:
            out_text = f.read()
        result.update(_parse_mopac_force_outfile(out_text))

    return result


def _parse_mopac_force_outfile(out_text: str) -> Dict[str, Any]:
    """Fallback: pull frequencies + ZPE + thermo block from the .out file."""
    result: Dict[str, Any] = {}

    # ZPE
    m = re.search(rf"ZERO POINT ENERGY\s+({NUM})\s+KCAL/MOL", out_text)
    if m:
        result["zpe_kcal_mol"] = _ff(m.group(1))

    # NORMAL COORDINATE ANALYSIS block contains the frequency rows
    freqs: List[float] = []
    nca = re.search(
        r"NORMAL COORDINATE ANALYSIS.*?(?=MASS-WEIGHTED COORDINATE|CARTESIAN FORCE|$)",
        out_text, re.DOTALL,
    )
    if nca:
        for line in nca.group(0).splitlines():
            stripped = line.strip()
            # Frequency rows are pure numbers (no atom labels, no Root No header)
            if not stripped or re.match(r"[A-Za-z]", stripped):
                continue
            if "Root No" in line or "Angstrom" in line:
                continue
            nums = re.findall(NUM, stripped)
            # Skip mode-displacement rows: those have a leading integer index
            if re.match(r"^\s*\d+\s+[-+]?\d", line):
                continue
            if nums and all(abs(_ff(n)) < 1e5 for n in nums):
                vals = [_ff(n) for n in nums]
                # Only accept rows that look like frequency rows: 1-3 entries,
                # values bounded to typical vibrational range
                if 1 <= len(vals) <= 3 and all(-2000 < v < 5000 for v in vals):
                    freqs.extend(vals)
    if freqs:
        result["vibrational_frequencies_cm-1"] = freqs
        result["n_imaginary_modes"] = sum(1 for f in freqs if f < -20.0)
        result["n_real_vib_modes"] = sum(1 for f in freqs if f > 20.0)

    # Thermo table — first non-header line after "CALCULATED THERMODYNAMIC PROPERTIES"
    thermo_match = re.search(
        r"(\d+(?:\.\d+)?)\s+TOT\.\s+([-\d.D+E]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)",
        out_text,
    )
    if thermo_match:
        result["temperature_K"] = float(thermo_match.group(1))
        result["heat_of_formation_T_kcal_mol"] = float(thermo_match.group(2))
        result["enthalpy_correction_cal_mol"] = float(thermo_match.group(3))
        result["heat_capacity_cal_K_mol"] = float(thermo_match.group(4))
        result["entropy_cal_K_mol"] = float(thermo_match.group(5))
    return result
