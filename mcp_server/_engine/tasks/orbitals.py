"""Visualize molecular orbitals — write molden + optional cube files.

This task dumps the orbital wavefunction in formats viewers understand
(`.molden` always, `.cube` per requested orbital on demand). It does NOT
render images itself — the user opens the files in their preferred viewer
(Avogadro, Jmol, IboView, VMD, Multiwfn, …), which renders isosurfaces far
better than a static matplotlib PNG ever could.

Backends:
  xtb    → `xtb --molden` writes a molden directly. Optional cubes are
           evaluated by re-loading the molden into PySCF's `mol`/`mo_coeff`
           and calling `pyscf.tools.cubegen.orbital`.
  mopac  → `GRAPHF` keyword writes a `.mgf`. We synthesize a molden from
           the .mgf (STO basis → hardcoded STO-3G Gaussian contractions)
           so the same cubegen path works.
  dft/hf → `pyscf.tools.molden.from_scf(mf, ...)` for molden,
           `pyscf.tools.cubegen.orbital(mf.mol, ..., mf.mo_coeff[:, idx])`
           for cubes. Direct — no round-trip through molden.

The orbital labels accepted by `cubes` are:
  homo, lumo, homo-1, homo-2, lumo+1, lumo+2, ...
  or an explicit 1-based integer MO index ("1", "5", ...).
For unrestricted (open-shell) calculations, suffix ":alpha" or ":beta"
selects the spin channel (e.g. "homo:alpha"). With no suffix and an open
shell, the alpha channel is used and a note is written into the result.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from ..calculators import (
    build_calculator, apply_calc_to_atoms, MOPAC_SOLVENT_EPS,
    method_label, program_label, mopac_spin_keyword,
    register_auto_tempdir, XTB_SOLVENT_MAP,
)
from ..io import read_geometry
from ..schema import base_result, element_warnings

# CODATA 2022: Hartree energy = 27.211 386 245 981(30) eV; Bohr radius =
# 0.529 177 210 544(82) Å, so ANGSTROM_TO_BOHR = 1/0.529177210544.
# Ref: Mohr, Tiesinga, Newell, Taylor, CODATA 2022, NIST,
# https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15).
HARTREE_TO_EV = 27.211386245981
ANGSTROM_TO_BOHR = 1.8897261259078


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    cubes: Optional[List[str]] = None,
    grid: int = 80,
    out_stem: Optional[str] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Write a molden file (always) and optional cubes for the requested MOs."""
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()
    cubes = list(cubes or [])

    # Output stem: where to drop .molden / .cube files. The CLI passes the
    # JSON path's stem; if missing (library use) we co-locate with the input.
    if out_stem is None:
        out_stem = os.path.splitext(os.path.abspath(input_path))[0] + f"_orbitals_{method}"
    out_stem = os.path.abspath(out_stem)
    os.makedirs(os.path.dirname(out_stem) or ".", exist_ok=True)

    # Build calc up-front for label/extras consistency on dft/hf.
    calc_for_label = None
    if method in ("dft", "hf"):
        calc_for_label = build_calculator(
            method, charge=charge, multiplicity=multiplicity, solvent=solvent,
            tier=tier, functional=functional, basis=basis,
        )

    result = base_result(
        task="visualize_orbitals",
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
                        solvent=solvent, out_stem=out_stem,
                        cubes=cubes, grid=grid)
    elif method == "mopac":
        body = _run_mopac(atoms, charge=charge, multiplicity=multiplicity,
                          solvent=solvent, out_stem=out_stem,
                          cubes=cubes, grid=grid)
    elif method in ("dft", "hf"):
        body = _run_generic(atoms, calc=calc_for_label, out_stem=out_stem,
                            cubes=cubes, grid=grid)
    else:
        raise ValueError(
            f"Unknown method {method!r}. Expected 'xtb', 'mopac', 'dft', or 'hf'."
        )

    result.update(body)
    warns = element_warnings(symbols, method)
    if warns:
        result.setdefault("warnings", []).extend(warns)

    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


# ---------------------------------------------------------------------------
# Orbital-label parsing
# ---------------------------------------------------------------------------

_LABEL_RE = re.compile(
    r"^(?P<core>homo|lumo|\d+)\s*(?P<sign>[+-])?\s*(?P<offset>\d+)?"
    r"(?::(?P<spin>alpha|beta))?$"
)


