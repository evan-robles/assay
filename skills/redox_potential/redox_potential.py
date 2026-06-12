#!/usr/bin/env python3
"""Self-contained `redox_potential` skill — chemistry engine inlined.

This single file bundles everything the `redox_potential` skill needs. It registers the
embedded engine modules into sys.modules under their real names (preserving each
module's namespace, so tasks that share function names like run()/_run_mopac do
NOT collide), then runs the chemkit CLI pinned to the `redox` subcommand.

Run standalone:  python redox_potential.py --help
"""
import base64 as _b64
import importlib.abc as _iabc
import importlib.machinery as _imach
import sys as _sys

# Lazy in-memory loader: the embedded module sources are exec'd by Python's
# normal import machinery ON FIRST IMPORT, so dependency order is driven by the
# actual `import` statements (not by us). Each module keeps its own namespace,
# so tasks that share top-level names (run(), _run_mopac, ...) never collide.
class _EmbeddedFinder(_iabc.MetaPathFinder, _iabc.Loader):
    def __init__(self, modules):
        # name -> (is_package, source_text)
        self._mods = {}
        for modname, is_pkg, payload, is_b64 in modules:
            src = _b64.b64decode(payload).decode("utf-8") if is_b64 else payload
            self._mods[modname] = (is_pkg, src)

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self._mods:
            return None
        is_pkg, _src = self._mods[fullname]
        return _imach.ModuleSpec(fullname, self, is_package=is_pkg)

    def create_module(self, spec):
        return None  # default module creation

    def exec_module(self, module):
        is_pkg, src = self._mods[module.__name__]
        if is_pkg:
            # Mark as a package so submodule + relative imports resolve.
            module.__path__ = []
        exec(compile(src, "<embedded:%s>" % module.__name__, "exec"),
             module.__dict__)


def _register_embedded(_MODULES):
    _sys.meta_path.insert(0, _EmbeddedFinder(_MODULES))


