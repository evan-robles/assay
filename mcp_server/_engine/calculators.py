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
# MOPAC spin keywords. Map covers up to mult=11 (decuplet) which is more than
# enough for any real molecule — even Mn²⁺/Fe³⁺ high-spin sit at mult ≤ 6.
_MOPAC_SPIN_NAMES = {
    2: "DOUBLET",  3: "TRIPLET",  4: "QUARTET",  5: "QUINTET",
    6: "SEXTET",   7: "SEPTET",   8: "OCTET",    9: "NONET",
}

def mopac_spin_keyword(multiplicity: int) -> str:
    """Return the MOPAC keyword for a given spin multiplicity. Raises for
    closed-shell (multiplicity ≤ 1) and for values outside MOPAC's table."""
    if multiplicity <= 1:
        raise ValueError(
            f"mopac_spin_keyword: multiplicity must be > 1 (got {multiplicity}); "
            "closed-shell calculations don't take a spin keyword."
        )
    name = _MOPAC_SPIN_NAMES.get(int(multiplicity))
    if name is None:
        raise ValueError(
            f"MOPAC does not support spin multiplicity {multiplicity}. "
            f"Known: {sorted(_MOPAC_SPIN_NAMES)}."
        )
    return name


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


# Track tempdirs allocated implicitly by build_calculator so we can clean
# them up at process exit. Tempdirs registered here are NOT surfaced in the
# result JSON (caller passed workdir=None, so the path isn't known outside
# this module). Tasks that expose their workdir to the user (freq, ts, irc,
# confsearch) bypass build_calculator's allocation by passing workdir=... in.
_AUTO_TEMPDIRS: list = []

def register_auto_tempdir(path: str) -> str:
    """Mark a workdir for cleanup at process exit. Call from tasks whose
    workdir is NOT surfaced in the result JSON (intermediate freq/opt
    preopt dirs, vibration finite-difference caches, etc.). Tasks that
    expose `*_workdir` to the user should skip this — those need to
    survive past the chemkit process so the user can inspect the files.

    Returns the path so callers can write `workdir = register_auto_tempdir(
    tempfile.mkdtemp(prefix='...'))` in one line.
    """
    _AUTO_TEMPDIRS.append(path)
    return path

def _cleanup_auto_tempdirs():
    import shutil as _sh
    for d in _AUTO_TEMPDIRS:
        try:
            _sh.rmtree(d, ignore_errors=True)
        except Exception:
            pass
import atexit as _atexit
_atexit.register(_cleanup_auto_tempdirs)


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

    If `workdir` is None a fresh tempdir is allocated and registered for
    auto-cleanup at process exit. Callers that want the workdir to persist
    past the chemkit run (e.g. so result['mopac_workdir'] is still readable
    afterwards) must pass `workdir=...` explicitly.
    """
    method = method.lower()
    if workdir is None:
        workdir = tempfile.mkdtemp(prefix=f"chemkit_{method}_")
        _AUTO_TEMPDIRS.append(workdir)

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

    DFT tier presets bundle (xc, basis, grid_level); explicit
    `--functional`/`--basis` override the tier defaults. HF takes only a
    `--basis` (default def2-tzvp). All PySCF runs use exact integrals
    (no density fitting) — true RKS/UKS and RHF/UHF.
    """
    try:
        from .backends.pyscf import (
            PySCFCalculator, resolve_dft_tier, HF_DEFAULT_BASIS,
        )
        from .backends.pyscf.hf import HF_TIERS, DEFAULT_TIER as HF_DEFAULT_TIER
    except ImportError as e:
        raise ImportError(
            f"chemkit.backends.pyscf is unavailable ({e}). "
            "Install pyscf to use --method dft or --method hf."
        )

    # PySCF log verbosity. Set once per process from the CLI (--verbose ->
    # CHEMKIT_PYSCF_VERBOSE) so every build_calculator call picks it up without
    # threading `verbose` through every task signature. Defaults to 4 (rich
    # SCF/optimizer detail) so the live .out log is useful out of the box.
    try:
        pyscf_verbose = int(os.environ.get("CHEMKIT_PYSCF_VERBOSE", "4"))
    except ValueError:
        pyscf_verbose = 4

    if method == "dft":
        cfg = resolve_dft_tier(tier, functional, basis)
        calc = PySCFCalculator(
            method="dft",
            xc=cfg["xc"],
            basis=cfg["basis"],
            grid_level=cfg["grid"],
            scf_tol=cfg["scf_tol"],
            max_cycle=cfg["max_cycle"],
            # auxbasis left as None: the density-fitting auxiliary basis is
            # chosen in build_mean_field() to match the functional (JK-fit for
            # hybrids, J-fit for pure functionals).
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            verbose=pyscf_verbose,
        )
        calc._chemkit_tier = cfg["tier"]
        calc._chemkit_functional = cfg["xc"]
        # Read the post-promotion basis off the calculator — PySCFCalculator
        # auto-promotes def2-tzvp → def2-tzvpd etc. for anions, so cfg["basis"]
        # would otherwise lie about what was actually used.
        calc._chemkit_basis = calc.basis
    else:  # hf
        used_basis = basis or HF_DEFAULT_BASIS
        hf_tier = (tier or HF_DEFAULT_TIER).lower()
        if hf_tier not in HF_TIERS:
            raise ValueError(f"Unknown HF tier {tier!r}. Choose from {sorted(HF_TIERS)}.")
        hf_cfg = HF_TIERS[hf_tier]
        calc = PySCFCalculator(
            method="hf",
            basis=used_basis,
            scf_tol=hf_cfg["scf_tol"],
            max_cycle=hf_cfg["max_cycle"],
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            verbose=pyscf_verbose,
        )
        calc._chemkit_tier = hf_tier
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
                from .backends.pyscf.scf import pack_scf_result, _report_auxbasis
                extras.update(pack_scf_result(mf))
                # Report the integral treatment honestly, read off the actual
                # mean-field object. chemkit runs EXACT RKS/UKS / RHF/UHF (no
                # density fitting) by default; `_report_auxbasis` returns None
                # when no DF is attached.
                aux = _report_auxbasis(mf)
                extras["density_fit"] = aux is not None
                extras["auxbasis"] = aux
                extras["integral_treatment"] = (
                    f"density fitting (RI, auxbasis={aux})" if aux is not None
                    else "exact (no density fitting)"
                )
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
        task_keywords.append(mopac_spin_keyword(multiplicity))
        task_keywords.append("UHF")
    if solvent:
        eps = MOPAC_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(f"mopac: unknown solvent {solvent!r}")
        task_keywords.append(f"EPS={eps}")
    # Always request ENPART + AUX so we can recover the absolute electronic energy.
    # THREADS scales with available cores; honor CHEMKIT_MOPAC_THREADS override.
    n_threads = int(os.environ.get("CHEMKIT_MOPAC_THREADS") or (os.cpu_count() or 1))
    task_keywords += [
        "GRADIENTS", "AUX", "ENPART", "LARGE=-1", f"THREADS={n_threads}", "GEO-OK",
    ]

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
        # CODATA 2022: 27.211386245981 eV. NIST,
        # https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15).
        energy_eV = float(m.group(1)) * 27.211386245981
        self.results["energy"] = energy_eV
        return energy_eV

    def calculate(self, atoms, properties, system_changes):
        self.atoms = atoms
        self.get_potential_energy(atoms)

    def get_property(self, name, atoms=None, allow_calculation=True):
        if name == "energy":
            return self.get_potential_energy(atoms)
        raise NotImplementedError(name)
