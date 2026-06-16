"""Frontier molecular orbital energies + HOMO-LUMO gap task.

Reports HOMO, LUMO, neighbouring frontier orbitals (HOMO-K..HOMO, LUMO..LUMO+K),
the HOMO-LUMO gap, and Koopmans-based global reactivity descriptors at a FIXED
geometry — no optimization.
"""
from __future__ import annotations

import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from ..calculators import (
    build_calculator, apply_calc_to_atoms, MOPAC_SOLVENT_EPS,
    method_label, program_label, collect_calc_extras, mopac_spin_keyword,
    register_auto_tempdir, resolve_dielectric,
)
from ..io import read_geometry
from ..schema import (
    base_result, energy_block_from_eV, element_warnings,
    scf_convergence_warnings,
)
from ._mopac_parsers import parse_mopac_extras

# CODATA 2022: Hartree energy = 27.211 386 245 981(30) eV; Bohr radius =
# 0.529 177 210 544(82) Å, so ANGSTROM_TO_BOHR = 1/0.529177210544.
# Ref: Mohr, Tiesinga, Newell, Taylor, CODATA 2022, NIST,
# https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15).
HARTREE_TO_EV = 27.211386245981
ANGSTROM_TO_BOHR = 1.8897261259078

NUM = r"[-+]?\d+\.\d+(?:[DdEe][-+]?\d+)?"


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    nfrontier: int = 3,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Single-point frontier orbital analysis on the supplied geometry."""
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    # Build the calc up-front for label/extras consistency (used by the
    # generic dft/hf branch and for label inference even on xtb/mopac).
    calc_for_label = None
    if method in ("dft", "hf"):
        calc_for_label = build_calculator(
            method, charge=charge, multiplicity=multiplicity, solvent=solvent,
            tier=tier, functional=functional, basis=basis, density_fit=density_fit,
            solvent_model=solvent_model,
        )

    result = base_result(
        task="frontier_orbitals",
        method=method_label(method, calc_for_label),
        program=program_label(method),
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=symbols,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )

    if method == "xtb":
        body = _run_xtb(atoms, charge=charge, multiplicity=multiplicity,
                        solvent=solvent, nfrontier=nfrontier)
    elif method == "mopac":
        body = _run_mopac(atoms, charge=charge, multiplicity=multiplicity,
                          solvent=solvent, nfrontier=nfrontier)
    elif method in ("dft", "hf"):
        body = _run_generic(atoms, calc=calc_for_label, method=method,
                            nfrontier=nfrontier)
    else:
        raise ValueError(
            f"Unknown method {method!r}. Expected 'xtb', 'mopac', 'dft', or 'hf'."
        )

    solvent_drop = body.pop("_solvent_drop_warning", None)
    # Preserve any warnings the body itself carries (e.g. "no virtual orbitals"
    # for basis-saturated species) — merge, don't overwrite.
    body_warns = list(body.pop("warnings", []) or [])
    result.update(body)
    warns = body_warns + element_warnings(symbols, method)
    warns += scf_convergence_warnings(method, body)
    if solvent_drop:
        warns.append(solvent_drop)
    if warns:
        result["warnings"] = warns

    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def _run_generic(atoms, *, calc, method, nfrontier) -> Dict[str, Any]:
    """DFT/HF frontier-orbital extraction via the PySCF backend.

    Relies on the PySCFCalculator stashing eigenvalues/occupations on itself
    as `_chemkit_extras` (orbital_energies_eV, orbital_occupations). For UKS
    /UHF, those fields are dicts {alpha: [...], beta: [...]} and we merge
    both channels (occupation 1.0 per electron) into a single sorted list so
    HOMO = highest occupied across both spins.
    """
    apply_calc_to_atoms(atoms, calc)
    total_energy_eV = float(atoms.get_potential_energy())
    extras = collect_calc_extras(method, atoms, calc) or {}
    eigs_eV = extras.get("orbital_energies_eV") or extras.get("eigenvalues_eV")
    occs = extras.get("orbital_occupations") or extras.get("occupations")
    if eigs_eV is None or occs is None:
        raise RuntimeError(
            f"frontier ({method}): PySCF calculator did not return orbital "
            "eigenvalues/occupations (expected on calc._chemkit_extras)."
        )
    # Unrestricted: merge α + β channels into a single sorted (energy, occ) list.
    if isinstance(eigs_eV, dict):
        e_a = list(eigs_eV.get("alpha", []))
        e_b = list(eigs_eV.get("beta", []))
        o_a = list(occs.get("alpha", []))
        o_b = list(occs.get("beta", []))
        combined = list(zip(e_a + e_b, o_a + o_b))
        combined.sort(key=lambda eo: eo[0])
        eigs_flat = [e for e, _ in combined]
        occs_flat = [o for _, o in combined]
    else:
        eigs_flat = list(eigs_eV)
        occs_flat = list(occs)
    body = _build_block(eigs_flat, occs_flat, total_energy_eV,
                        energy_zero="electronic energy (bare nuclei + electrons)",
                        nfrontier=nfrontier)
    # Carry the SCF convergence flags up so run() can promote a non-convergence
    # to a prominent top-level warning (calculation-reporting-standards #6/#7).
    for k in ("scf_converged", "scf_cycles"):
        if k in extras:
            body[k] = extras[k]
    return body


# ---------------------------------------------------------------------------
# xtb
# ---------------------------------------------------------------------------

def _run_xtb(atoms, *, charge, multiplicity, solvent, nfrontier) -> Dict[str, Any]:
    try:
        import numpy as np
        from xtb.interface import Calculator, Param
        from xtb.libxtb import VERBOSITY_MUTED
    except ImportError as exc:
        raise RuntimeError(
            f"frontier (xtb) requires xtb-python ({exc}). "
            "Install: conda install -c conda-forge xtb-python"
        )

    numbers = np.asarray(atoms.get_atomic_numbers(), dtype=np.int32)
    positions_bohr = np.asarray(atoms.get_positions()) * ANGSTROM_TO_BOHR
    uhf = max(0, multiplicity - 1)

    xcalc = Calculator(Param.GFN2xTB, numbers, positions_bohr,
                       charge=float(charge), uhf=uhf)
    xcalc.set_verbosity(VERBOSITY_MUTED)
    solvent_dropped = False
    if solvent:
        try:
            from xtb.utils import get_solvent, Solvent
            sol = get_solvent(solvent)
            if sol != Solvent.none:
                xcalc.set_solvent(sol)
        except Exception:
            # Older xtb-python lacks set_solvent — orbital energies would be
            # gas-phase while the result claims a solvent. Flag it.
            solvent_dropped = True

    res = xcalc.singlepoint()
    eigs_eV = (np.asarray(res.get_orbital_eigenvalues()) * HARTREE_TO_EV).tolist()
    occs = np.asarray(res.get_orbital_occupations()).tolist()
    total_energy_eV = float(res.get_energy()) * HARTREE_TO_EV

    block = _build_block(eigs_eV, occs, total_energy_eV,
                         energy_zero="isolated atoms at infinity (xtb)",
                         nfrontier=nfrontier)
    if solvent_dropped:
        block["solvent_applied"] = False
        block["_solvent_drop_warning"] = (
            f"Requested solvent {solvent!r} was NOT applied (this xtb-python "
            "build lacks set_solvent); orbital energies are GAS-PHASE values."
        )
    return block


# ---------------------------------------------------------------------------
# MOPAC
# ---------------------------------------------------------------------------

def _run_mopac(atoms, *, charge, multiplicity, solvent, nfrontier) -> Dict[str, Any]:
    from ase.calculators.mopac import MOPAC

    keywords = ["PM7", "1SCF", "VECTORS", "ALLVEC", "AUX", "ENPART",
                "LARGE=-1", "THREADS=1", "GEO-OK"]
    if charge != 0:
        keywords.append(f"CHARGE={charge}")
    if multiplicity > 1:
        keywords.append(mopac_spin_keyword(multiplicity))
        keywords.append("UHF")
    if solvent:
        eps = resolve_dielectric(solvent, MOPAC_SOLVENT_EPS, backend="mopac")
        keywords.append(f"EPS={eps}")

    workdir = register_auto_tempdir(tempfile.mkdtemp(prefix="chemkit_frontier_mopac_"))
    calc = MOPAC(label=os.path.join(workdir, "mopac"),
                 task=" ".join(keywords), relscf=0.01)
    calc._chemkit_keywords = keywords
    calc._chemkit_workdir = workdir
    atoms.calc = calc
    total_energy_eV = float(atoms.get_potential_energy())

    eigs_eV, occs = _parse_mopac_eigenvalues(workdir)
    if not eigs_eV:
        raise RuntimeError(
            f"MOPAC: could not parse orbital eigenvalues. Workdir: {workdir}"
        )

    body = _build_block(eigs_eV, occs, total_energy_eV,
                        energy_zero="elements in their standard states (PM7 heat of formation)",
                        nfrontier=nfrontier)

    extras = parse_mopac_extras(workdir)
    if extras:
        if "heat_of_formation_kcal_mol" in extras:
            body["final_heat_of_formation_kcal_mol"] = extras["heat_of_formation_kcal_mol"]
        body["code_specific"] = extras
    return body


def _parse_mopac_eigenvalues(workdir: str) -> Tuple[List[float], List[float]]:
    """Return a merged-and-sorted (eigenvalues_eV, occupations) list.

    Open-shell (UHF) MOPAC runs have separate α and β blocks; we concatenate
    them (with occupations 1.0/1.0 for occupied α/β orbitals) and sort by
    energy so HOMO = highest-occupied across both spin channels. This matches
    how chemists report the "HOMO" of a radical (whichever spin is highest).
    """
    aux_path = _find_with_ext(workdir, ".aux")
    if aux_path and os.path.isfile(aux_path):
        with open(aux_path) as f:
            aux_text = f.read()
        # Restricted: single EIGENVALUES block.
        eigs_r = _parse_aux_array(aux_text, "EIGENVALUES")
        if eigs_r:
            occs = _occupations_from_aux(aux_text, len(eigs_r))
            return eigs_r, occs
        # Unrestricted: separate ALPHA / BETA blocks.
        eigs_a = _parse_aux_array(aux_text, "ALPHA_EIGENVALUES")
        eigs_b = _parse_aux_array(aux_text, "BETA_EIGENVALUES")
        if eigs_a or eigs_b:
            return _merge_uhf_orbitals(aux_text, eigs_a, eigs_b)

    out_path = _find_with_ext(workdir, ".out")
    if out_path and os.path.isfile(out_path):
        with open(out_path) as f:
            out_text = f.read()
        # First try unrestricted: separate "ALPHA EIGENVALUES" / "BETA EIGENVALUES"
        eigs_a = _parse_eigenvalues_from_out(out_text, header="ALPHA EIGENVALUES")
        eigs_b = _parse_eigenvalues_from_out(out_text, header="BETA EIGENVALUES")
        if eigs_a or eigs_b:
            n_alpha = _parse_int_aux(_safe_read(aux_path), "NUM_ALPHA_ELECTRONS") if aux_path else None
            n_beta = _parse_int_aux(_safe_read(aux_path), "NUM_BETA_ELECTRONS") if aux_path else None
            if n_alpha is None or n_beta is None:
                # No AUX → guess: assume the first half of each block is occupied.
                n_alpha = len(eigs_a) // 2 if eigs_a else 0
                n_beta = len(eigs_b) // 2 if eigs_b else 0
            return _zip_uhf(eigs_a, eigs_b, n_alpha, n_beta)
        # Restricted closed-shell only — look for the standalone EIGENVALUES block.
        eigs = _parse_eigenvalues_from_out(out_text, header="EIGENVALUES")
        if eigs:
            n_electrons = _parse_int_aux(_safe_read(aux_path), "NUM_ELECTRONS") if aux_path else None
            if n_electrons is None:
                n_electrons = 2 * (len(eigs) // 2)
            n_occ = n_electrons // 2
            occs = [2.0] * n_occ + [0.0] * (len(eigs) - n_occ)
            return eigs, occs
    return [], []


def _occupations_from_aux(aux_text: str, n_orb: int) -> List[float]:
    occs = _parse_aux_array(aux_text, "MOLECULAR_ORBITAL_OCCUPANCIES")
    if occs and len(occs) >= n_orb:
        return occs[:n_orb]
    n_electrons = _parse_int_aux(aux_text, "NUM_ELECTRONS")
    if n_electrons is None:
        return []
    n_occ = n_electrons // 2
    return [2.0] * n_occ + [0.0] * (n_orb - n_occ)


def _merge_uhf_orbitals(
    aux_text: str, eigs_a: List[float], eigs_b: List[float],
) -> Tuple[List[float], List[float]]:
    """For UHF: zip α + β eigenvalues into a single sorted list with
    spin-channel occupations (1.0 occupied, 0.0 virtual per channel)."""
    n_alpha = _parse_int_aux(aux_text, "NUM_ALPHA_ELECTRONS")
    n_beta = _parse_int_aux(aux_text, "NUM_BETA_ELECTRONS")
    if n_alpha is None or n_beta is None:
        # Fall back to per-channel "half occupied" guess.
        n_alpha = len(eigs_a) // 2 if eigs_a else 0
        n_beta = len(eigs_b) // 2 if eigs_b else 0
    return _zip_uhf(eigs_a, eigs_b, n_alpha, n_beta)


def _zip_uhf(
    eigs_a: List[float], eigs_b: List[float], n_alpha: int, n_beta: int,
) -> Tuple[List[float], List[float]]:
    occ_a = [1.0] * n_alpha + [0.0] * max(0, len(eigs_a) - n_alpha)
    occ_b = [1.0] * n_beta + [0.0] * max(0, len(eigs_b) - n_beta)
    combined = list(zip(eigs_a + eigs_b, occ_a + occ_b))
    combined.sort(key=lambda eo: eo[0])
    return [e for e, _ in combined], [o for _, o in combined]


def _parse_int_aux(aux_text: Optional[str], key: str) -> Optional[int]:
    if not aux_text:
        return None
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*(\d+)", aux_text, re.MULTILINE)
    return int(m.group(1)) if m else None


def _parse_aux_array(aux_text: str, key: str) -> List[float]:
    pattern = rf"^\s*{re.escape(key)}(?::[A-Z()/0-9\-]+)?\s*\[\d+\]\s*=\s*$"
    out: List[float] = []
    in_block = False
    for ln in aux_text.splitlines():
        if re.match(pattern, ln):
            in_block = True
            continue
        if not in_block:
            continue
        if re.match(r"^\s*[A-Z_][A-Z0-9_]*", ln) and "=" in ln:
            break
        for n in re.findall(NUM, ln):
            try:
                out.append(float(n.replace("D", "E").replace("d", "e")))
            except ValueError:
                pass
    return out


def _parse_eigenvalues_from_out(out_text: str, *, header: str = "EIGENVALUES") -> List[float]:
    # Anchor with a word boundary on the LEFT so 'EIGENVALUES' does not match
    # inside 'ALPHA_EIGENVALUES' / 'BETA_EIGENVALUES'. The header is taken as a
    # whole token; e.g. header='EIGENVALUES' won't match 'ALPHA EIGENVALUES'.
    pat = r"(?<![A-Z_])" + re.escape(header) + r"\s*\n(.*?)(?:\n\s*\n|NET ATOMIC)"
    m = re.search(pat, out_text, re.DOTALL)
    if not m:
        return []
    vals: List[float] = []
    for ln in m.group(1).splitlines():
        for tok in re.findall(NUM, ln):
            try:
                vals.append(float(tok))
            except ValueError:
                pass
    return vals


def _find_with_ext(workdir: str, ext: str):
    if not os.path.isdir(workdir):
        return None
    for name in os.listdir(workdir):
        if name.lower().endswith(ext):
            return os.path.join(workdir, name)
    return None


def _safe_read(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.isfile(path):
        return None
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Shared post-processing
# ---------------------------------------------------------------------------

def _build_block(eigs_eV: List[float], occs: List[float],
                 total_energy_eV: float, *, energy_zero: str,
                 nfrontier: int) -> Dict[str, Any]:
    occupied = [i for i, o in enumerate(occs) if o > 1e-6]
    virtual = [i for i, o in enumerate(occs) if o < 1e-6]
    # Some closed-shell systems have a basis that's fully saturated by
    # occupied orbitals (e.g. F- in GFN2's minimal valence basis: 1×2s +
    # 3×2p = 4 occupied, 0 virtual). Likewise some bare metal atoms in
    # GFN2 have no virtuals above the valence shell. In those cases HOMO
    # is well-defined but LUMO is not — return a structured "partial"
    # result with a warning rather than crashing.
    if not occupied:
        body: Dict[str, Any] = {}
        body.update(energy_block_from_eV(total_energy_eV))
        body["energy_zero"] = energy_zero
        body["n_orbitals"] = len(eigs_eV)
        body["n_occupied"] = 0
        body["homo_eV"] = None
        body["lumo_eV"] = None
        body["homo_lumo_gap_eV"] = None
        body["orbitals"] = []
        body["koopmans"] = {}
        body["warnings"] = [
            "No occupied orbitals returned by the calculator — cannot "
            "compute HOMO/LUMO. This usually means an SCF failure; check "
            "the calculator log."
        ]
        return body
    if not virtual:
        homo_idx = occupied[-1]
        homo = float(eigs_eV[homo_idx])
        body = {}
        body.update(energy_block_from_eV(total_energy_eV))
        body["energy_zero"] = energy_zero
        body["n_orbitals"] = len(eigs_eV)
        body["n_occupied"] = len(occupied)
        body["homo_index"] = homo_idx
        body["lumo_index"] = None
        body["homo_eV"] = homo
        body["lumo_eV"] = None
        body["homo_lumo_gap_eV"] = None
        body["orbitals"] = _frontier_entries(
            eigs_eV, occs, homo_idx, lumo_idx=None, nfrontier=nfrontier
        )
        body["koopmans"] = {"vertical_IP_eV": -homo}
        body["warnings"] = [
            f"No virtual orbitals available — the calculator's basis is fully "
            f"saturated by {len(occupied)} occupied orbital(s). HOMO/IP are "
            f"reported; LUMO/EA/gap and the rest of the Koopmans descriptors "
            f"cannot be computed. Common for closed-shell anions and bare "
            f"metal atoms in GFN2's minimal valence basis."
        ]
        return body
    homo_idx = occupied[-1]
    lumo_idx = virtual[0]
    homo = float(eigs_eV[homo_idx])
    lumo = float(eigs_eV[lumo_idx])

    body: Dict[str, Any] = {}
    body.update(energy_block_from_eV(total_energy_eV))
    body["energy_zero"] = energy_zero
    body["n_orbitals"] = len(eigs_eV)
    body["n_occupied"] = len(occupied)
    body["homo_index"] = homo_idx
    body["lumo_index"] = lumo_idx
    body["homo_eV"] = homo
    body["lumo_eV"] = lumo
    body["homo_lumo_gap_eV"] = lumo - homo
    body["orbitals"] = _frontier_entries(eigs_eV, occs, homo_idx, lumo_idx, nfrontier)
    body["koopmans"] = _koopmans(homo, lumo)
    return body


def _frontier_entries(eigs_eV, occs, homo_idx, lumo_idx, nfrontier) -> List[Dict[str, Any]]:
    nfrontier = max(1, int(nfrontier))
    entries: List[Dict[str, Any]] = []
    for k in range(nfrontier):
        idx = homo_idx - k
        if idx < 0:
            break
        entries.append({
            "label": "HOMO" if k == 0 else f"HOMO-{k}",
            "index": idx,
            "energy_eV": float(eigs_eV[idx]),
            "occupation": float(occs[idx]),
        })
    if lumo_idx is not None:
        for k in range(nfrontier):
            idx = lumo_idx + k
            if idx >= len(eigs_eV):
                break
            entries.append({
                "label": "LUMO" if k == 0 else f"LUMO+{k}",
                "index": idx,
                "energy_eV": float(eigs_eV[idx]),
                "occupation": float(occs[idx]),
            })
    entries.sort(key=lambda e: e["energy_eV"])
    return entries


def _koopmans(homo_eV: float, lumo_eV: float) -> Dict[str, float]:
    ip = -homo_eV
    ea = -lumo_eV
    chi = 0.5 * (ip + ea)
    eta = 0.5 * (ip - ea)
    out = {
        "vertical_IP_eV": ip,
        "vertical_EA_eV": ea,
        "electronegativity_eV": chi,
        "chemical_hardness_eV": eta,
    }
    if abs(eta) > 1e-9:
        out["chemical_softness_per_eV"] = 1.0 / eta
        out["electrophilicity_index_eV"] = (chi * chi) / (2.0 * eta)
    return out
