"""Mean-field machinery shared by every PySCF method.

- Pick RKS/UKS (or RHF/UHF) based on multiplicity
- Attach an implicit solvent model (ddCOSMO by default)
- Enable density fitting (RI-J) with a matching auxiliary basis
- Pack a converged SCF object into the chemkit JSON schema
"""
from __future__ import annotations
import sys
from typing import Any, Dict, Optional


# Maps chemkit's friendly solvent names to PySCF's solvent presets.
# PySCF's pcm/ddCOSMO module knows these directly via `.eps = ...`; we keep
# a dielectric table here so the interface mirrors the xtb/MOPAC backends.
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


def _functional_needs_exact_exchange(xc: str) -> bool:
    """True if `xc` includes any exact (HF) exchange — i.e. is a (range-separated)
    hybrid. Such functionals build a K matrix, which density fitting must fit
    with a JK auxiliary basis, NOT a Coulomb-only (J) one."""
    try:
        from pyscf import dft as dft_mod
        omega, alpha, hyb = dft_mod.numint.NumInt().rsh_and_hybrid_coeff(xc, spin=0)
        return (abs(hyb) > 1e-10) or (abs(alpha) > 1e-10) or (abs(omega) > 1e-10)
    except Exception:
        # If we can't tell, assume it might need K and use the safe JK auxbasis.
        return True


# Auxiliary-basis choices for density fitting.
#   - J only  (Coulomb)            -> a "jfit"  basis is correct and cheaper
#   - J and K (Coulomb + exchange) -> a "jkfit" basis is REQUIRED; using a
#     jfit basis to fit the exchange (K) matrix introduces a systematic error
#     (~0.1-0.8 mEh here), which is wrong for every hybrid functional and for HF.
# def2-universal-* covers the whole def2 orbital-basis family (svp/tzvp/qzvpp).
AUXBASIS_J = "def2-universal-jfit"
AUXBASIS_JK = "def2-universal-jkfit"


def build_mean_field(
    mol,
    *,
    method: str = "dft",
    xc: Optional[str] = None,
    grid_level: int = 3,
    scf_tol: float = 1e-8,
    max_cycle: Optional[int] = None,
    density_fit: bool = False,
    auxbasis: Optional[str] = None,
    solvent: Optional[str] = None,
):
    """Construct a converged-or-ready-to-converge SCF/KS object.

    method: 'dft' or 'hf'
    xc: libxc functional string when method == 'dft' (e.g. 'wb97x_d3bj')

    By default this runs EXACT (analytic four-center integral) Kohn-Sham
    (RKS/UKS) or Hartree-Fock (RHF/UHF) with NO density fitting — the most
    accurate option and the chemkit default.

    Density fitting (the resolution-of-identity / RI approximation to the
    two-electron integrals) is OFF by default. If a caller explicitly sets
    `density_fit=True`, the auxiliary basis is chosen to match the integrals
    the method actually builds: a JK-fit auxbasis when exact exchange is
    present (HF, or hybrid/RSH functionals), a Coulomb-only J-fit auxbasis for
    pure functionals. An explicit `auxbasis` overrides that choice.
    """
    method = method.lower()
    is_open_shell = mol.spin != 0

    if method == "dft":
        from pyscf import dft as dft_mod
        if xc is None:
            raise ValueError("DFT requires an xc functional.")
        mf = dft_mod.UKS(mol) if is_open_shell else dft_mod.RKS(mol)
        mf.xc = xc
        mf.grids.level = int(grid_level)
        needs_k = _functional_needs_exact_exchange(xc)
    elif method == "hf":
        from pyscf import scf as scf_mod
        mf = scf_mod.UHF(mol) if is_open_shell else scf_mod.RHF(mol)
        needs_k = True  # Hartree-Fock is 100% exact exchange.
    else:
        raise ValueError(f"Unknown PySCF method {method!r}")

    if density_fit:
        # Correct auxbasis for what's being fit: JK whenever exact exchange is
        # present, J-only for pure functionals. Caller override wins.
        aux = auxbasis if auxbasis is not None else (
            AUXBASIS_JK if needs_k else AUXBASIS_J
        )
        mf = mf.density_fit(auxbasis=aux)

    if solvent:
        # ddCOSMO is native to PySCF; it needs a numeric dielectric, so look the
        # solvent name up in PYSCF_SOLVENT_EPS and set it directly.
        eps = PYSCF_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(
                f"PySCF backend: unknown solvent {solvent!r}. "
                f"Known: {sorted(PYSCF_SOLVENT_EPS)}"
            )
        from pyscf import solvent as solv_mod
        mf = solv_mod.ddCOSMO(mf)
        mf.with_solvent.eps = eps

    mf.conv_tol = float(scf_tol)
    if max_cycle is not None:
        mf.max_cycle = int(max_cycle)
    # Keep PySCF's SCF log off stdout (reserved for the result JSON) and on
    # stderr, where the live .out log captures it. The density_fit/solvent
    # wrappers above can create fresh objects, so set this on the final mf.
    mf.stdout = sys.stderr
    return mf