def _resolve_labels(labels: List[str], mo_occ, n_alpha: Optional[int] = None,
                    n_beta: Optional[int] = None) -> List[Tuple[str, int, Optional[str]]]:
    """Resolve user orbital labels into (display_label, 0-based MO index, spin) tuples.

    `mo_occ` may be a 1D list (restricted) or a 2-tuple of 1D lists
    (unrestricted, alpha/beta). For UHF/UKS, we resolve homo/lumo per-spin.
    """
    import numpy as np
    out: List[Tuple[str, int, Optional[str]]] = []

    def _homo_lumo(occ_1d):
        occ = np.asarray(occ_1d)
        occupied = np.where(occ > 0)[0]
        if len(occupied) == 0:
            return None, 0
        homo = int(occupied[-1])
        lumo = homo + 1 if homo + 1 < len(occ) else None
        return homo, lumo

    # Detect unrestricted (mo_occ shape is (2, nmo) or tuple of two arrays).
    arr = np.asarray(mo_occ)
    is_unrestricted = arr.ndim == 2 and arr.shape[0] == 2

    if is_unrestricted:
        homo_a, lumo_a = _homo_lumo(arr[0])
        homo_b, lumo_b = _homo_lumo(arr[1])
    else:
        homo, lumo = _homo_lumo(arr.flatten())

    for raw in labels:
        s = raw.strip().lower().replace(" ", "")
        m = _LABEL_RE.match(s)
        if not m:
            raise ValueError(
                f"Invalid orbital label {raw!r}. Use 'homo', 'lumo', 'homo-1', "
                "'lumo+2', a 1-based integer index, or any of those with "
                "':alpha' / ':beta' suffix."
            )
        core = m.group("core")
        sign = m.group("sign") or "+"
        offset = int(m.group("offset") or 0)
        spin = m.group("spin")
        delta = offset if sign == "+" else -offset

        # Pick the reference index for this label + spin.
        if is_unrestricted:
            if spin is None:
                spin = "alpha"  # default to alpha for open-shell; noted in result
            ref_homo = homo_a if spin == "alpha" else homo_b
            ref_lumo = lumo_a if spin == "alpha" else lumo_b
        else:
            ref_homo, ref_lumo = homo, lumo

        if core == "homo":
            idx = ref_homo + delta if ref_homo is not None else None
        elif core == "lumo":
            idx = ref_lumo + delta if ref_lumo is not None else None
        else:
            # Explicit 1-based MO index — ':alpha'/':beta' selects spin block.
            idx = int(core) - 1 + delta

        if idx is None or idx < 0:
            raise ValueError(
                f"Orbital {raw!r} resolves outside the orbital range "
                f"(homo={ref_homo}, lumo={ref_lumo})."
            )

        out.append((raw, idx, spin))
    return out


# ---------------------------------------------------------------------------
# PySCF (dft / hf)
# ---------------------------------------------------------------------------

def _run_generic(atoms, *, calc, out_stem: str, cubes: List[str],
                 grid: int) -> Dict[str, Any]:
    """DFT/HF molden + optional cubes via PySCF directly off the mf object."""
    from pyscf.tools import molden, cubegen
    import numpy as np

    apply_calc_to_atoms(atoms, calc)
    _ = atoms.get_potential_energy()  # force the SCF
    mf = getattr(calc, "mean_field", None) or getattr(calc, "_mf", None)
    if mf is None:
        raise RuntimeError(
            "orbitals (dft/hf): PySCF calculator did not retain a mean_field "
            "object. This is a bug — every PySCFCalculator caches mf."
        )

    molden_path = f"{out_stem}.molden"
    molden.from_scf(mf, molden_path)

    cube_paths: Dict[str, str] = {}
    notes: List[str] = []
    if cubes:
        is_unrestricted = np.asarray(mf.mo_occ).ndim == 2
        resolved = _resolve_labels(cubes, mf.mo_occ)
        for label, idx, spin in resolved:
            if is_unrestricted:
                coeff = mf.mo_coeff[0 if (spin or "alpha") == "alpha" else 1][:, idx]
                spin_tag = (spin or "alpha")
                if spin is None:
                    notes.append(f"{label}: open-shell — defaulted to alpha channel.")
                fname = f"{out_stem}_{_safe_label(label)}_{spin_tag}.cube"
            else:
                coeff = mf.mo_coeff[:, idx]
                fname = f"{out_stem}_{_safe_label(label)}.cube"
            cubegen.orbital(mf.mol, fname, coeff,
                            nx=grid, ny=grid, nz=grid)
            cube_paths[label] = os.path.abspath(fname)

    body: Dict[str, Any] = {
        "molden_path": os.path.abspath(molden_path),
        "cube_paths": cube_paths,
        "n_orbitals": int(np.asarray(mf.mo_occ).size if np.asarray(mf.mo_occ).ndim == 1
                          else np.asarray(mf.mo_occ).shape[1]),
        "grid_resolution": grid if cubes else None,
    }
    body["mo_summary"] = _mo_summary(mf.mo_occ, mf.mo_energy)
    if notes:
        body.setdefault("warnings", []).extend(notes)
    return body


