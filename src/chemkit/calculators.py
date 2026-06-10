"""ASE calculator factory for xtb (xtb-python or CLI), MOPAC, optional COSMO solvation."""
from __future__ import annotations
import os
import shutil
import tempfile
from typing import Optional

import numpy as np


XTB_SOLVENT_MAP = {
    # ALPB solvents understood by xtb
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

# Solvents supported by the xtb CLI's --alpb flag but NOT by the xtb-python
# Solvent enum exposed via the ASE wrapper. For these we must route through the
# CLI path (_XtbCliCalculator) rather than silently dropping the solvent.
XTB_PYTHON_UNSUPPORTED_SOLVENTS = {"octanol"}

# MOPAC COSMO: EPS=<dielectric>; pull common solvent constants.
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


def build_calculator(
    method: str,
    *,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    workdir: Optional[str] = None,
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
):
    """Return an ASE calculator for the requested method.

    method: 'xtb' (GFN2-xTB), 'mopac' (PM7), 'dft' (PySCF DFT), 'hf' (PySCF HF)
    multiplicity: 2S+1 (ASE uses unpaired-electron count internally for some calcs)
    solvent: e.g. 'water' for ALPB (xtb) or COSMO EPS=... (MOPAC). None = gas phase.
    tier/functional/basis: PySCF-only knobs. Silently ignored for xtb/mopac.
    """
    method = method.lower()
    if workdir is None:
        workdir = tempfile.mkdtemp(prefix=f"chemkit_{method}_")

    if method == "xtb":
        return _build_xtb(charge, multiplicity, solvent, workdir)
    if method == "mopac":
        return _build_mopac(charge, multiplicity, solvent, workdir)
    if method in ("dft", "hf"):
        return _build_pyscf(
            method, charge, multiplicity, solvent, workdir,
            tier=tier, functional=functional, basis=basis,
        )
    raise ValueError(
        f"Unknown method {method!r}. Expected 'xtb', 'mopac', 'dft', or 'hf'."
    )


def _build_pyscf(method, charge, multiplicity, solvent, workdir,
                 *, tier=None, functional=None, basis=None):
    """Dispatch DFT/HF to the PySCF backend (lazy import).

    The PySCF backend lives in chemkit.backends.pyscf and exposes an
    ASE-compatible Calculator class. We import lazily so users without
    PySCF installed can still use xtb/mopac.

    DFT tier presets bundle (xc, basis, grid_level, auxbasis); explicit
    `--functional`/`--basis` override the tier defaults. HF takes only a
    `--basis` (default def2-tzvp).
    """
    try:
        from .backends.pyscf import (
            PySCFCalculator, resolve_dft_tier, HF_DEFAULT_BASIS,
        )
    except ImportError as e:
        raise ImportError(
            f"chemkit.backends.pyscf is unavailable ({e}). "
            "Install pyscf to use --method dft or --method hf."
        )

    if method == "dft":
        cfg = resolve_dft_tier(tier, functional, basis)
        calc = PySCFCalculator(
            method="dft",
            xc=cfg["xc"],
            basis=cfg["basis"],
            grid_level=cfg["grid"],
            auxbasis=cfg["aux"],
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
        )
        calc._chemkit_tier = cfg["tier"]
        calc._chemkit_functional = cfg["xc"]
        # Read the post-promotion basis off the calculator — PySCFCalculator
        # auto-promotes def2-tzvp → def2-tzvpd etc. for anions, so cfg["basis"]
        # would otherwise lie about what was actually used.
        calc._chemkit_basis = calc.basis
    else:  # hf
        used_basis = basis or HF_DEFAULT_BASIS
        calc = PySCFCalculator(
            method="hf",
            basis=used_basis,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
        )
        calc._chemkit_tier = None
        calc._chemkit_functional = None
        calc._chemkit_basis = calc.basis  # honors anion auto-promotion

    calc._chemkit_method = method
    calc._chemkit_workdir = workdir
    return calc


# ---------------------------------------------------------------------------
# Per-method label helpers used by task modules to populate result JSON
# without scattering hardcoded strings.
# ---------------------------------------------------------------------------

def method_label(method: str, calc=None) -> str:
    """Human-readable method label for the `method` field of result JSON.

    For DFT we want to surface the functional + basis (or tier preset) since
    those are the actual scientific knobs. For xtb/mopac we just return the
    canonical name.
    """
    m = (method or "").lower()
    if m == "xtb":
        return "GFN2-xTB"
    if m == "mopac":
        return "PM7"
    if m in ("dft", "hf"):
        if calc is not None:
            functional = getattr(calc, "_chemkit_functional", None)
            basis = getattr(calc, "_chemkit_basis", None)
            tier = getattr(calc, "_chemkit_tier", None)
            if m == "hf":
                return f"HF/{basis}" if basis else "HF"
            # DFT
            if functional and basis:
                return f"{functional}/{basis}"
            if functional:
                return functional
            if tier:
                return f"DFT[{tier}]"
            return "DFT"
        return m.upper()
    return m


def program_label(method: str) -> str:
    """Underlying program string for the `program` field."""
    m = (method or "").lower()
    if m == "xtb":
        return "xtb"
    if m == "mopac":
        return "mopac"
    if m in ("dft", "hf"):
        return "pyscf"
    return m


def collect_calc_extras(method: str, atoms, calc) -> dict:
    """Return code-specific extras dict appropriate for `method`.

    For xtb: tries to recover HOMO/LUMO via xtb-python.
    For mopac: parses HOMO/LUMO, dipole, HoF, ENPART from the workdir.
    For dft/hf: pulls anything the PySCF calculator stashed on itself
    (e.g. orbital energies, dipole). Returns {} if nothing is available.
    """
    m = (method or "").lower()
    extras: dict = {}
    if m == "xtb":
        try:
            from .tasks.sp import _xtb_homo_lumo  # local import to avoid cycle at top
            extras.update(_xtb_homo_lumo(atoms, calc) or {})
        except Exception:
            pass
    elif m == "mopac":
        try:
            from .tasks._mopac_parsers import parse_mopac_extras
        except ImportError:
            return extras
        workdir = getattr(calc, "_chemkit_workdir", None)
        if workdir:
            extras.update(parse_mopac_extras(workdir) or {})
    elif m in ("dft", "hf"):
        mf = getattr(calc, "mean_field", None)
        if mf is not None:
            try:
                from .backends.pyscf.scf import pack_scf_result
                extras.update(pack_scf_result(mf))
            except Exception:
                pass
        functional = getattr(calc, "_chemkit_functional", None)
        basis = getattr(calc, "_chemkit_basis", None)
        tier = getattr(calc, "_chemkit_tier", None)
        if functional:
            extras["functional"] = functional
        if basis:
            extras["basis"] = basis
        if tier:
            extras["tier"] = tier
    return extras


def _build_xtb(charge, multiplicity, solvent, workdir):
    """Prefer xtb-python (compiled); fall back to subprocess via a thin shim.

    For solvents the xtb-python Solvent enum doesn't expose (octanol etc.) we
    route through the CLI even when xtb-python is installed — otherwise the
    ASE wrapper silently drops the solvent and reports gas-phase energies.
    """
    sol_key = solvent.lower() if solvent else None
    if sol_key and sol_key not in XTB_SOLVENT_MAP:
        raise ValueError(f"xtb: unknown solvent {solvent!r}")
    if sol_key in XTB_PYTHON_UNSUPPORTED_SOLVENTS:
        return _XtbCliCalculator(
            charge=charge, uhf=max(0, multiplicity - 1),
            solvent=solvent, workdir=workdir,
        )
    try:
        from xtb.ase.calculator import XTB
        kwargs = {"method": "GFN2-xTB"}
        if solvent:
            kwargs["solvent"] = XTB_SOLVENT_MAP[sol_key]
        calc = XTB(**kwargs)
        calc._chemkit_charge = charge
        calc._chemkit_uhf = max(0, multiplicity - 1)
        return calc
    except ImportError:
        return _XtbCliCalculator(
            charge=charge,
            uhf=max(0, multiplicity - 1),
            solvent=solvent,
            workdir=workdir,
        )


def _build_mopac(charge, multiplicity, solvent, workdir):
    from ase.calculators.mopac import MOPAC

    task_keywords = ["PM7"]
    if charge != 0:
        task_keywords.append(f"CHARGE={charge}")
    if multiplicity > 1:
        names = {2: "DOUBLET", 3: "TRIPLET", 4: "QUARTET", 5: "QUINTET"}
        spin = names.get(multiplicity)
        if spin:
            task_keywords.append(spin)
        if multiplicity > 1:
            task_keywords.append("UHF")
    if solvent:
        eps = MOPAC_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(f"mopac: unknown solvent {solvent!r}")
        task_keywords.append(f"EPS={eps}")
    # Always request ENPART + AUX so we can recover the absolute electronic energy.
    task_keywords += ["GRADIENTS", "AUX", "ENPART", "LARGE=-1", "THREADS=1", "GEO-OK"]

    calc = MOPAC(
        label=os.path.join(workdir, "mopac"),
        task=" ".join(task_keywords),
        relscf=0.01,
    )
    calc._chemkit_keywords = task_keywords
    calc._chemkit_workdir = workdir
    return calc


def apply_calc_to_atoms(atoms, calc):
    """Attach calc to atoms and propagate xtb charge/uhf when needed.

    xtb-python's XTB calculator reads total charge and unpaired-electron count
    from `atoms.get_initial_charges().sum()` / `get_initial_magnetic_moments().sum()`
    — NOT from `atoms.info`. Only the sums matter to xtb (it solves for the
    requested total charge/spin, not a per-atom partition), so we dump the
    full charge/uhf onto the first atom and zero the rest.
    """
    if hasattr(calc, "_chemkit_charge"):
        charges = np.zeros(len(atoms))
        charges[0] = calc._chemkit_charge
        atoms.set_initial_charges(charges)

        magmoms = np.zeros(len(atoms))
        magmoms[0] = calc._chemkit_uhf
        atoms.set_initial_magnetic_moments(magmoms)
    atoms.calc = calc
    return atoms


class _XtbCliCalculator:
    """Minimal ASE-compatible wrapper around the `xtb` CLI when xtb-python is absent."""

    implemented_properties = ["energy", "forces"]
    name = "xtb-cli"

    def __init__(self, *, charge=0, uhf=0, solvent=None, workdir):
        if not shutil.which("xtb"):
            raise FileNotFoundError("xtb CLI not found and xtb-python unavailable.")
        self.charge = charge
        self.uhf = uhf
        self.solvent = solvent
        self.workdir = workdir
        self.parameters = {}
        self.results = {}
        self.atoms = None

    def get_potential_energy(self, atoms=None):
        from ase.io import write as ase_write
        import re, subprocess
        if atoms is not None:
            self.atoms = atoms
        xyz = os.path.join(self.workdir, "mol.xyz")
        ase_write(xyz, self.atoms)
        cmd = ["xtb", xyz, "--gfn", "2", "--sp",
               "--chrg", str(self.charge), "--uhf", str(self.uhf)]
        if self.solvent:
            sol = XTB_SOLVENT_MAP.get(self.solvent.lower(), self.solvent)
            cmd += ["--alpb", sol]
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=self.workdir, timeout=300)
        m = re.search(r"total energy\s+([-+]?\d+\.\d+)\s*Eh", res.stdout)
        if not m:
            raise RuntimeError("xtb CLI: could not parse total energy.\n" + res.stdout[-2000:])
        # Convert Hartree -> eV to match ASE convention.
        energy_eV = float(m.group(1)) * 27.211386245988
        self.results["energy"] = energy_eV
        return energy_eV

    def calculate(self, atoms, properties, system_changes):
        self.atoms = atoms
        self.get_potential_energy(atoms)

    def get_property(self, name, atoms=None, allow_calculation=True):
        if name == "energy":
            return self.get_potential_energy(atoms)
        raise NotImplementedError(name)