def _report_auxbasis(mf):
    """Return the density-fitting auxiliary basis actually attached to `mf`
    (for honest reporting in the result JSON), or None if DF isn't in use.
    Handles solvent-wrapped objects whose `.with_df` lives on the inner mf."""
    obj = getattr(mf, "_scf", mf)  # unwrap solvent/other decorators if present
    with_df = getattr(obj, "with_df", None) or getattr(mf, "with_df", None)
    if with_df is None:
        return None
    return getattr(with_df, "auxbasis", None)


def pack_scf_result(mf) -> Dict[str, Any]:
    """Extract the standard chemkit per-method block from a converged SCF.

    Returns the contents that go under `code_specific` — HOMO/LUMO, dipole,
    SCF iteration count, dispersion contribution (if applicable). The caller
    wraps this in `base_result` + `energy_block_from_eV`.
    """
    import numpy as np

    # CODATA 2022: Hartree energy in eV = 27.211 386 245 981(30) eV.
    # Ref: Mohr, Tiesinga, Newell, Taylor, CODATA 2022, NIST,
    # https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15).
    HARTREE_TO_EV = 27.211386245981

    out: Dict[str, Any] = {
        "scf_converged": bool(getattr(mf, "converged", False)),
        "scf_cycles": int(getattr(mf, "cycles", 0) or 0),
    }

    # Orbital eigenvalues. For UKS/UHF, mo_energy is a (2, n_mo) array or a
    # 2-tuple — α and β channels. The reported HOMO is the highest occupied
    # across BOTH channels, and LUMO is the lowest unoccupied across both;
    # the gap is the difference. (Previously the α channel was reported
    # alone, which is wrong whenever β HOMO sits above α HOMO — common in
    # high-spin systems with significant exchange splitting.)
    try:
        mo_energy = mf.mo_energy
        mo_occ = mf.mo_occ
        is_uks = isinstance(mo_energy, (list, tuple)) or (
            hasattr(mo_energy, "ndim") and mo_energy.ndim == 2
        )
        out["spin_unrestricted"] = bool(is_uks)

        if is_uks:
            e_a = np.asarray(mo_energy[0])
            e_b = np.asarray(mo_energy[1])
            occ_a = np.asarray(mo_occ[0])
            occ_b = np.asarray(mo_occ[1])
            # Per-channel arrays for the frontier task; consumers that need
            # spin-resolved gaps can use these directly.
            out["orbital_energies_eV"] = {
                "alpha": (e_a * HARTREE_TO_EV).tolist(),
                "beta": (e_b * HARTREE_TO_EV).tolist(),
            }
            out["orbital_occupations"] = {
                "alpha": occ_a.tolist(),
                "beta": occ_b.tolist(),
            }
            # Merge channels with occupation 1.0 per electron and find HOMO/LUMO
            # in the merged set. For UHF, occupied means occ > 0.5 (each channel
            # contributes 0 or 1).
            occ_thresh = 0.5
            homo_candidates = []
            lumo_candidates = []
            for e, occ in ((e_a, occ_a), (e_b, occ_b)):
                occ_idx = np.where(occ > occ_thresh)[0]
                vir_idx = np.where(occ < occ_thresh)[0]
                if occ_idx.size:
                    homo_candidates.append(float(e[occ_idx[-1]]) * HARTREE_TO_EV)
                if vir_idx.size:
                    lumo_candidates.append(float(e[vir_idx[0]]) * HARTREE_TO_EV)
            if homo_candidates and lumo_candidates:
                homo = max(homo_candidates)
                lumo = min(lumo_candidates)
                out["homo_eV"] = homo
                out["lumo_eV"] = lumo
                out["homo_lumo_gap_eV"] = lumo - homo
        else:
            e_a = np.asarray(mo_energy)
            occ_a = np.asarray(mo_occ)
            out["orbital_energies_eV"] = (e_a * HARTREE_TO_EV).tolist()
            out["orbital_occupations"] = occ_a.tolist()
            occ_idx = np.where(occ_a > 1e-6)[0]
            vir_idx = np.where(occ_a < 1e-6)[0]
            if occ_idx.size and vir_idx.size:
                homo = float(e_a[occ_idx[-1]]) * HARTREE_TO_EV
                lumo = float(e_a[vir_idx[0]]) * HARTREE_TO_EV
                out["homo_eV"] = homo
                out["lumo_eV"] = lumo
                out["homo_lumo_gap_eV"] = lumo - homo
    except Exception:
        pass

    # Dipole moment (Debye); cheap, always available post-SCF.
    # Convention matches chemkit's xtb/mopac extras: `dipole_debye` is the
    # scalar magnitude (consumed by tasks like electrostatics), the vector
    # lives at `dipole_vector_debye`.
    try:
        d = mf.dip_moment(unit="Debye", verbose=0)
        out["dipole_vector_debye"] = [float(x) for x in d]
        out["dipole_debye"] = float(np.linalg.norm(d))
    except Exception:
        pass

    # Mulliken partial charges — needed by the electrostatics/fukui tasks.
    try:
        # mulliken_pop returns (pop, charges); charges length = n_atoms.
        _, q_mulliken = mf.mulliken_pop(verbose=0)
        out["partial_charges"] = [float(x) for x in q_mulliken]
        out["partial_charges_scheme"] = "Mulliken (PySCF)"
    except Exception:
        pass

    return out