def _mo_summary(mo_occ, mo_energy) -> Dict[str, Any]:
    """Return a compact summary (homo/lumo indices + energies) for the result JSON."""
    import numpy as np
    occ = np.asarray(mo_occ)
    en = np.asarray(mo_energy)
    if occ.ndim == 2 and occ.shape[0] == 2:
        out: Dict[str, Any] = {"unrestricted": True}
        for i, spin in enumerate(("alpha", "beta")):
            occupied = np.where(occ[i] > 0)[0]
            if len(occupied):
                homo = int(occupied[-1])
                lumo = homo + 1 if homo + 1 < len(occ[i]) else None
                out[spin] = {
                    "homo_idx_1based": homo + 1,
                    "homo_energy_eV": float(en[i][homo] * HARTREE_TO_EV),
                    "lumo_idx_1based": lumo + 1 if lumo is not None else None,
                    "lumo_energy_eV": float(en[i][lumo] * HARTREE_TO_EV) if lumo is not None else None,
                }
        return out
    occupied = np.where(occ.flatten() > 0)[0]
    if not len(occupied):
        return {"unrestricted": False}
    homo = int(occupied[-1])
    lumo = homo + 1 if homo + 1 < len(occ.flatten()) else None
    return {
        "unrestricted": False,
        "homo_idx_1based": homo + 1,
        "homo_energy_eV": float(en.flatten()[homo] * HARTREE_TO_EV),
        "lumo_idx_1based": lumo + 1 if lumo is not None else None,
        "lumo_energy_eV": (float(en.flatten()[lumo] * HARTREE_TO_EV)
                           if lumo is not None else None),
    }


def _safe_label(label: str) -> str:
    """Sanitize an orbital label for use in a filename ('homo-1' -> 'homo-1')."""
    return re.sub(r"[^A-Za-z0-9_+-]", "_", label.replace(":", "_"))


# ---------------------------------------------------------------------------
# xtb
# ---------------------------------------------------------------------------

def _run_xtb(atoms, *, charge: int, multiplicity: int,
             solvent: Optional[str], out_stem: str,
             cubes: List[str], grid: int) -> Dict[str, Any]:
    """Run `xtb --molden` to produce the molden; reuse it for optional cubes."""
    if not shutil.which("xtb"):
        raise RuntimeError(
            "orbitals (xtb) requires the `xtb` binary on PATH. "
            "Install: conda install -c conda-forge xtb"
        )

    workdir = register_auto_tempdir(tempfile.mkdtemp(prefix="chemkit_orb_xtb_"))
    xyz_in = os.path.join(workdir, "input.xyz")
    atoms.write(xyz_in)

    cmd = ["xtb", xyz_in, "--sp", "--molden", "--norestart"]
    if charge != 0:
        cmd += ["--chrg", str(charge)]
    uhf = max(0, multiplicity - 1)
    if uhf:
        cmd += ["--uhf", str(uhf)]
    if solvent:
        sol = XTB_SOLVENT_MAP.get(solvent.lower(), solvent)
        cmd += ["--alpb", sol]

    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"xtb failed (rc={proc.returncode}). Workdir: {workdir}\n"
            f"stderr tail:\n{proc.stderr[-1000:]}"
        )

    src_molden = os.path.join(workdir, "molden.input")
    if not os.path.isfile(src_molden):
        raise RuntimeError(
            f"xtb completed but did not write molden.input in {workdir}. "
            f"stdout tail:\n{proc.stdout[-500:]}"
        )

    molden_path = f"{out_stem}.molden"
    shutil.copyfile(src_molden, molden_path)

    cube_paths: Dict[str, str] = {}
    n_orbitals = 0
    mo_summary: Dict[str, Any] = {}
    if cubes or True:  # Always load molden to populate mo_summary in result JSON.
        from pyscf.tools import molden as pmolden, cubegen
        import numpy as np

        loaded = pmolden.load(molden_path)
        # pyscf.tools.molden.load returns (mol, mo_energy, mo_coeff, mo_occ, irrep, spin)
        mol, mo_energy, mo_coeff, mo_occ, _irrep, _spin = loaded
        n_orbitals = int(np.asarray(mo_occ).size)
        # pyscf.tools.molden.load returns mo_energy in hartree (PySCF native).
        mo_summary = _mo_summary(mo_occ, np.asarray(mo_energy))

        if cubes:
            resolved = _resolve_labels(cubes, mo_occ)
            for label, idx, _spin_tag in resolved:
                fname = f"{out_stem}_{_safe_label(label)}.cube"
                cubegen.orbital(mol, fname, mo_coeff[:, idx],
                                nx=grid, ny=grid, nz=grid)
                cube_paths[label] = os.path.abspath(fname)

    return {
        "molden_path": os.path.abspath(molden_path),
        "cube_paths": cube_paths,
        "n_orbitals": n_orbitals,
        "grid_resolution": grid if cubes else None,
        "mo_summary": mo_summary,
        "xtb_workdir": workdir,
    }


