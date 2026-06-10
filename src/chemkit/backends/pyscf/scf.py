"""Mean-field machinery shared by every PySCF method.

- Pick RKS/UKS (or RHF/UHF) based on multiplicity
- Attach an implicit solvent model (ddCOSMO by default)
- Enable density fitting (RI-J) with a matching auxiliary basis
- Pack a converged SCF object into the chemkit JSON schema
"""
from __future__ import annotations
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


def build_mean_field(
    mol,
    *,
    method: str = "dft",
    xc: Optional[str] = None,
    grid_level: int = 3,
    scf_tol: float = 1e-8,
    density_fit: bool = True,
    auxbasis: str = "def2-universal-jfit",
    solvent: Optional[str] = None,
):
    """Construct a converged-or-ready-to-converge SCF/KS object.

    method: 'dft' or 'hf'
    xc: libxc functional string when method == 'dft' (e.g. 'wb97x_d3bj')
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
    elif method == "hf":
        from pyscf import scf as scf_mod
        mf = scf_mod.UHF(mol) if is_open_shell else scf_mod.RHF(mol)
    else:
        raise ValueError(f"Unknown PySCF method {method!r}")

    if density_fit:
        mf = mf.density_fit(auxbasis=auxbasis)

    if solvent:
        mf = attach_solvent(mf, solvent)

    mf.conv_tol = float(scf_tol)
    return mf


def attach_solvent(mf, solvent_name: str, model: str = "ddcosmo"):
    """Wrap an SCF object with an implicit solvent model.

    Defaults to ddCOSMO — fastest of PySCF's PCM family and well-tested.
    SMD (free-energy-of-solvation parameterization) is available via PySCF's
    smd module; expose it later if/when a `--solvent-model` flag is added.
    """
    eps = PYSCF_SOLVENT_EPS.get(solvent_name.lower())
    if eps is None:
        raise ValueError(
            f"PySCF backend: unknown solvent {solvent_name!r}. "
            f"Known: {sorted(PYSCF_SOLVENT_EPS)}"
        )

    if model.lower() == "ddcosmo":
        from pyscf import solvent as solv_mod
        mf = solv_mod.ddCOSMO(mf)
        mf.with_solvent.eps = eps
    elif model.lower() == "smd":
        from pyscf.solvent import smd as smd_mod
        mf = smd_mod.SMD(mf)
        mf.with_solvent.solvent = solvent_name.lower()
    else:
        raise ValueError(f"Unknown solvent model {model!r} (use ddcosmo or smd)")
    return mf


def pack_scf_result(mf) -> Dict[str, Any]:
    """Extract the standard chemkit per-method block from a converged SCF.

    Returns the contents that go under `code_specific` — HOMO/LUMO, dipole,
    SCF iteration count, dispersion contribution (if applicable). The caller
    wraps this in `base_result` + `energy_block_from_eV`.
    """
    import numpy as np

    HARTREE_TO_EV = 27.211386245988

    out: Dict[str, Any] = {
        "scf_converged": bool(getattr(mf, "converged", False)),
        "scf_cycles": int(getattr(mf, "cycles", 0) or 0),
    }

    # Orbital eigenvalues. UKS/UHF return a tuple (alpha, beta); we report the
    # alpha-channel HOMO/LUMO as a pragmatic default and flag the open-shell
    # case so consumers know not to over-interpret a single gap.
    try:
        mo_energy = mf.mo_energy
        mo_occ = mf.mo_occ
        if isinstance(mo_energy, (list, tuple)) or (hasattr(mo_energy, "ndim") and mo_energy.ndim == 2):
            e_a = np.asarray(mo_energy[0])
            occ_a = np.asarray(mo_occ[0])
            out["spin_unrestricted"] = True
        else:
            e_a = np.asarray(mo_energy)
            occ_a = np.asarray(mo_occ)
            out["spin_unrestricted"] = False

        # Full eigenvalue/occupation arrays — needed by the frontier task.
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