_EMBEDDED = [
    ('_engine', True, r'''"""chemkit: ASE-based computational chemistry suite."""
__version__ = "1.0.0"
''', False),
    ('_engine.io', False, r'''"""I/O helpers: read molecular geometry; write structured JSON result files."""
from __future__ import annotations
import json
import math
import os
import pathlib
import sys
from typing import Any, Dict

from ase.io import read as ase_read


def read_geometry(path: str):
    """Read xyz/sdf/pdb (anything ASE recognizes) and return an Atoms object."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Geometry file not found: {path}")
    atoms = ase_read(path)
    return atoms


def write_result(result: Dict[str, Any], out_path: str) -> str:
    """Write result dict to JSON; create parent dir if missing. Returns abs path.

    NaN and ±Infinity are coerced to None so the output is strict-JSON valid
    (browsers, Go, Rust will choke on `NaN` literals). A failed calculation
    that propagates NaN into a result field is still readable by consumers
    rather than silently producing malformed JSON.
    """
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(_scrub(result), f, indent=2, default=_default_json,
                 allow_nan=False)
    return out_path


def _default_json(o):
    # numpy scalars and arrays — must come before generic .tolist() since
    # np.bool_ defines neither tolist() (it returns a Python bool) nor a
    # numpy.integer/floating MRO link.
    try:
        import numpy as np
        if isinstance(o, np.ndarray):
            return _scrub(o.tolist())
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.floating):
            v = float(o)
            return v if math.isfinite(v) else None
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.complexfloating):
            return {"real": float(o.real), "imag": float(o.imag)}
    except ImportError:
        pass
    if isinstance(o, (pathlib.Path, os.PathLike)):
        return os.fspath(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, complex):
        return {"real": o.real, "imag": o.imag}
    if hasattr(o, "tolist"):
        return _scrub(o.tolist())
    raise TypeError(f"Not JSON-serializable: {type(o).__name__}")


def _scrub(value):
    """Recursively replace non-finite floats with None (the JSON-strict
    representation of NaN/Inf). Walks lists/tuples/dicts only — leaves
    everything else alone."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, tuple):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def cli_invocation() -> str:
    """Reconstruct the command line that produced this run (for reproducibility)."""
    return " ".join(sys.argv)


''', False),
    ('_engine.schema', False, r'''"""Shared JSON result schema used by every task."""
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
''', False),
    ('_engine.calculators', False, r'''"""ASE calculator factory for xtb (xtb-python or CLI), MOPAC, optional COSMO solvation."""
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

    DFT tier presets bundle (xc, basis, grid_level, auxbasis); explicit
    `--functional`/`--basis` override the tier defaults. HF takes only a
    `--basis` (default def2-tzvp).
    """
    try:
        from _engine.backends.pyscf import (
            PySCFCalculator, resolve_dft_tier, HF_DEFAULT_BASIS,
        )
        from _engine.backends.pyscf.hf import HF_TIERS, DEFAULT_TIER as HF_DEFAULT_TIER
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
            scf_tol=cfg["scf_tol"],
            max_cycle=cfg["max_cycle"],
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
            from _engine.tasks.sp import _xtb_homo_lumo  # local import to avoid cycle at top
            extras.update(_xtb_homo_lumo(atoms, calc) or {})
        except Exception:
            pass
    elif m == "mopac":
        try:
            from _engine.tasks._mopac_parsers import parse_mopac_extras
        except ImportError:
            return extras
        workdir = getattr(calc, "_chemkit_workdir", None)
        if workdir:
            extras.update(parse_mopac_extras(workdir) or {})
    elif m in ("dft", "hf"):
        mf = getattr(calc, "mean_field", None)
        if mf is not None:
            try:
                from _engine.backends.pyscf.scf import pack_scf_result
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
''', False),
    ('_engine.cli', False, r'''"""`chemkit` command-line interface."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from typing import List, Optional

from _engine import __version__
from _engine.io import write_result, cli_invocation


def _add_chem_options(p, *, with_input: bool = True, with_solvent: bool = True):
    """Shared CLI options. Set `with_solvent=False` for tasks where the
    solvent is fixed by the task itself (e.g. logp pins water + octanol)."""
    if with_input:
        p.add_argument("input", help="Path to input geometry (.xyz, .sdf, .pdb).")
    p.add_argument("--method", choices=["xtb", "mopac", "dft", "hf"], required=True)
    p.add_argument("--charge", type=int, default=0)
    p.add_argument("--mult", "--multiplicity", dest="multiplicity",
                   type=int, default=1, help="Spin multiplicity 2S+1 (default 1).")
    if with_solvent:
        p.add_argument("--solvent", default=None,
                       help="Implicit solvent (e.g. water, methanol, dmso). Gas phase if omitted.")
    # PySCF-only knobs; silently ignored for xtb/mopac.
    p.add_argument("--tier", choices=["fast", "standard", "accurate"], default=None,
                   help="DFT tier preset (fast=r2SCAN/def2-SVP, standard=wB97X-V/def2-TZVP, "
                        "accurate=wB97M-V/def2-QZVPP). Ignored unless --method dft.")
    p.add_argument("--functional", default=None,
                   help="DFT functional override, libxc name (e.g. b3lyp, pbe0, wb97x_v, "
                        "wb97m_v, wb97x-d3bj). Ignored unless --method dft.")
    p.add_argument("--basis", default=None,
                   help="Basis-set override for DFT/HF (e.g. def2-tzvp, cc-pvtz). "
                        "Ignored unless --method dft or --method hf.")
    p.add_argument("--out", default=None,
                   help="Output JSON path. Default: <input-stem>_<task>_<method>.json")
    _add_view_option(p)


def _add_view_option(p):
    """Add the --no-view flag. By default, geometry-producing tasks open the
    resulting structure in an in-terminal asciimol viewer when run interactively
    (a TTY) and asciimol is installed; --no-view suppresses that."""
    p.add_argument(
        "--no-view", dest="view", action="store_false", default=True,
        help="Do not open the resulting geometry in the in-terminal asciimol "
             "viewer (the viewer otherwise launches automatically on an "
             "interactive terminal when asciimol is installed).",
    )


def _add_common(p):
    """Back-compat shim — existing subparsers continue to use this."""
    _add_chem_options(p)


def _default_out(input_path: str, task: str, method: str) -> str:
    stem = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.abspath(f"{stem}_{task}_{method}.json")


# Tasks that produce a single viewable geometry, and the result-dict keys (in
# priority order) that hold its .xyz path.
_VIEW_KEYS = {
    "build":      ["xyz_path"],
    "opt":        ["optimized_xyz"],
    "ts":         ["ts_xyz"],
    "confsearch": ["best_conformer_xyz", "conformers_xyz", "all_conformers_xyz"],
    "profile":    ["ts_xyz"],
}


def _maybe_view(result: dict, task: str, view: bool) -> None:
    """Open the resulting geometry in the in-terminal asciimol viewer.

    Only fires when ALL of these hold, so it never hangs tests, pipelines, or
    agent/automation runs:
      * the user did not pass --no-view (view=True),
      * stdout AND stdin are real interactive terminals (a human is present),
      * asciimol is installed,
      * the task produced a viewable .xyz that exists on disk.

    Note: internal sub-task calls (freq->opt, build->opt, ...) go through
    task.run() directly, never cli.main(), so this only runs for the top-level
    user invocation — exactly once, on the final geometry.
    """
    import shutil as _shutil
    if not view:
        return
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        return
    if _shutil.which("asciimol") is None:
        return
    xyz = None
    for key in _VIEW_KEYS.get(task, []):
        cand = result.get(key)
        if cand and os.path.isfile(cand):
            xyz = cand
            break
    if not xyz:
        return
    print(f"\n# opening {os.path.basename(xyz)} in asciimol "
          f"(press q to quit)…", file=sys.stderr)
    try:
        subprocess.run(["asciimol", xyz])
    except (OSError, KeyboardInterrupt):
        pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chemkit",
        description="ASE-based computational chemistry suite (xtb, MOPAC).",
    )
    parser.add_argument("--version", action="version", version=f"chemkit {__version__}")
    sub = parser.add_subparsers(dest="task", required=True)

    p_sp = sub.add_parser("sp", help="Single-point energy.")
    _add_common(p_sp)

    p_opt = sub.add_parser("opt", help="Geometry optimization.")
    _add_common(p_opt)
    p_opt.add_argument("--fmax", type=float, default=0.05,
                       help="Force convergence threshold in eV/Å (default 0.05).")
    p_opt.add_argument("--steps", type=int, default=500)
    p_opt.add_argument("--xyz-out", default=None,
                       help="Optimized geometry destination. Default: <stem>_<method>_opt.xyz")

    p_freq = sub.add_parser("freq", help="Opt-freq: optimize then vibrational analysis + thermochemistry.")
    _add_common(p_freq)
    p_freq.add_argument("--temperature", type=float, default=298.15)
    p_freq.add_argument("--pressure", type=float, default=101325.0)
    p_freq.add_argument("--geometry", choices=["monatomic", "linear", "nonlinear"],
                        default=None,
                        help="Override molecular geometry (monatomic/linear/nonlinear). "
                             "If omitted, auto-detected from the input atoms.")
    p_freq.add_argument("--symmetry", type=int, default=None,
                        help="Rotational symmetry number σ. If omitted, defaults to "
                             "1 with a warning — look up σ for your point group "
                             "(H2O σ=2, NH3 σ=3, CH4/benzene σ=12) to avoid "
                             "overestimating rotational entropy by R·ln σ.")
    p_freq.add_argument(
        "--no-preopt", dest="preopt", action="store_false", default=True,
        help="Skip the automatic pre-optimization step. By default freq always "
             "optimizes the input geometry first so the Hessian is taken at a "
             "true stationary point.",
    )
    p_freq.add_argument(
        "--preopt-fmax", type=float, default=0.001,
        help="Force convergence (eV/Å) for the pre-opt step (default 0.01, "
             "tighter than `opt`'s 0.05 because residual forces propagate into "
             "near-zero imaginary modes).",
    )
    p_freq.add_argument(
        "--auto-confsearch", dest="auto_confsearch", action="store_true",
        default=False,
        help="Run an Open Babel conformer search (with PM7 postopt) before the "
             "freq step and take the lowest-energy minimum as the input geometry. "
             "Useful for flexible molecules where the user-supplied geometry "
             "may not be the global minimum; otherwise soft-mode saddles show "
             "up as spurious imaginary modes.",
    )

    p_bind = sub.add_parser("binding", help="Binding/interaction energy.")
    _add_common(p_bind)
    p_bind.add_argument("--monomer", action="append", required=True,
                        help="Path to a monomer geometry. Repeat for each fragment.")
    p_bind.add_argument("--monomer-charge", action="append", type=int, default=None)
    p_bind.add_argument("--monomer-mult", action="append", type=int, default=None)

    p_redox = sub.add_parser("redox", help="Redox potential vs SHE / Ag-AgCl / Fc+-Fc.")
    _add_common(p_redox)
    p_redox.add_argument("--ox-charge", type=int, required=True)
    p_redox.add_argument("--red-charge", type=int, required=True)
    p_redox.add_argument("--ox-mult", type=int, default=1)
    p_redox.add_argument("--red-mult", type=int, default=2)
    p_redox.add_argument("--ref", choices=["SHE", "Ag/AgCl", "Fc+/Fc"], default="SHE")
    p_redox.add_argument("--n-electrons", type=int, default=1)

    p_conf = sub.add_parser("confsearch", help="Conformer search via Open Babel (confab).")
    _add_common(p_conf)
    p_conf.add_argument("--max-conformers", type=int, default=20)
    p_conf.add_argument(
        "--postopt", choices=["none", "mopac"], default="mopac",
        help=(
            "Re-optimize CREST conformers with another method to recover "
            "shallow minima that GFN2-xTB smooths over. 'mopac' uses PM7 "
            "(default). Pass 'none' to skip."
        ),
    )
    p_conf.add_argument(
        "--postopt-rmsd", type=float, default=0.25,
        help="RMSD threshold (Å) for deduping post-optimized conformers (default 0.25).",
    )
    p_conf.add_argument(
        "--postopt-ewin", type=float, default=6.0,
        help="Energy window (kcal/mol) to keep after post-optimization (default 6.0).",
    )

    p_front = sub.add_parser(
        "frontier",
        help="Frontier molecular orbital energies + HOMO-LUMO gap (no opt).",
    )
    _add_common(p_front)
    p_front.add_argument(
        "--nfrontier", type=int, default=3,
        help="Number of occupied & virtual orbitals on each side of the gap "
             "to report (default 3).",
    )

    p_elst = sub.add_parser(
        "electrostatics",
        help="Dipole + atomic partial charges (single-point, no opt).",
    )
    _add_common(p_elst)

    p_solv = sub.add_parser(
        "solvation",
        help="ΔG_solv = E(solvated) − E(gas) at fixed geometry (electronic only).",
    )
    _add_common(p_solv)

    p_logp = sub.add_parser(
        "logp",
        help="logP from ΔG_solv(water) − ΔG_solv(octanol). Neutral species only.",
    )
    _add_chem_options(p_logp, with_solvent=False)

    p_prof = sub.add_parser(
        "profile",
        help="Reaction profile: opt(R) + opt(P) + TS search + freq×3 + IRC "
             "connectivity check + ΔE/ΔH/ΔG diagram PNG.",
    )
    p_prof.add_argument("--reactant", required=True, help="Reactant xyz.")
    p_prof.add_argument("--product", required=True, help="Product xyz.")
    p_prof.add_argument("--ts-guess", dest="ts_guess", required=True,
                        help="TS guess xyz (often from /conformational_analysis).")
    p_prof.add_argument(
        "--method", choices=["xtb", "mopac", "dft", "hf"], required=True,
        help="Same method is used for every species in the cycle.",
    )
    p_prof.add_argument("--charge", type=int, default=0)
    p_prof.add_argument("--mult", "--multiplicity", dest="multiplicity",
                        type=int, default=1)
    p_prof.add_argument("--solvent", default=None)
    p_prof.add_argument("--temperature", type=float, default=298.15)
    p_prof.add_argument("--pressure", type=float, default=101325.0)
    p_prof.add_argument(
        "--rmsd-tol", type=float, default=0.5,
        help="Å threshold for IRC-endpoint connectivity check (default 0.5).",
    )
    p_prof.add_argument(
        "--no-irc", dest="skip_irc", action="store_true", default=False,
        help="Skip the IRC connectivity check (only the RMSD-based check is "
             "available for dft/hf anyway, so this is a noop there).",
    )
    p_prof.add_argument("--tier", choices=["fast", "standard", "accurate"], default=None)
    p_prof.add_argument("--functional", default=None)
    p_prof.add_argument("--basis", default=None)
    p_prof.add_argument("--out", default=None)
    _add_view_option(p_prof)

    p_pka = sub.add_parser(
        "pka",
        help="pKa via thermodynamic cycle HA(aq) → A⁻(aq) + H⁺(aq). Requires "
             "BOTH the protonated and deprotonated xyz files.",
    )
    p_pka.add_argument("--ha", required=True, help="xyz of the protonated form (HA).")
    p_pka.add_argument("--a-minus", dest="a_minus", required=True,
                       help="xyz of the deprotonated form (A⁻).")
    p_pka.add_argument(
        "--method", choices=["xtb", "mopac", "dft", "hf"], required=True,
        help="Same method is applied to every species in the cycle.",
    )
    p_pka.add_argument(
        "--mode", choices=["absolute", "reference"], default="absolute",
        help="absolute: uses literature G(H+,aq). reference: uses a known acid "
             "(--ref-ha, --ref-a-minus, --pka-ref). Reference is far more accurate.",
    )
    p_pka.add_argument(
        "--solvent", default="water",
        help="Implicit solvent (default 'water' — required for the absolute "
             "G(H+) reference to apply).",
    )
    p_pka.add_argument("--ha-charge", type=int, default=0,
                       help="Charge of HA (default 0). A⁻ charge is HA charge − 1.")
    p_pka.add_argument("--ha-mult", type=int, default=1, help="HA multiplicity (default 1).")
    p_pka.add_argument("--a-minus-mult", type=int, default=1, help="A⁻ multiplicity (default 1).")
    p_pka.add_argument("--temperature", type=float, default=298.15)
    p_pka.add_argument("--pressure", type=float, default=101325.0)
    p_pka.add_argument(
        "--hplus-reference", default="tissandier_1998",
        choices=["tissandier_1998", "kelly_2006"],
        help="Source for G(H+,aq). Tissandier −270.28 kcal/mol (default); "
             "Kelly −265.9 kcal/mol shifts every pKa by ~1.4 units.",
    )
    # Reference-mode args
    p_pka.add_argument("--ref-ha", default=None, help="Reference acid HA xyz (reference mode).")
    p_pka.add_argument("--ref-a-minus", default=None, help="Reference base A⁻ xyz (reference mode).")
    p_pka.add_argument("--pka-ref", type=float, default=None,
                       help="Known experimental pKa of the reference acid (reference mode).")
    p_pka.add_argument("--ref-ha-charge", type=int, default=0)
    p_pka.add_argument("--ref-ha-mult", type=int, default=1)
    p_pka.add_argument("--ref-a-minus-mult", type=int, default=1)
    p_pka.add_argument("--tier", choices=["fast", "standard", "accurate"], default=None)
    p_pka.add_argument("--functional", default=None)
    p_pka.add_argument("--basis", default=None)
    p_pka.add_argument("--out", default=None)

    p_build = sub.add_parser(
        "build",
        help="Build a 3D xyz from a SMILES string OR a molecule name (Open Babel "
             "--gen3d; names are resolved online via PubChem/OPSIN/NIST).",
    )
    p_build.add_argument(
        "smiles",
        help="SMILES string (e.g. 'CCO') or a plain molecule name (e.g. "
             "'ethanol'). A name is resolved to SMILES online and the source "
             "is reported.",
    )
    p_build.add_argument(
        "--out-xyz", default=None,
        help="Destination .xyz path. Default: <input-sanitized>.xyz in cwd.",
    )
    p_build.add_argument(
        "--name", default=None,
        help="Title comment for the xyz (default: the SMILES string).",
    )
    p_build.add_argument(
        "--opt", dest="opt_method", choices=["xtb", "mopac", "dft", "hf"],
        default=None,
        help="Optional QM refinement step after the obabel build. Calls "
             "`chemkit opt` internally; the QM-relaxed xyz becomes the canonical "
             "output.",
    )
    p_build.add_argument(
        "--solvent", default=None,
        help="Implicit solvent for the optional QM step (ignored without --opt).",
    )
    p_build.add_argument(
        "--charge", type=int, default=None,
        help="Net charge forwarded to the QM step (default 0).",
    )
    p_build.add_argument(
        "--mult", "--multiplicity", dest="multiplicity", type=int, default=None,
        help="Spin multiplicity forwarded to the QM step (default 1).",
    )
    p_build.add_argument("--tier", choices=["fast", "standard", "accurate"], default=None)
    p_build.add_argument("--functional", default=None)
    p_build.add_argument("--basis", default=None)
    p_build.add_argument("--out", default=None, help="Result JSON path.")
    _add_view_option(p_build)

    p_fukui = sub.add_parser(
        "fukui",
        help="Condensed Fukui functions + dual descriptor (atom-resolved reactivity).",
    )
    _add_common(p_fukui)
    p_fukui.add_argument(
        "--cation-mult", type=int, default=None,
        help="Multiplicity of the N-1 (cation) state. If omitted, derived from "
             "--mult: singlet parent → doublet (M+1), higher-spin parent → M-1. "
             "Override for systems where the high-spin N-1 is the ground state.",
    )
    p_fukui.add_argument(
        "--anion-mult", type=int, default=None,
        help="Multiplicity of the N+1 (anion) state. If omitted, derived from "
             "--mult: singlet parent → doublet (M+1), higher-spin parent → M-1.",
    )
    p_fukui.add_argument(
        "--no-plot", dest="plot", action="store_false", default=True,
        help="Skip the PNG bar chart of f+/f-/dual per atom.",
    )

    p_ts = sub.add_parser(
        "ts", help="Transition-state search (locate a first-order saddle).",
    )
    _add_common(p_ts)
    p_ts.add_argument(
        "--steps", type=int, default=500,
        help="Max optimizer iterations (default 500).",
    )
    p_ts.add_argument(
        "--verify-freq", dest="verify_freq", action="store_true", default=True,
        help="Run a frequency calculation on the converged TS to verify it has "
             "exactly one imaginary mode (the reaction-coordinate direction). "
             "Default on.",
    )
    p_ts.add_argument(
        "--no-verify-freq", dest="verify_freq", action="store_false",
        help="Skip the post-TS frequency verification.",
    )

    p_irc = sub.add_parser(
        "irc", help="Intrinsic reaction coordinate (walk down from a TS).",
    )
    _add_common(p_irc)
    p_irc.add_argument(
        "--max-points", type=int, default=40,
        help="Max IRC points per direction (default 40).",
    )
    p_irc.add_argument(
        "--step", type=float, default=0.05,
        help="Mass-weighted step size in amu^1/2 * bohr (default 0.05). xtb path only.",
    )

    p_rxn = sub.add_parser(
        "rxn-energy",
        help="Reaction energy ΔE / ΔH / ΔG for reactants → products.",
    )
    # rxn-energy has no single 'input' file. Species come from repeated
    # --reactant / --product flags. Method/solvent/PySCF knobs still apply.
    _add_chem_options(p_rxn, with_input=False)
    p_rxn.add_argument(
        "--reactant", action="append", default=None, required=True,
        help="Species spec '[COEF*]PATH[,charge=Q][,mult=M]'. Repeat per reactant.",
    )
    p_rxn.add_argument(
        "--product", action="append", default=None, required=True,
        help="Species spec '[COEF*]PATH[,charge=Q][,mult=M]'. Repeat per product.",
    )
    p_rxn.add_argument(
        "--mode", choices=["sp", "opt", "freq"], default="sp",
        help="sp: single-point on each input xyz (default). opt: optimize then SP. "
             "freq: full opt+freq → reports ΔE, ΔH, ΔG.",
    )
    p_rxn.add_argument("--temperature", type=float, default=298.15)
    p_rxn.add_argument("--pressure", type=float, default=101325.0)

    p_scan = sub.add_parser(
        "scan", help="Relaxed dihedral scan (torsional energy profile).",
    )
    _add_common(p_scan)
    p_scan.add_argument(
        "--dihedral", default=None,
        help="Comma-separated 1-based atom indices i,j,k,l defining the dihedral "
             "to scan (matches the C1, C2, ... labels in plots and filenames). "
             "If omitted, auto-detects all non-ring rotatable C–C bonds "
             "(including methyl rotors) and scans each.",
    )
    p_scan.add_argument(
        "--steps", type=int, default=24,
        help="Number of points around 360° (default 24 = 15° resolution).",
    )
    p_scan.add_argument(
        "--fmax", type=float, default=0.05,
        help="Per-step force convergence (eV/Å, default 0.05).",
    )
    p_scan.add_argument(
        "--opt-steps", type=int, default=200,
        help="Max optimizer iterations per scan point (default 200).",
    )

    args = parser.parse_args(argv)
    cli = cli_invocation()

    # PySCF-only knobs threaded into every task.run(...) call below.
    # Tasks that don't use them ignore them; tasks that use dft/hf consume them.
    pyscf_kwargs = dict(tier=args.tier, functional=args.functional, basis=args.basis)

    if args.task == "sp":
        from _engine.tasks import sp
        result = sp.run(args.input, method=args.method, charge=args.charge,
                        multiplicity=args.multiplicity, solvent=args.solvent, cli=cli,
                        **pyscf_kwargs)
    elif args.task == "opt":
        from _engine.tasks import opt
        result = opt.run(args.input, method=args.method, charge=args.charge,
                         multiplicity=args.multiplicity, solvent=args.solvent,
                         fmax=args.fmax, steps=args.steps, out_xyz=args.xyz_out,
                         cli=cli, **pyscf_kwargs)
    elif args.task == "freq":
        from _engine.tasks import freq
        result = freq.run(args.input, method=args.method, charge=args.charge,
                          multiplicity=args.multiplicity, solvent=args.solvent,
                          temperature_K=args.temperature, pressure_Pa=args.pressure,
                          geometry=args.geometry, symmetrynumber=args.symmetry,
                          preopt=args.preopt, preopt_fmax=args.preopt_fmax,
                          auto_confsearch=args.auto_confsearch,
                          cli=cli, **pyscf_kwargs)
    elif args.task == "binding":
        from _engine.tasks import binding
        result = binding.run(args.input, args.monomer, method=args.method,
                             charge=args.charge, multiplicity=args.multiplicity,
                             solvent=args.solvent,
                             monomer_charges=args.monomer_charge,
                             monomer_multiplicities=args.monomer_mult, cli=cli,
                             **pyscf_kwargs)
    elif args.task == "redox":
        from _engine.tasks import redox
        result = redox.run(args.input, method=args.method,
                           oxidized_charge=args.ox_charge,
                           reduced_charge=args.red_charge,
                           oxidized_multiplicity=args.ox_mult,
                           reduced_multiplicity=args.red_mult,
                           solvent=args.solvent, reference=args.ref,
                           n_electrons=args.n_electrons, cli=cli,
                           **pyscf_kwargs)
    elif args.task == "confsearch":
        from _engine.tasks import confsearch
        result = confsearch.run(
            args.input, method=args.method, solvent=args.solvent,
            n_max_conformers=args.max_conformers,
            postopt=args.postopt,
            postopt_rmsd=args.postopt_rmsd,
            postopt_ewin=args.postopt_ewin,
            charge=args.charge, multiplicity=args.multiplicity,
            cli=cli, **pyscf_kwargs,
        )
    elif args.task == "frontier":
        from _engine.tasks import frontier
        result = frontier.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            nfrontier=args.nfrontier, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "electrostatics":
        from _engine.tasks import electrostatics
        result = electrostatics.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent, cli=cli,
            **pyscf_kwargs,
        )
    elif args.task == "solvation":
        if not args.solvent:
            parser.error("solvation requires --solvent (e.g. --solvent water)")
        from _engine.tasks import solvation
        result = solvation.run(
            args.input, method=args.method, solvent=args.solvent,
            charge=args.charge, multiplicity=args.multiplicity, cli=cli,
            **pyscf_kwargs,
        )
    elif args.task == "logp":
        from _engine.tasks import logp
        result = logp.run(
            args.input, method=args.method,
            charge=args.charge, multiplicity=args.multiplicity, cli=cli,
            **pyscf_kwargs,
        )
    elif args.task == "fukui":
        from _engine.tasks import fukui
        out_path_pre = args.out or _default_out(args.input, args.task, args.method)
        out_stem = os.path.splitext(out_path_pre)[0]
        result = fukui.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            cation_mult=args.cation_mult, anion_mult=args.anion_mult,
            plot=args.plot, out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "ts":
        from _engine.tasks import ts
        out_path_pre = args.out or _default_out(args.input, args.task, args.method)
        out_stem = os.path.splitext(out_path_pre)[0]
        result = ts.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            steps=args.steps, verify_freq=args.verify_freq,
            out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "irc":
        from _engine.tasks import irc
        out_path_pre = args.out or _default_out(args.input, args.task, args.method)
        out_stem = os.path.splitext(out_path_pre)[0]
        result = irc.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            max_points=args.max_points, step=args.step,
            out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "rxn-energy":
        from _engine.tasks import reaction_energy
        result = reaction_energy.run(
            reactants=args.reactant, products=args.product,
            method=args.method, mode=args.mode, solvent=args.solvent,
            temperature_K=args.temperature, pressure_Pa=args.pressure,
            cli=cli, **pyscf_kwargs,
        )
    elif args.task == "profile":
        from _engine.tasks import reaction_profile as profile_task
        out_path_pre = (
            args.out
            or _default_out(args.reactant, args.task, args.method)
        )
        out_stem = os.path.splitext(out_path_pre)[0]
        result = profile_task.run(
            reactant_xyz=args.reactant, product_xyz=args.product,
            ts_guess_xyz=args.ts_guess, method=args.method,
            charge=args.charge, multiplicity=args.multiplicity,
            solvent=args.solvent,
            temperature_K=args.temperature, pressure_Pa=args.pressure,
            rmsd_tol=args.rmsd_tol, skip_irc=args.skip_irc,
            out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "pka":
        from _engine.tasks import pka as pka_task
        result = pka_task.run(
            ha_xyz=args.ha, a_minus_xyz=args.a_minus,
            method=args.method, mode=args.mode, solvent=args.solvent,
            ha_charge=args.ha_charge, ha_multiplicity=args.ha_mult,
            a_minus_multiplicity=args.a_minus_mult,
            temperature_K=args.temperature, pressure_Pa=args.pressure,
            hplus_reference=args.hplus_reference,
            ref_ha_xyz=args.ref_ha, ref_a_minus_xyz=args.ref_a_minus,
            ref_pka=args.pka_ref,
            ref_ha_charge=args.ref_ha_charge,
            ref_ha_multiplicity=args.ref_ha_mult,
            ref_a_minus_multiplicity=args.ref_a_minus_mult,
            cli=cli, **pyscf_kwargs,
        )
    elif args.task == "build":
        import re
        from _engine.tasks import build as build_task
        if args.out_xyz:
            out_xyz = args.out_xyz
        else:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", args.smiles)[:60] or "molecule"
            out_xyz = os.path.abspath(f"{safe}.xyz")
        result = build_task.run(
            molecule=args.smiles, out_xyz=out_xyz, name=args.name,
            opt_method=args.opt_method, opt_solvent=args.solvent,
            opt_charge=args.charge, opt_multiplicity=args.multiplicity,
            tier=args.tier, functional=args.functional, basis=args.basis,
            cli=cli,
        )
    elif args.task == "scan":
        from _engine.tasks import scan
        dihedral_tuple = None
        if args.dihedral:
            parts = [p.strip() for p in args.dihedral.split(",")]
            if len(parts) != 4:
                parser.error("--dihedral must be 4 comma-separated atom indices")
            try:
                one_based = tuple(int(p) for p in parts)
            except ValueError:
                parser.error("--dihedral atom indices must be integers")
            if any(k < 1 for k in one_based):
                parser.error("--dihedral atom indices are 1-based (must be >= 1)")
            dihedral_tuple = tuple(k - 1 for k in one_based)
        # Compute the JSON path early so scan.run can place its auxiliary
        # files (xyz / png / out) with a matching stem.
        out_path_pre = args.out or _default_out(args.input, args.task, args.method)
        out_stem = os.path.splitext(out_path_pre)[0]
        result = scan.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            dihedral=dihedral_tuple, n_steps=args.steps,
            fmax=args.fmax, opt_steps=args.opt_steps,
            out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    else:
        parser.error(f"Unknown task {args.task!r}")
        return 2

    # Tasks without a single `input` xyz need bespoke default-output paths.
    if args.task == "rxn-energy":
        from _engine.tasks.reaction_energy import _parse_species_spec
        first_path, _, _, _ = _parse_species_spec(args.reactant[0])
        out_path = args.out or _default_out(first_path, args.task, args.method)
    elif args.task == "pka":
        out_path = args.out or _default_out(args.ha, args.task, args.method)
    elif args.task == "profile":
        out_path = args.out or _default_out(args.reactant, args.task, args.method)
    elif args.task == "build":
        # build's input is a SMILES string and its --opt is optional, so the
        # naming convention is simpler: drop next to the xyz it wrote.
        if args.out:
            out_path = args.out
        else:
            stem = os.path.splitext(result["xyz_path"])[0]
            out_path = os.path.abspath(f"{stem}_build.json")
    else:
        out_path = args.out or _default_out(args.input, args.task, args.method)
    write_result(result, out_path)

    # For confsearch, also write the full conformer ensemble as an XYZ next
    # to the JSON so downstream tools have it without digging into tmp.
    if args.task == "confsearch":
        import shutil
        stem = os.path.splitext(out_path)[0]
        ensemble_dst = f"{stem}_conformers.xyz"
        ensemble_src = None
        post = result.get("postopt")
        if post and post.get("ensemble_xyz") and os.path.isfile(post["ensemble_xyz"]):
            ensemble_src = post["ensemble_xyz"]
        elif result.get("all_conformers_xyz") and os.path.isfile(result["all_conformers_xyz"]):
            ensemble_src = result["all_conformers_xyz"]
        if ensemble_src:
            shutil.copyfile(ensemble_src, ensemble_dst)
            result["conformers_xyz"] = os.path.abspath(ensemble_dst)
            # Rewrite the JSON so it records the persistent xyz path.
            write_result(result, out_path)

    print(json.dumps(result, indent=2, default=str))
    print(f"\n# result written to: {out_path}", file=sys.stderr)
    if args.task == "confsearch" and result.get("conformers_xyz"):
        print(f"# conformers xyz written to: {result['conformers_xyz']}", file=sys.stderr)
    if args.task == "scan":
        for d in result.get("dihedrals", []):
            for k in ("trajectory_xyz", "plot_png"):
                if d.get(k):
                    print(f"# {k}: {d[k]}", file=sys.stderr)
    if args.task == "fukui" and result.get("plot_png"):
        print(f"# plot_png: {result['plot_png']}", file=sys.stderr)

    # Visualize the resulting geometry in-terminal (interactive TTY only).
    _maybe_view(result, args.task, getattr(args, "view", False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
''', False),
    ('_engine.tasks', True, r"""""", False),
    ('_engine.tasks._mopac_parsers', False, r'''"""MOPAC .out / .aux scrapers for properties ASE doesn't surface."""
from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Optional

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
''', False),
    ('_engine.tasks.redox', False, r'''"""Redox potential via a simple thermodynamic cycle.

E°(red) = -(ΔG_redox + n*F*E_ref) / (n*F)

Where ΔG_redox = G(reduced) - G(oxidized) for the half-reaction
    Ox + n e⁻ → Red

Uses gas-phase or implicit-solvent (COSMO/ALPB) electronic energies as a stand-in
for Gibbs energies. For research-grade values, run `freq` on each oxidation
state and supply G's manually.
"""
from __future__ import annotations
import os
from typing import Any, Dict, Optional

from _engine.tasks import sp as sp_task
from _engine.calculators import program_label
from _engine.io import read_geometry
from _engine.schema import base_result, EV_TO_KCAL

# Standard reference potentials (V vs absolute potential of electron at rest).
# E_abs(SHE) ≈ 4.281 V (Trasatti / IUPAC recommended).
REFERENCE_POTENTIALS_V = {
    "SHE": 4.281,
    "Ag/AgCl": 4.281 + 0.222,
    "Fc+/Fc": 4.281 + 0.40,   # approximate; depends on solvent
}


def run(
    input_path: str,
    *,
    method: str,
    oxidized_charge: int,
    reduced_charge: int,
    oxidized_multiplicity: int = 1,
    reduced_multiplicity: int = 2,
    solvent: Optional[str] = None,
    reference: str = "SHE",
    n_electrons: int = 1,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    if reference not in REFERENCE_POTENTIALS_V:
        raise ValueError(
            f"Unknown reference {reference!r}. "
            f"Choose from: {list(REFERENCE_POTENTIALS_V)}"
        )

    # The reduction Ox + n e⁻ → Red implies reduced_charge − oxidized_charge = −n.
    # Reject obvious mismatches: passing n_electrons=2 with Δcharge=−1 (or any
    # combination where they disagree) produces a meaningless E°.
    expected_dq = -int(n_electrons)
    actual_dq = int(reduced_charge) - int(oxidized_charge)
    if actual_dq != expected_dq:
        raise ValueError(
            f"redox: n_electrons={n_electrons} but reduced_charge - oxidized_charge "
            f"= {actual_dq} (expected {expected_dq}). The reduced form must have "
            f"exactly n more electrons than the oxidized form (one less unit of charge "
            "per electron added)."
        )
    # Spin parity: each unpaired-electron count changes by ±1 per added electron,
    # so the multiplicities must differ by exactly n_electrons modulo 2.
    expected_parity = n_electrons % 2
    actual_parity = abs(int(reduced_multiplicity) - int(oxidized_multiplicity)) % 2
    if actual_parity != expected_parity:
        raise ValueError(
            f"redox: |reduced_mult - oxidized_mult| has parity {actual_parity}, "
            f"expected {expected_parity} for {n_electrons}-electron transfer. "
            "Adding n electrons flips spin parity n times; check that your "
            "ox/red multiplicities are consistent (e.g. neutral singlet ↔ "
            "anion-radical doublet for n=1)."
        )

    ox_sp = sp_task.run(
        input_path, method=method, charge=oxidized_charge,
        multiplicity=oxidized_multiplicity, solvent=solvent, cli=cli,
        tier=tier, functional=functional, basis=basis,
    )
    red_sp = sp_task.run(
        input_path, method=method, charge=reduced_charge,
        multiplicity=reduced_multiplicity, solvent=solvent, cli=cli,
        tier=tier, functional=functional, basis=basis,
    )

    delta_E_eV = red_sp["total_energy_eV"] - ox_sp["total_energy_eV"]
    # E°(red/ox) = -(ΔG/nF) - E_ref(abs). With ΔG in eV and one electron, ΔG/F = ΔE in volts.
    E_redox_V = -(delta_E_eV / n_electrons) - REFERENCE_POTENTIALS_V[reference]

    atoms = read_geometry(input_path)
    result = base_result(
        task="redox_potential",
        method=ox_sp["method"],
        program=program_label(method),
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=atoms.get_chemical_symbols(),
        charge=oxidized_charge, multiplicity=oxidized_multiplicity,
        solvent=solvent, cli=cli,
    )
    result["redox_potential_V_vs_" + reference] = E_redox_V
    result["delta_E_redox_eV"] = delta_E_eV
    result["delta_E_redox_kcal_mol"] = delta_E_eV * EV_TO_KCAL
    result["n_electrons"] = n_electrons
    result["oxidized_state"] = {
        "charge": oxidized_charge, "multiplicity": oxidized_multiplicity,
        "energy_eV": ox_sp["total_energy_eV"],
    }
    result["reduced_state"] = {
        "charge": reduced_charge, "multiplicity": reduced_multiplicity,
        "energy_eV": red_sp["total_energy_eV"],
    }
    result["warnings"] = result.get("warnings", []) + [
        "Redox potential computed from single-point energies on the SAME geometry — "
        "does not include reorganization or solvation free energy contributions properly. "
        "Accuracy with semi-empirical methods is ±0.3–0.5 V at best. "
        "For publishable values: optimize each oxidation state, run freq for ΔG, "
        "include explicit solvation cycle.",
    ]
    return result
''', False),
    ('_engine.tasks.sp', False, r'''"""Single-point energy task."""
from __future__ import annotations
import os
from typing import Any, Dict, Optional

from _engine.calculators import (
    build_calculator, apply_calc_to_atoms,
    method_label, program_label, collect_calc_extras,
)
from _engine.io import read_geometry
from _engine.schema import base_result, energy_block_from_eV, element_warnings


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()
    calc = build_calculator(
        method, charge=charge, multiplicity=multiplicity, solvent=solvent,
        tier=tier, functional=functional, basis=basis,
    )
    apply_calc_to_atoms(atoms, calc)

    energy_eV = atoms.get_potential_energy()

    # ASE's MOPAC calculator already returns the heat of formation (the canonical
    # PM7 observable), which is what chemists usually mean by "the energy" of a
    # semi-empirical calculation. Keep `total_energy_eV` aligned with that so
    # `sp` matches `opt`/`freq`. The absolute electronic energy (ETOT from
    # ENPART) is still available in code_specific.electronic_total_energy_eV.
    result = base_result(
        task="single_point",
        method=method_label(method, calc),
        program=program_label(method),
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=symbols,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )
    result.update(energy_block_from_eV(energy_eV))
    if method == "xtb":
        result["energy_zero"] = "isolated atoms at infinity (xtb)"
    elif method == "mopac":
        result["energy_zero"] = "elements in their standard states (PM7 heat of formation)"
    else:
        result["energy_zero"] = "electronic energy (bare nuclei + electrons)"

    # Pull code-specific extras (HOMO/LUMO, dipole, heat of formation, etc.).
    extras = collect_calc_extras(method, atoms, calc)
    if method == "mopac" and "heat_of_formation_kcal_mol" in extras:
        # Promote HoF to top level so the schema matches `opt` / `freq`.
        result["final_heat_of_formation_kcal_mol"] = extras["heat_of_formation_kcal_mol"]
    if extras:
        result["code_specific"] = extras

    warns = element_warnings(symbols, method)
    if warns:
        result["warnings"] = warns
    return result


def _xtb_homo_lumo(atoms, calc) -> Dict[str, Any]:
    """Run a low-level xtb singlepoint to recover orbital eigenvalues.

    The ASE-side XTB calculator only returns energy/forces/dipole; orbital
    energies live on the xtb-python Calculator's Result object.

    Kept here (rather than in calculators.py) to avoid importing xtb at
    module-load time on systems without xtb-python installed.
    """
    try:
        import numpy as np
        from xtb.interface import Calculator, Param
        from xtb.libxtb import VERBOSITY_MUTED
    except ImportError:
        return {}

    HARTREE_TO_EV = 27.211386245988
    ANGSTROM_TO_BOHR = 1.8897261254535

    numbers = np.array(atoms.get_atomic_numbers(), dtype=np.int32)
    positions_bohr = np.asarray(atoms.get_positions()) * ANGSTROM_TO_BOHR

    charge = float(getattr(calc, "_chemkit_charge", 0))
    uhf = int(getattr(calc, "_chemkit_uhf", 0))

    try:
        xcalc = Calculator(Param.GFN2xTB, numbers, positions_bohr,
                           charge=charge, uhf=uhf)
        xcalc.set_verbosity(VERBOSITY_MUTED)
        # ALPB solvent if configured on the ASE calc
        solvent = getattr(calc, "parameters", {}).get("solvent")
        if solvent:
            try:
                from xtb.utils import get_solvent, Solvent
                sol = get_solvent(solvent)
                if sol != Solvent.none:
                    xcalc.set_solvent(sol)
            except Exception:
                pass
        res = xcalc.singlepoint()
        eigs = np.asarray(res.get_orbital_eigenvalues())   # Hartree
        occs = np.asarray(res.get_orbital_occupations())
    except Exception:
        return {}

    occupied = np.where(occs > 1e-6)[0]
    virtual = np.where(occs < 1e-6)[0]
    if occupied.size == 0 or virtual.size == 0:
        return {}

    homo_idx = int(occupied[-1])
    lumo_idx = int(virtual[0])
    homo_eV = float(eigs[homo_idx]) * HARTREE_TO_EV
    lumo_eV = float(eigs[lumo_idx]) * HARTREE_TO_EV
    return {
        "homo_eV": homo_eV,
        "lumo_eV": lumo_eV,
        "homo_lumo_gap_eV": lumo_eV - homo_eV,
    }
''', False),
    ('_engine.backends', True, r'''"""Backend dispatch layer for chemkit.

xtb and MOPAC live in `chemkit.calculators` (single-method ASE calculators).
PySCF hosts many methods (HF, DFT, MP2, CCSD(T), CASSCF, TDDFT) and gets its
own subpackage here.
"""
''', False),
    ('_engine.backends.pyscf', True, r'''"""PySCF backend — multi-method (HF, DFT, ...) ab initio entry points.

Public surface:
    PySCFCalculator              # ASE Calculator for use by every chemkit task
    run_sp_dft, run_sp_hf        # standalone single-point helpers
    resolve_dft_tier, DFT_TIERS  # tier presets used by chemkit.calculators
"""
from .calculator import PySCFCalculator
from .dft import (
    run_sp as run_sp_dft,
    resolve_tier as resolve_dft_tier,
    TIERS as DFT_TIERS,
    DEFAULT_TIER as DFT_DEFAULT_TIER,
)
from .hf import (
    run_sp as run_sp_hf,
    DEFAULT_BASIS as HF_DEFAULT_BASIS,
)

__all__ = [
    "PySCFCalculator",
    "run_sp_dft",
    "run_sp_hf",
    "resolve_dft_tier",
    "DFT_TIERS",
    "DFT_DEFAULT_TIER",
    "HF_DEFAULT_BASIS",
]
''', False),
    ('_engine.backends.pyscf.calculator', False, r'''"""ASE-compatible Calculator backed by PySCF.

Lets every chemkit task that already speaks ASE (opt, freq, binding, scan,
electrostatics, ...) pick up DFT and HF without per-task plumbing. The
calculator caches its converged SCF object on the most recent geometry, so
chained property requests (energy then forces; energy then dipole) avoid
re-running the SCF.

Warm-start: the converged density matrix from the most recent geometry is
cached and passed as `dm0` to the next `kernel()` call. ASE driver loops
(BFGS opt, Vibrations finite-difference Hessian) feed back small-displacement
geometries, so the prior DM is an excellent initial guess and typically cuts
the iteration count by 2-3×. If the warm-start fails to converge (rare; can
happen at large displacements that drag the molecule through a near-
degeneracy), we automatically fall back to a cold SCF.
"""
from __future__ import annotations
from typing import Optional

import numpy as np
from ase.calculators.calculator import Calculator, all_changes

from .molecule import build_mol, promote_basis_for_anion
from .scf import build_mean_field


HARTREE_TO_EV = 27.211386245988
HARTREE_PER_BOHR_TO_EV_PER_ANG = 27.211386245988 / 0.529177210903


class PySCFCalculator(Calculator):
    """ASE Calculator delegating to PySCF for DFT (RKS/UKS) or HF (RHF/UHF).

    Parameters mirror the chemkit CLI knobs. `method` selects the theory layer
    ('dft' or 'hf'); `xc` is required when `method == 'dft'`.
    """

    implemented_properties = ["energy", "forces", "dipole"]
    name = "pyscf"

    def __init__(
        self,
        *,
        method: str = "dft",
        xc: Optional[str] = None,
        basis: str = "def2-tzvp",
        charge: int = 0,
        multiplicity: int = 1,
        solvent: Optional[str] = None,
        grid_level: int = 4,
        scf_tol: float = 1e-8,
        max_cycle: Optional[int] = None,
        density_fit: bool = True,
        auxbasis: str = "def2-universal-jfit",
        max_memory_mb: int = 8000,
        verbose: int = 0,
    ):
        super().__init__()
        method = method.lower()
        if method not in ("dft", "hf"):
            raise ValueError(f"PySCFCalculator: unknown method {method!r}")
        if method == "dft" and not xc:
            raise ValueError("PySCFCalculator: DFT requires an xc functional.")
        self._method = method
        self._xc = xc
        self._basis = basis
        self._charge = int(charge)
        self._multiplicity = int(multiplicity)
        self._solvent = solvent
        self._grid_level = int(grid_level)
        self._scf_tol = float(scf_tol)
        self._max_cycle = max_cycle if max_cycle is None else int(max_cycle)
        self._density_fit = bool(density_fit)
        self._auxbasis = auxbasis
        self._max_memory_mb = int(max_memory_mb)
        self._verbose = int(verbose)

        # Auto-promote diffuse basis for anions; record what we actually used.
        self._basis, self._basis_promoted = promote_basis_for_anion(self._basis, self._charge)

        # Cached converged mean-field; invalidated by ASE when atoms change.
        self._mol = None
        self._mf = None
        self._cached_positions = None

        # Warm-start density matrix from the previous converged SCF.
        # Keyed on the full (symbols, charge, mult, basis, xc, method, solvent)
        # tuple: any change invalidates the cached DM. The symbols guard catches
        # different fragments (shape mismatch); the rest catch the case where
        # something mutates the calculator's parameters between calls (current
        # chemkit code doesn't, but the guard is cheap insurance against a
        # silently-stale DM leaking through if that invariant ever breaks).
        self._cached_dm = None
        self._cached_dm_key: Optional[tuple] = None

    # ---- chemkit-side accessors (used by sp.py / electrostatics / frontier) --

    @property
    def method(self) -> str:
        return self._method

    @property
    def functional(self) -> Optional[str]:
        return self._xc

    @property
    def basis(self) -> str:
        return self._basis

    @property
    def basis_promoted(self) -> bool:
        return self._basis_promoted

    @property
    def mean_field(self):
        """Last converged SCF object (or None if no calculation yet)."""
        return self._mf

    # ---- ASE plumbing ------------------------------------------------------

    def calculate(self, atoms=None, properties=("energy",), system_changes=all_changes):
        super().calculate(atoms, properties, system_changes)

        positions = np.asarray(self.atoms.get_positions(), dtype=float)
        need_scf = (
            self._mf is None
            or self._cached_positions is None
            or not np.array_equal(positions, self._cached_positions)
        )
        if need_scf:
            self._mol = build_mol(
                self.atoms,
                basis=self._basis,
                charge=self._charge,
                multiplicity=self._multiplicity,
                max_memory_mb=self._max_memory_mb,
                verbose=self._verbose,
            )
            self._mf = build_mean_field(
                self._mol,
                method=self._method,
                xc=self._xc,
                grid_level=self._grid_level,
                scf_tol=self._scf_tol,
                max_cycle=self._max_cycle,
                density_fit=self._density_fit,
                auxbasis=self._auxbasis,
                solvent=self._solvent,
            )

            # Warm-start: pass the previous converged density as initial guess
            # iff every cache-key field matches. Symbols guard against shape
            # mismatch; the rest guard against a stale DM if any calculator
            # parameter mutated since the cache was written.
            current_key = (
                tuple(self.atoms.get_chemical_symbols()),
                self._charge,
                self._multiplicity,
                self._basis,
                self._xc,
                self._method,
                self._solvent,
            )
            dm0 = self._cached_dm if self._cached_dm_key == current_key else None

            energy_hartree = _run_scf_with_warm_start(self._mf, dm0)

            self._cached_energy_eV = energy_hartree * HARTREE_TO_EV
            self._cached_positions = positions.copy()
            self._cached_forces = None
            self._cached_dipole = None

            # Cache the converged density for the next geometry's warm start.
            # Only when convergence succeeded — a non-converged DM would
            # poison the next step.
            if getattr(self._mf, "converged", False):
                try:
                    self._cached_dm = self._mf.make_rdm1()
                    self._cached_dm_key = current_key
                except Exception:
                    self._cached_dm = None
                    self._cached_dm_key = None
            else:
                self._cached_dm = None
                self._cached_dm_key = None

        self.results["energy"] = self._cached_energy_eV

        if "forces" in properties:
            if self._cached_forces is None:
                grad = self._mf.nuc_grad_method().kernel()  # Hartree / Bohr
                self._cached_forces = -np.asarray(grad) * HARTREE_PER_BOHR_TO_EV_PER_ANG
            self.results["forces"] = self._cached_forces

        if "dipole" in properties:
            if self._cached_dipole is None:
                try:
                    self._cached_dipole = np.asarray(
                        self._mf.dip_moment(unit="Debye", verbose=0), dtype=float
                    )
                except Exception:
                    self._cached_dipole = np.zeros(3)
            self.results["dipole"] = self._cached_dipole


def _run_scf_with_warm_start(mf, dm0):
    """Run mf.kernel() with the previous converged DM as initial guess; if it
    fails to converge (or raises), fall back to a cold SCF.

    Two failure modes are handled:
      1. dm0 has wrong shape (e.g. spin-restricted vs unrestricted mismatch
         after a cached-from-different-method run leaked through). kernel()
         raises ValueError on shape mismatch — caught, retried cold.
      2. dm0 leads to non-convergence (rare; usually only at large
         displacements through near-degeneracies). `mf.converged` is False;
         we redo from atomic guess. Net cost: the wasted partial SCF.
    """
    if dm0 is None:
        return float(mf.kernel())
    try:
        energy = float(mf.kernel(dm0=dm0))
    except Exception:
        # Shape mismatch or some other dm0-induced failure — drop it and
        # let PySCF build the default atomic-density guess.
        return float(mf.kernel())
    if getattr(mf, "converged", False):
        return energy
    # Warm start converged to non-convergence; retry cold.
    return float(mf.kernel())
''', False),
    ('_engine.backends.pyscf.dft', False, r'''"""DFT entry point for the PySCF backend.

Exposes `run_sp(atoms, ...)` returning a chemkit-shape result dict.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from .molecule import build_mol, promote_basis_for_anion
from .scf import build_mean_field, pack_scf_result


# Tier table: (xc, basis, grid_level, auxbasis).
# Functional strings use libxc names (PySCF accepts both libxc and its own
# aliases — libxc is the safer bet for portability).
# wB97X-V / wB97M-V use VV10 nonlocal correlation, native in PySCF — no add-on.
# wB97X-D3BJ would be a hair better at the standard tier but requires
# pyscf-dispersion, which currently fails to load on Python 3.13.
TIERS = {
    "fast":     {"xc": "r2scan",  "basis": "def2-svp",   "grid": 3, "aux": "def2-universal-jfit",
                 "scf_tol": 1e-7,  "max_cycle": 80},
    "standard": {"xc": "wb97x_v", "basis": "def2-tzvp",  "grid": 4, "aux": "def2-universal-jfit",
                 "scf_tol": 1e-8,  "max_cycle": 150},
    "accurate": {"xc": "wb97m_v", "basis": "def2-qzvpp", "grid": 5, "aux": "def2-universal-jfit",
                 "scf_tol": 1e-10, "max_cycle": 300},
}
DEFAULT_TIER = "standard"


def resolve_tier(
    tier: Optional[str],
    xc: Optional[str],
    basis: Optional[str],
) -> Dict[str, Any]:
    """Merge a tier preset with explicit overrides. Overrides win."""
    tier_name = (tier or DEFAULT_TIER).lower()
    if tier_name not in TIERS:
        raise ValueError(f"Unknown DFT tier {tier!r}. Choose from {sorted(TIERS)}.")
    cfg = dict(TIERS[tier_name])
    cfg["tier"] = tier_name
    if xc:
        cfg["xc"] = xc
    if basis:
        cfg["basis"] = basis
    return cfg


def run_sp(
    atoms,
    *,
    tier: Optional[str] = None,
    xc: Optional[str] = None,
    basis: Optional[str] = None,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    max_memory_mb: int = 8000,
) -> Dict[str, Any]:
    """Run a DFT single-point and return the per-method `code_specific` block
    plus the converged total energy in Hartree.

    The task layer (chemkit.tasks.sp) wraps this into the shared chemkit
    result schema; this function stays backend-shaped so it can be reused by
    `opt`, `freq`, `binding`, etc. without round-tripping JSON.
    """
    cfg = resolve_tier(tier, xc, basis)
    used_basis, promoted = promote_basis_for_anion(cfg["basis"], charge)
    cfg["basis"] = used_basis

    mol = build_mol(
        atoms,
        basis=cfg["basis"],
        charge=charge,
        multiplicity=multiplicity,
        max_memory_mb=max_memory_mb,
    )

    mf = build_mean_field(
        mol,
        method="dft",
        xc=cfg["xc"],
        grid_level=cfg["grid"],
        scf_tol=cfg["scf_tol"],
        max_cycle=cfg["max_cycle"],
        auxbasis=cfg["aux"],
        solvent=solvent,
    )

    energy_hartree = float(mf.kernel())

    extras = pack_scf_result(mf)
    extras.update({
        "tier": cfg["tier"],
        "functional": cfg["xc"],
        "basis": cfg["basis"],
        "grid_level": cfg["grid"],
        "auxbasis": cfg["aux"],
        "scf_tol": cfg["scf_tol"],
        "scf_max_cycle": cfg["max_cycle"],
        "density_fit": True,
        "solvent_model": ("ddCOSMO" if solvent else None),
    })

    warnings = []
    if promoted:
        warnings.append(
            f"Anion detected (charge={charge}); basis promoted to {used_basis} "
            f"to add diffuse functions."
        )
    if not extras.get("scf_converged", False):
        warnings.append("DFT SCF did not converge — energy is from the last iteration.")

    return {
        "energy_hartree": energy_hartree,
        "extras": extras,
        "warnings": warnings,
    }
''', False),
    ('_engine.backends.pyscf.hf', False, r'''"""Hartree-Fock entry point for the PySCF backend.

HF has no functional or tier — just a basis. Shape mirrors `dft.run_sp` so the
task layer can dispatch uniformly.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from .molecule import build_mol, promote_basis_for_anion
from .scf import build_mean_field, pack_scf_result


DEFAULT_BASIS = "def2-tzvp"

# HF has no functional, but convergence still varies with how tight you want
# the answer. Same scf_tol/max_cycle ladder as DFT so chemkit's --tier flag
# means the same thing across methods.
HF_TIERS = {
    "fast":     {"scf_tol": 1e-7,  "max_cycle": 80},
    "standard": {"scf_tol": 1e-8,  "max_cycle": 150},
    "accurate": {"scf_tol": 1e-10, "max_cycle": 300},
}
DEFAULT_TIER = "standard"


def run_sp(
    atoms,
    *,
    tier: Optional[str] = None,
    basis: Optional[str] = None,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    max_memory_mb: int = 8000,
) -> Dict[str, Any]:
    """Run an HF single-point and return {energy_hartree, extras, warnings}."""
    chosen_basis = basis or DEFAULT_BASIS
    used_basis, promoted = promote_basis_for_anion(chosen_basis, charge)
    tier_name = (tier or DEFAULT_TIER).lower()
    if tier_name not in HF_TIERS:
        raise ValueError(f"Unknown HF tier {tier!r}. Choose from {sorted(HF_TIERS)}.")
    tcfg = HF_TIERS[tier_name]

    mol = build_mol(
        atoms,
        basis=used_basis,
        charge=charge,
        multiplicity=multiplicity,
        max_memory_mb=max_memory_mb,
    )

    mf = build_mean_field(
        mol,
        method="hf",
        scf_tol=tcfg["scf_tol"],
        max_cycle=tcfg["max_cycle"],
        solvent=solvent,
    )

    energy_hartree = float(mf.kernel())

    extras = pack_scf_result(mf)
    extras.update({
        "tier": tier_name,
        "basis": used_basis,
        "scf_tol": tcfg["scf_tol"],
        "scf_max_cycle": tcfg["max_cycle"],
        "density_fit": True,
        "solvent_model": ("ddCOSMO" if solvent else None),
    })

    warnings = []
    if promoted:
        warnings.append(
            f"Anion detected (charge={charge}); basis promoted to {used_basis} "
            f"to add diffuse functions."
        )
    if not extras.get("scf_converged", False):
        warnings.append("HF SCF did not converge — energy is from the last iteration.")

    return {
        "energy_hartree": energy_hartree,
        "extras": extras,
        "warnings": warnings,
    }
''', False),
    ('_engine.backends.pyscf.molecule', False, r'''"""ASE Atoms -> pyscf.gto.Mole construction with chemkit defaults.

Responsibilities:
- Convert ASE Atoms into a pyscf.gto.Mole
- Handle charge / spin (PySCF wants nelec_alpha - nelec_beta, not multiplicity)
- Auto-promote basis sets for anions (diffuse functions matter a lot)
- Centralize the "pyscf not installed" error
"""
from __future__ import annotations
from typing import Tuple


# Basis sets that should be promoted to their diffuse variant for anions.
# Keep the mapping small and explicit; users can override with --basis.
_DIFFUSE_PROMOTION = {
    "def2-svp": "def2-svpd",
    "def2-tzvp": "def2-tzvpd",
    "def2-tzvpp": "def2-tzvppd",
    "def2-qzvp": "def2-qzvpd",
    "def2-qzvpp": "def2-qzvppd",
    "cc-pvdz": "aug-cc-pvdz",
    "cc-pvtz": "aug-cc-pvtz",
    "cc-pvqz": "aug-cc-pvqz",
}


def _require_pyscf():
    try:
        import pyscf  # noqa: F401
        from pyscf import gto  # noqa: F401
        return gto
    except ImportError as e:
        raise ImportError(
            "PySCF is not installed. Install it with:\n"
            "    pip install pyscf\n"
            "Optional dispersion add-on:\n"
            "    pip install pyscf-dispersion"
        ) from e


def promote_basis_for_anion(basis: str, charge: int) -> Tuple[str, bool]:
    """If `charge < 0`, swap to a diffuse-augmented basis when one is known.

    Returns (resolved_basis, was_promoted).
    """
    if charge >= 0:
        return basis, False
    promoted = _DIFFUSE_PROMOTION.get(basis.lower())
    if promoted is None:
        return basis, False
    return promoted, True


def build_mol(
    atoms,
    *,
    basis: str,
    charge: int = 0,
    multiplicity: int = 1,
    max_memory_mb: int = 8000,
    verbose: int = 0,
):
    """Build a pyscf.gto.Mole from an ASE Atoms object.

    PySCF uses `spin = 2S = (n_alpha - n_beta)`, not the chemistry-conventional
    multiplicity (2S+1). We translate here so the rest of chemkit stays
    consistent with xtb/MOPAC's `--mult` semantics.
    """
    gto = _require_pyscf()

    if multiplicity < 1:
        raise ValueError(f"multiplicity must be >= 1, got {multiplicity}")
    spin = multiplicity - 1  # PySCF convention

    atom_spec = [
        (sym, tuple(pos))
        for sym, pos in zip(atoms.get_chemical_symbols(), atoms.get_positions())
    ]

    mol = gto.M(
        atom=atom_spec,
        basis=basis,
        charge=int(charge),
        spin=int(spin),
        unit="Angstrom",
        max_memory=int(max_memory_mb),
        verbose=int(verbose),
    )
    return mol
''', False),
    ('_engine.backends.pyscf.scf', False, r'''"""Mean-field machinery shared by every PySCF method.

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
    max_cycle: Optional[int] = None,
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
    if max_cycle is not None:
        mf.max_cycle = int(max_cycle)
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
''', False),
]

_register_embedded(_EMBEDDED)


# --- launcher ---------------------------------------------------------------
from _engine.cli import main as _main  # noqa: E402

if __name__ == "__main__":
    _sys.exit(_main(["redox", *_sys.argv[1:]]))