# ---------------------------------------------------------------------------
# MOPAC — implemented in the follow-up task. Stub for now so the dispatcher
# raises a clear error if invoked with --method mopac.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MOPAC
# ---------------------------------------------------------------------------
#
# MOPAC's GRAPHF keyword writes a `.mgf` (MOPAC Graphical) file with:
#   line 1     : "<natom> MOPAC-Graphical data Version ..."
#   line 2..N+1: "<Z> <x> <y> <z> <partial_charge>"   per atom
#   line N+2..2N+1: "<zs> <zp> <zd>"                  STO exponents per atom
#   then     : ORBITAL <occ> <sym>     <energy_eV>
#              <basis-function-coefficients on continuation lines>
#   repeated for every MO; finally INVERSE_MATRIX (unused here).
#
# Basis-function ORDER per atom in the coefficient vector is:
#   one s-shell, then a p-shell (px,py,pz) if zp>0, then d-shell if zd>0.
# Coefficients are in the original STO basis (not the Löwdin-orthogonal one),
# so we can drop them straight into a [MO] block once the [GTO] block lists
# basis functions in the same order.
#
# Molden expects Gaussians, not Slater functions, so each STO is expanded as
# STO-3G (3 primitive Gaussians per shell). The STO-3G contraction
# coefficients/exponents below are the standard ones (Hehre, Stewart, Pople
# 1969), evaluated for the per-atom Slater exponent ζ via αᵢ = αᵢ⁰·ζ².

# (α_i, c_i) for an STO-3G expansion at ζ=1; scale α by ζ² for any other ζ.
# Source: Stewart, J. Chem. Phys. 52, 431 (1970) Tables I–II.
_STO3G_S = [(0.109818, 0.444635), (0.405771, 0.535328), (2.22766, 0.154329)]
_STO3G_P = [(0.101920, 0.391957), (0.349745, 0.607684), (1.50331, 0.155916)]
_STO3G_D = [(0.0825, 0.219076), (0.265, 0.659015), (0.851, 0.301341)]  # rough


def _run_mopac(atoms, *, charge: int, multiplicity: int,
               solvent: Optional[str], out_stem: str,
               cubes: List[str], grid: int) -> Dict[str, Any]:
    """Run MOPAC with GRAPHF, parse the .mgf, synthesize a molden."""
    from ase.calculators.mopac import MOPAC

    keywords = ["PM7", "1SCF", "GRAPHF", "AUX",
                "LARGE=-1", "THREADS=1", "GEO-OK"]
    if charge != 0:
        keywords.append(f"CHARGE={charge}")
    if multiplicity > 1:
        keywords.append(mopac_spin_keyword(multiplicity))
        keywords.append("UHF")
    if solvent:
        eps = MOPAC_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(f"mopac: unknown solvent {solvent!r}")
        keywords.append(f"EPS={eps}")

    workdir = register_auto_tempdir(tempfile.mkdtemp(prefix="chemkit_orb_mopac_"))
    calc = MOPAC(label=os.path.join(workdir, "mopac"),
                 task=" ".join(keywords), relscf=0.01)
    atoms.calc = calc
    _ = atoms.get_potential_energy()  # force the SCF

    mgf_path = os.path.join(workdir, "mopac.mgf")
    if not os.path.isfile(mgf_path):
        raise RuntimeError(
            f"MOPAC did not write a .mgf file. workdir={workdir}. "
            "(Did GRAPHF get rejected? Check mopac.out.)"
        )

    parsed = _parse_mgf(mgf_path)
    molden_path = f"{out_stem}.molden"
    _emit_molden_from_mgf(parsed, molden_path)

    # Also drop a copy of the raw .mgf next to the molden, for users who'd
    # rather feed Jmol/Multiwfn the native MOPAC output.
    mgf_dst = f"{out_stem}.mgf"
    shutil.copyfile(mgf_path, mgf_dst)

    cube_paths: Dict[str, str] = {}
    n_orbitals = len(parsed["mos"])
    # Reuse the same pyscf molden-load → cubegen path used by xtb.
    if cubes:
        from pyscf.tools import molden as pmolden, cubegen
        mol, mo_energy, mo_coeff, mo_occ, _irrep, _spin = pmolden.load(molden_path)
        resolved = _resolve_labels(cubes, mo_occ)
        for label, idx, _spin_tag in resolved:
            fname = f"{out_stem}_{_safe_label(label)}.cube"
            cubegen.orbital(mol, fname, mo_coeff[:, idx],
                            nx=grid, ny=grid, nz=grid)
            cube_paths[label] = os.path.abspath(fname)

    # MO summary directly from the parsed .mgf (energies already in eV).
    occs = [mo["occ"] for mo in parsed["mos"]]
    energies_eV = [mo["energy_eV"] for mo in parsed["mos"]]
    mo_summary = _mo_summary_eV(occs, energies_eV)

    return {
        "molden_path": os.path.abspath(molden_path),
        "mgf_path": os.path.abspath(mgf_dst),
        "cube_paths": cube_paths,
        "n_orbitals": n_orbitals,
        "grid_resolution": grid if cubes else None,
        "mo_summary": mo_summary,
        "warnings": [
            "MOPAC orbitals are PM7 STO basis; cubes here re-fit each STO "
            "as STO-3G (3 primitive Gaussians). Shapes are qualitatively "
            "correct but absolute amplitudes differ from a true PM7 plot."
        ],
        "mopac_workdir": workdir,
    }


def _mo_summary_eV(occs: List[float], energies_eV: List[float]) -> Dict[str, Any]:
    """Restricted-only MO summary built straight from eV lists."""
    occupied = [i for i, o in enumerate(occs) if o > 0]
    if not occupied:
        return {"unrestricted": False}
    homo = occupied[-1]
    lumo = homo + 1 if homo + 1 < len(occs) else None
    return {
        "unrestricted": False,
        "homo_idx_1based": homo + 1,
        "homo_energy_eV": float(energies_eV[homo]),
        "lumo_idx_1based": lumo + 1 if lumo is not None else None,
        "lumo_energy_eV": float(energies_eV[lumo]) if lumo is not None else None,
    }


# ---- mgf parser -----------------------------------------------------------

def _parse_mgf(path: str) -> Dict[str, Any]:
    """Parse a MOPAC GRAPHF .mgf file into a dict of {atoms, exponents, mos}."""
    with open(path) as f:
        lines = [ln.rstrip("\n") for ln in f]

    # Line 0: "    <natom> MOPAC-Graphical data Version ..."
    first = lines[0].split()
    natom = int(first[0])

    atoms: List[Dict[str, Any]] = []
    for i in range(1, natom + 1):
        parts = lines[i].split()
        Z = int(parts[0])
        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        # parts[4] is the partial charge — not needed for the wavefunction.
        atoms.append({"Z": Z, "xyz_A": (x, y, z)})

    # Lines natom+1 .. 2*natom: STO exponents (zs zp zd) per atom.
    for j, i in enumerate(range(natom + 1, 2 * natom + 1)):
        parts = lines[i].split()
        zs = float(parts[0])
        zp = float(parts[1]) if len(parts) > 1 else 0.0
        zd = float(parts[2]) if len(parts) > 2 else 0.0
        atoms[j]["zs"] = zs
        atoms[j]["zp"] = zp
        atoms[j]["zd"] = zd

    # Build the basis-function order so we can pair coefficients with shells.
    # Per atom: [s] then [px,py,pz] if zp>0 then [dxy,dxz,dyz,d(x²-y²),dz²]
    # if zd>0. (MOPAC uses 5d real spherical harmonics.)
    nbf_per_atom: List[int] = []
    for a in atoms:
        n = 1
        if a["zp"] > 0:
            n += 3
        if a["zd"] > 0:
            n += 5
        nbf_per_atom.append(n)
    nbf = sum(nbf_per_atom)

    # Parse ORBITAL records. Each is:
    #   ORBITAL <occ> <sym>    <energy_eV>
    # followed by Fortran-style coefficient lines (each value is 14 chars,
    # exponent uses 'D'); coefficients run until we've collected `nbf` floats.
    mos: List[Dict[str, Any]] = []
    idx = 2 * natom + 1
    while idx < len(lines):
        ln = lines[idx]
        if "INVERSE_MATRIX" in ln or "Keywords:" in ln:
            break
        if not ln.lstrip().startswith("ORBITAL"):
            idx += 1
            continue
        parts = ln.split()
        # parts: ['ORBITAL', '<occ>', '<sym>', '<energy>']
        occ = float(parts[1])
        energy_eV = float(parts[-1])
        idx += 1
        coeffs: List[float] = []
        while len(coeffs) < nbf and idx < len(lines):
            ln2 = lines[idx]
            # Coefficient tokens are fixed-width (16 chars typically), Fortran
            # 'D' exponent. Use whitespace split then post-fix 'D' -> 'E'.
            for tok in _fortran_floats(ln2):
                coeffs.append(tok)
                if len(coeffs) == nbf:
                    break
            idx += 1
        mos.append({"occ": occ, "energy_eV": energy_eV, "coeffs": coeffs})
    return {"atoms": atoms, "nbf_per_atom": nbf_per_atom, "mos": mos}


_FLOAT_TOKEN_RE = re.compile(r"[-+]?\d+\.\d+(?:[DdEe][-+]?\d+)?")


def _fortran_floats(line: str) -> List[float]:
    """Extract Fortran-format floats from a line, normalizing D/d exponents."""
    out = []
    for tok in _FLOAT_TOKEN_RE.findall(line):
        out.append(float(tok.replace("D", "E").replace("d", "e")))
    return out


# ---- molden emitter -------------------------------------------------------

# Element symbols 1..36 (covers anything MOPAC routinely runs).
_ELEMENTS = [
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr",
]


def _emit_molden_from_mgf(parsed: Dict[str, Any], out_path: str) -> None:
    """Write a [Molden Format] file from parsed MOPAC GRAPHF data."""
    atoms = parsed["atoms"]
    mos = parsed["mos"]
    lines: List[str] = ["[Molden Format]", "[Title]", "Synthesized from MOPAC GRAPHF",
                        "[Atoms] Angs"]
    for i, a in enumerate(atoms, start=1):
        sym = _ELEMENTS[a["Z"] - 1] if 1 <= a["Z"] <= len(_ELEMENTS) else f"X{a['Z']}"
        x, y, z = a["xyz_A"]
        lines.append(f"{sym:<3s}  {i:3d}  {a['Z']:3d}  {x:16.10f}  {y:16.10f}  {z:16.10f}")
    lines.append("[GTO]")
    for i, a in enumerate(atoms, start=1):
        lines.append(f"  {i} 0")
        # s shell
        zs = a["zs"]
        lines.append(f" s   {len(_STO3G_S)}  1.00")
        for alpha0, c in _STO3G_S:
            lines.append(f"   {alpha0 * zs * zs:18.10E}   {c:18.10E}")
        # p shell
        if a["zp"] > 0:
            zp = a["zp"]
            lines.append(f" p   {len(_STO3G_P)}  1.00")
            for alpha0, c in _STO3G_P:
                lines.append(f"   {alpha0 * zp * zp:18.10E}   {c:18.10E}")
        # d shell
        if a["zd"] > 0:
            zd = a["zd"]
            lines.append(f" d   {len(_STO3G_D)}  1.00")
            for alpha0, c in _STO3G_D:
                lines.append(f"   {alpha0 * zd * zd:18.10E}   {c:18.10E}")
        lines.append("")  # blank line separates atoms in [GTO]
    # Spherical d harmonics (matches MOPAC's 5d convention).
    lines.append("[5D]")
    lines.append("[MO]")
    for k, mo in enumerate(mos, start=1):
        lines.append(f" Sym=     {k}a")
        lines.append(f" Ene= {mo['energy_eV'] / HARTREE_TO_EV:18.10f}")
        lines.append(" Spin= Alpha")
        lines.append(f" Occup= {mo['occ']:8.4f}")
        for j, c in enumerate(mo["coeffs"], start=1):
            lines.append(f"  {j:5d}  {c:18.10E}")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
