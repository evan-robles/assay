#!/usr/bin/env python3
"""Self-contained `build_from_smiles` skill — chemistry engine inlined.

This single file bundles everything the `build_from_smiles` skill needs. It registers the
embedded engine modules into sys.modules under their real names (preserving each
module's namespace, so tasks that share function names like run()/_run_mopac do
NOT collide), then runs the chemkit CLI pinned to the `build` subcommand.

Run standalone:  python build_from_smiles.py --help
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
    ('_engine.resolve', False, r'''"""Resolve a plain molecule *name* to a SMILES string from online databases.

Used by `chemkit build` when the user supplies something like "ethanol" or
"L-alanine" instead of a SMILES or an .xyz file. We try a chain of reliable
public sources, in order, and report which one answered (with an ACS-format
citation so the provenance is auditable):

  1. PubChem  (PUG REST)        — name -> CID -> isomeric SMILES
  2. OPSIN    (EBI web service) — IUPAC-name -> SMILES (no database, a parser)
  3. NIST     (WebBook)         — name -> InChI -> SMILES (via Open Babel)

The first source that returns a usable structure wins. Each resolver returns a
``Resolution`` carrying the SMILES, which flavor it is (isomeric vs.
connectivity), a short human-readable source label, and an ACS citation string
with the access date.

Network access is always attempted (callers ask for a name precisely because
they don't have the structure). Every resolver fails soft: on timeout, HTTP
error, or empty result it returns ``None`` and the chain moves on.
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

_TIMEOUT = 20  # seconds per request
_USER_AGENT = "chemkit/1.0 (https://github.com/; molecule name resolver)"


@dataclass
class Resolution:
    """A successful name -> SMILES resolution and its provenance."""
    smiles: str
    name_input: str
    source: str            # short key, e.g. "PubChem", "OPSIN", "NIST WebBook"
    smiles_kind: str       # "isomeric" | "connectivity" | "unspecified"
    citation: str          # ACS-format attribution string
    url: Optional[str] = None
    identifier: Optional[str] = None   # e.g. "CID 702"

    def as_dict(self) -> dict:
        return {
            "smiles": self.smiles,
            "name_input": self.name_input,
            "source": self.source,
            "smiles_kind": self.smiles_kind,
            "citation": self.citation,
            "url": self.url,
            "identifier": self.identifier,
        }


# ---------------------------------------------------------------------------
# small HTTP helper
# ---------------------------------------------------------------------------

def _http_get(url: str) -> Optional[str]:
    """GET a URL, following redirects. Returns the body text or None on error."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def _today() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# 1. PubChem (PUG REST)
# ---------------------------------------------------------------------------

def _resolve_pubchem(name: str) -> Optional[Resolution]:
    """name -> CID + isomeric SMILES via the PubChem PUG REST API.

    PubChem now exposes ``SMILES`` (the isomeric/stereo-aware form) and
    ``ConnectivitySMILES``; the legacy ``IsomericSMILES``/``CanonicalSMILES``
    names are remapped server-side. We request both and prefer the isomeric one.
    """
    enc = urllib.parse.quote(name, safe="")
    url = (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{enc}/property/SMILES,ConnectivitySMILES,Title/JSON"
    )
    body = _http_get(url)
    if not body:
        return None
    try:
        props = json.loads(body)["PropertyTable"]["Properties"][0]
    except (KeyError, IndexError, ValueError):
        return None

    iso = (props.get("SMILES") or "").strip()
    conn = (props.get("ConnectivitySMILES") or "").strip()
    smiles = iso or conn
    if not smiles:
        return None
    kind = "isomeric" if iso else "connectivity"
    cid = props.get("CID")
    title = props.get("Title") or name

    page = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}" if cid else None
    citation = (
        "National Center for Biotechnology Information. PubChem Compound "
        f"Summary for CID {cid}, {title}. "
        f"{page} (accessed {_today()})."
    )
    return Resolution(
        smiles=smiles,
        name_input=name,
        source="PubChem",
        smiles_kind=kind,
        citation=citation,
        url=page,
        identifier=f"CID {cid}" if cid is not None else None,
    )


# ---------------------------------------------------------------------------
# 2. OPSIN (IUPAC name -> structure), hosted at EBI
# ---------------------------------------------------------------------------

def _resolve_opsin(name: str) -> Optional[Resolution]:
    """Resolve a *systematic* (IUPAC) name to SMILES via OPSIN.

    OPSIN is a deterministic name-to-structure parser (not a lookup database),
    so it only succeeds for systematic names — but for those it is extremely
    reliable and stereochemistry-aware.
    """
    enc = urllib.parse.quote(name, safe="")
    url = f"https://www.ebi.ac.uk/opsin/ws/{enc}.json"
    body = _http_get(url)
    if not body:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if data.get("status") != "SUCCESS":
        return None
    smiles = (data.get("smiles") or "").strip()
    if not smiles:
        return None
    citation = (
        "Lowe, D. M.; Corbett, P. T.; Murray-Rust, P.; Glen, R. C. "
        "Chemical Name to Structure: OPSIN, an Open Source Solution. "
        "J. Chem. Inf. Model. 2011, 51 (3), 739-753. "
        f"OPSIN web service, https://www.ebi.ac.uk/opsin/ (accessed {_today()})."
    )
    return Resolution(
        smiles=smiles,
        name_input=name,
        source="OPSIN",
        smiles_kind="isomeric",
        citation=citation,
        url=f"https://www.ebi.ac.uk/opsin/ws/{enc}.smi",
        identifier=None,
    )


# ---------------------------------------------------------------------------
# 3. NIST WebBook (name -> InChI -> SMILES via Open Babel)
# ---------------------------------------------------------------------------

def _inchi_to_smiles(inchi: str) -> Optional[str]:
    """Convert an InChI to SMILES with Open Babel (already a chemkit dep)."""
    obabel = shutil.which("obabel")
    if obabel is None:
        return None
    try:
        proc = subprocess.run(
            [obabel, "-iinchi", "-osmi"],
            input=inchi, capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # obabel prints "<smiles>\t<title>" — take the first whitespace token.
    out = proc.stdout.strip().split()
    return out[0] if out else None


def _resolve_nist(name: str) -> Optional[Resolution]:
    """name -> InChI (NIST WebBook) -> SMILES (Open Babel).

    The WebBook does not serve SMILES directly, but its species pages embed a
    standard InChI which we convert locally. We scrape the InChI string out of
    the HTML rather than parse the whole page.
    """
    enc = urllib.parse.quote(name, safe="")
    page_url = f"https://webbook.nist.gov/cgi/cbook.cgi?Name={enc}&Units=SI"
    body = _http_get(page_url)
    if not body:
        return None

    inchi = _extract_inchi(body)
    if not inchi:
        return None
    smiles = _inchi_to_smiles(inchi)
    if not smiles:
        return None
    citation = (
        "Linstrom, P. J.; Mallard, W. G., Eds. NIST Chemistry WebBook, NIST "
        "Standard Reference Database Number 69; National Institute of Standards "
        "and Technology: Gaithersburg, MD. https://webbook.nist.gov/ "
        f"(accessed {_today()})."
    )
    return Resolution(
        smiles=smiles,
        name_input=name,
        # InChI->SMILES drops nothing structural but stereo round-tripping
        # through obabel is not guaranteed, so label it honestly.
        source="NIST WebBook",
        smiles_kind="unspecified",
        citation=citation,
        url=page_url,
        identifier=None,
    )


def _extract_inchi(html: str) -> Optional[str]:
    """Pull a standard InChI string out of NIST WebBook HTML."""
    marker = "InChI=1S/"
    idx = html.find(marker)
    if idx == -1:
        marker = "InChI=1/"
        idx = html.find(marker)
        if idx == -1:
            return None
    # InChI runs until the first whitespace or HTML tag boundary.
    end = idx
    while end < len(html) and html[end] not in " \t\r\n\"'<>":
        end += 1
    inchi = html[idx:end].strip()
    return inchi or None


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

# Resolver chain, in priority order. Each is (label, fn).
_RESOLVERS = [
    ("PubChem", _resolve_pubchem),
    ("OPSIN", _resolve_opsin),
    ("NIST WebBook", _resolve_nist),
]


def resolve_name_to_smiles(name: str) -> Resolution:
    """Resolve a molecule *name* to SMILES, trying each source in turn.

    Returns the first successful ``Resolution``. Raises ``LookupError`` with a
    summary of everything tried if no reliable source could resolve the name.
    """
    name = name.strip()
    if not name:
        raise LookupError("Empty molecule name.")

    tried: List[str] = []
    for label, fn in _RESOLVERS:
        tried.append(label)
        try:
            res = fn(name)
        except Exception:
            res = None
        if res is not None and res.smiles:
            return res

    raise LookupError(
        f"Could not resolve {name!r} to a SMILES from any reliable source. "
        f"Tried: {', '.join(tried)}. Check the spelling, supply a SMILES "
        "string directly, or provide an .xyz file."
    )
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
    ('_engine.tasks.build', False, r'''"""Build 3D molecular geometry from a SMILES string via Open Babel.

Pipeline:
  0. If the input is a plain molecule *name* (not a SMILES), resolve it to a
     SMILES online — PubChem -> OPSIN -> NIST WebBook — recording the source.
  1. Write the SMILES to a temporary ``.smi`` file.
  2. Run ``obabel <tmp>.smi --gen3d -O <out>.xyz`` to generate 3D coordinates.
  3. Delete the temporary ``.smi`` file.
  4. Optionally hand off to xtb / mopac / dft / hf via the existing opt task
     so the user gets a QM-quality geometry in one command.

The headline output is an .xyz file. JSON records the atom count, the obabel
invocation, the SMILES source (when resolved from a name, with an ACS-format
citation), and (if requested) the QM-opt convergence + energy.

Why this skill exists: every other chemkit skill takes an .xyz as input.
For users who only have a SMILES — or even just a molecule name — `chemkit
build` closes the on-ramp without requiring them to fire up Avogadro or paste
into PubChem.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Open Babel helpers
# ---------------------------------------------------------------------------

def _require_obabel() -> str:
    """Return the path to the obabel executable or raise a helpful error."""
    exe = shutil.which("obabel")
    if exe is None:
        raise EnvironmentError(
            "chemkit build requires Open Babel (`obabel`), which was not found "
            "on PATH. Install with `conda install -c conda-forge openbabel` or "
            "your platform package manager."
        )
    return exe


def _looks_like_smiles(text: str) -> bool:
    """Return True if Open Babel can parse `text` as a SMILES string.

    Used to distinguish a SMILES (e.g. 'CCO') from a plain molecule name
    (e.g. 'ethanol'), which obabel rejects with '0 molecules converted'.
    Short strings like 'C' (methane) are valid SMILES and resolve as such —
    the right default when someone types into a structure builder.
    """
    text = text.strip()
    if not text:
        return False
    obabel = _require_obabel()
    try:
        proc = subprocess.run(
            [obabel, f"-:{text}", "-osmi"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # obabel reports "N molecule(s) converted" on stderr; a 0 means it could
    # not parse the input as a SMILES.
    return proc.returncode == 0 and "0 molecules converted" not in proc.stderr


def _gen3d_from_smiles(smiles: str, out_xyz: str, *, title: Optional[str]) -> str:
    """Convert a SMILES string to a 3D .xyz via Open Babel.

    Follows the canonical workflow:
      1. Write the SMILES to a temporary .smi file.
      2. obabel <tmp>.smi --gen3d -O <out>.xyz
      3. Delete the temporary .smi file (always, even on failure).

    Returns the captured obabel command line for the result record.
    """
    obabel = _require_obabel()

    out_xyz = os.path.abspath(out_xyz)
    os.makedirs(os.path.dirname(out_xyz) or ".", exist_ok=True)

    # Step 1: temporary .smi file holding the SMILES string.
    fd, smi_path = tempfile.mkstemp(suffix=".smi", prefix="chemkit_build_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(smiles.strip() + "\n")

        # Step 2: obabel <tmp>.smi --gen3d -O <out>.xyz
        cmd = [obabel, smi_path, "--gen3d", "-O", out_xyz]
        if title:
            cmd += ["--title", title]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # obabel often exits 0 even when it cannot parse the SMILES — it just
        # prints "0 molecules converted" and writes an empty .xyz. Treat a
        # missing/empty output file (or a nonzero exit) as a hard failure.
        wrote_geometry = os.path.isfile(out_xyz) and os.path.getsize(out_xyz) > 0
        if proc.returncode != 0 or not wrote_geometry:
            # Don't leave an empty stub behind for downstream tools to trip on.
            if os.path.isfile(out_xyz) and not wrote_geometry:
                try:
                    os.remove(out_xyz)
                except OSError:
                    pass
            raise RuntimeError(
                f"obabel failed to build 3D coordinates for SMILES {smiles!r} "
                "(no atoms were written — the SMILES is likely invalid).\n"
                f"command: {' '.join(cmd)}\n"
                f"stdout: {proc.stdout.strip()}\n"
                f"stderr: {proc.stderr.strip()}"
            )
        return " ".join(cmd)
    finally:
        # Step 3: always remove the temporary .smi file.
        try:
            os.remove(smi_path)
        except OSError:
            pass


def _xyz_atom_count(xyz_path: str) -> int:
    """Read the atom count from the first line of an .xyz file."""
    with open(xyz_path) as f:
        first = f.readline().strip()
    return int(first)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    molecule: str,
    *,
    out_xyz: str,
    name: Optional[str] = None,
    opt_method: Optional[str] = None,
    opt_solvent: Optional[str] = None,
    opt_charge: Optional[int] = None,
    opt_multiplicity: Optional[int] = None,
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    cli: str = "",
) -> Dict[str, Any]:
    """Build a 3D xyz from a SMILES string *or* a molecule name, using Open Babel.

    Args:
      molecule: either a SMILES string (e.g. 'CCO') or a plain molecule name
        (e.g. 'ethanol'). If it does not parse as SMILES, it is resolved to a
        SMILES online via PubChem -> OPSIN -> NIST WebBook, and the source is
        recorded in the result with an ACS-format citation.
      out_xyz: destination .xyz path. Will be overwritten if it exists.
      name: optional title comment for the xyz (defaults to the input/SMILES).
      opt_method: if set, hand off to chemkit.tasks.opt for a QM refinement
        after the obabel build. One of 'xtb' / 'mopac' / 'dft' / 'hf'.
      opt_solvent: implicit solvent forwarded to opt.
      opt_charge, opt_multiplicity: net charge / spin multiplicity forwarded
        to the QM step. obabel does not infer these here, so they default to
        0 and 1 respectively unless the user supplies them.
      tier, functional, basis: DFT/HF knobs forwarded to opt.

    Returns a result dict; also writes `out_xyz` to disk.
    """
    molecule = molecule.strip()

    # Decide whether the input is already a SMILES or a name to look up.
    smiles_source: Optional[Dict[str, Any]] = None
    if _looks_like_smiles(molecule):
        smiles = molecule
    else:
        # Treat as a molecule name: resolve to SMILES from a reliable source.
        from _engine.resolve import resolve_name_to_smiles
        resolution = resolve_name_to_smiles(molecule)
        smiles = resolution.smiles
        smiles_source = resolution.as_dict()

    comment = name or f"chemkit build: {molecule}"
    obabel_cmd = _gen3d_from_smiles(smiles, out_xyz, title=comment)
    out_xyz = os.path.abspath(out_xyz)

    result: Dict[str, Any] = {
        "task": "build_from_smiles",
        "program": "openbabel",
        "input": molecule,
        "smiles_input": smiles,
        "n_atoms": _xyz_atom_count(out_xyz),
        "build": {
            "method": "obabel --gen3d",
            "command": obabel_cmd,
        },
        "xyz_path": out_xyz,
        "cli_invocation": cli,
        "warnings": [],
    }
    if smiles_source is not None:
        # The input was a name; record where the SMILES came from.
        result["smiles_source"] = smiles_source

    # Optional QM refinement step
    if opt_method:
        from _engine.tasks import opt as opt_task
        q = 0 if opt_charge is None else opt_charge
        m = 1 if opt_multiplicity is None else opt_multiplicity
        qm_xyz = os.path.splitext(out_xyz)[0] + f"_{opt_method}.xyz"
        opt_res = opt_task.run(
            input_path=out_xyz,
            method=opt_method,
            charge=q,
            multiplicity=m,
            solvent=opt_solvent,
            out_xyz=qm_xyz,
            cli=f"(internal build_from_smiles QM refinement: {opt_method})",
            tier=tier, functional=functional, basis=basis,
        )
        result["qm_optimization"] = {
            "method": opt_res["method"],
            "program": opt_res["program"],
            "solvent": opt_solvent,
            "charge": q,
            "multiplicity": m,
            "converged": bool(opt_res.get("converged")),
            "n_steps": opt_res.get("n_steps"),
            "total_energy_eV": opt_res.get("total_energy_eV"),
            "optimized_xyz": opt_res.get("optimized_xyz"),
        }
        # Promote the QM-relaxed xyz as the canonical output path so downstream
        # skills see the better geometry by default. Keep the obabel file too
        # for transparency.
        result["xyz_path_obabel"] = out_xyz
        result["xyz_path"] = qm_xyz
        if not opt_res.get("converged"):
            result["warnings"].append(
                f"QM refinement ({opt_method}) did not converge — using the "
                "non-converged geometry. Consider re-running with --opt-steps "
                "or a tighter starting structure."
            )

    if not result["warnings"]:
        del result["warnings"]
    return result
''', False),
    ('_engine.tasks.opt', False, r'''"""Geometry optimization task.

For xtb (GFN2): ASE's BFGS drives the optimization using xtb-python forces.
For mopac (PM7): MOPAC's native EF optimizer drives the optimization in a single
binary invocation. ASE/BFGS is bypassed for MOPAC because line searches starting
from chemically nonsensical geometries can step into atomic collisions whose
gradients overflow MOPAC's fixed-width force printout, which then breaks ASE's
output parser. MOPAC's own optimizer uses internal coordinates and handles such
inputs gracefully.
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from _engine.calculators import MOPAC_SOLVENT_EPS, mopac_spin_keyword
from _engine.io import read_geometry
from _engine.schema import (
    base_result,
    energy_block_from_eV,
    element_warnings,
)


KCAL_PER_MOL_TO_EV = 1.0 / 23.060547830619026  # eV per kcal/mol


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    fmax: float = 0.05,    # eV/Å
    steps: int = 500,
    out_xyz: Optional[str] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    method = method.lower()
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    if out_xyz is None:
        stem = os.path.splitext(os.path.basename(input_path))[0]
        out_xyz = os.path.abspath(f"{stem}_{method}_opt.xyz")

    if method == "mopac":
        return _run_mopac(
            input_path=input_path,
            atoms=atoms,
            symbols=symbols,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            fmax=fmax,
            steps=steps,
            out_xyz=out_xyz,
            cli=cli,
        )

    return _run_ase(
        input_path=input_path,
        atoms=atoms,
        symbols=symbols,
        method=method,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        fmax=fmax,
        steps=steps,
        out_xyz=out_xyz,
        cli=cli,
        tier=tier,
        functional=functional,
        basis=basis,
    )


def _run_ase(
    *, input_path, atoms, symbols, method, charge, multiplicity, solvent,
    fmax, steps, out_xyz, cli,
    tier=None, functional=None, basis=None,
) -> Dict[str, Any]:
    from ase.io import write as ase_write
    from ase.optimize import BFGS
    from _engine.calculators import (
        build_calculator, apply_calc_to_atoms,
        method_label, program_label,
    )

    calc = build_calculator(
        method, charge=charge, multiplicity=multiplicity, solvent=solvent,
        tier=tier, functional=functional, basis=basis,
    )
    apply_calc_to_atoms(atoms, calc)

    dyn = BFGS(atoms, logfile=None)
    converged = dyn.run(fmax=fmax, steps=steps)
    final_energy = atoms.get_potential_energy()
    ase_write(out_xyz, atoms, format="xyz")

    result = base_result(
        task="geometry_optimization",
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
    result.update(energy_block_from_eV(final_energy))
    result["converged"] = bool(converged)
    result["n_steps"] = int(dyn.get_number_of_steps())
    result["fmax_target_eV_per_A"] = fmax
    result["optimized_xyz"] = out_xyz

    warns = element_warnings(symbols, method)
    if not converged:
        warns.append(f"Optimization did NOT converge within {steps} steps (fmax={fmax}).")
    if warns:
        result["warnings"] = warns
    return result


def _run_mopac(
    *, input_path, atoms, symbols, charge, multiplicity, solvent,
    fmax, steps, out_xyz, cli,
) -> Dict[str, Any]:
    mopac_exe = shutil.which("mopac")
    if mopac_exe is None:
        raise FileNotFoundError("mopac executable not found in PATH.")

    workdir = tempfile.mkdtemp(prefix="chemkit_mopac_opt_")
    mop_path = os.path.join(workdir, "mopac.mop")
    out_path = os.path.join(workdir, "mopac.out")
    arc_path = os.path.join(workdir, "mopac.arc")

    keywords = _mopac_opt_keywords(
        charge=charge, multiplicity=multiplicity, solvent=solvent,
        fmax=fmax, steps=steps,
    )
    _write_mopac_input(mop_path, keywords, symbols, atoms.get_positions())

    proc = subprocess.run(
        [mopac_exe, "mopac.mop"],
        cwd=workdir, capture_output=True, text=True, timeout=600,
    )

    if not os.path.isfile(out_path):
        raise RuntimeError(
            f"mopac did not produce {out_path}.\n"
            f"stdout: {proc.stdout[-1000:]}\nstderr: {proc.stderr[-1000:]}"
        )

    with open(out_path) as f:
        out_text = f.read()

    converged, conv_msg = _parse_mopac_convergence(out_text)
    hof_kcal = _parse_mopac_hof(out_text)
    grad_norm = _parse_mopac_gradient_norm(out_text)
    final_symbols, final_positions = _parse_mopac_final_geometry(arc_path, out_text)

    if final_symbols and final_positions:
        atoms.set_chemical_symbols(final_symbols)
        atoms.set_positions(final_positions)

    from ase.io import write as ase_write
    ase_write(out_xyz, atoms, format="xyz")

    energy_eV = (
        hof_kcal * KCAL_PER_MOL_TO_EV if hof_kcal is not None else float("nan")
    )

    result = base_result(
        task="geometry_optimization",
        method="PM7",
        program="mopac",
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=atoms.get_chemical_symbols(),
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )
    if hof_kcal is not None:
        result.update(energy_block_from_eV(energy_eV))
        result["final_heat_of_formation_kcal_mol"] = hof_kcal
    result["converged"] = bool(converged)
    result["fmax_target_eV_per_A"] = fmax
    if grad_norm is not None:
        result["mopac_gradient_norm_kcal_per_A"] = grad_norm
    if conv_msg:
        result["mopac_status"] = conv_msg
    result["optimized_xyz"] = out_xyz
    result["mopac_workdir"] = workdir
    result["mopac_keywords"] = keywords

    warns = element_warnings(symbols, "mopac")
    if not converged:
        warns.append(
            f"MOPAC reported the optimization did NOT converge "
            f"({conv_msg or 'see mopac.out'}); final geometry returned anyway."
        )
    if hof_kcal is not None and abs(hof_kcal) > 10000:
        warns.append(
            f"Final heat of formation is extreme ({hof_kcal:.1f} kcal/mol). "
            "The optimizer may have settled into a non-physical (collapsed/exploded) "
            "geometry; inspect the optimized xyz before trusting the energy."
        )
    if warns:
        result["warnings"] = warns
    return result


def _mopac_opt_keywords(
    *, charge: int, multiplicity: int, solvent: Optional[str],
    fmax: float, steps: int,
) -> List[str]:
    # MOPAC default = full geometry optimization with EF. We give it loose
    # GNORM (gradient norm) target derived from fmax (which is in eV/Å). MOPAC's
    # GNORM is in kcal/(mol·Å); convert and convert per-atom-component fmax
    # roughly into a system gradient norm threshold by scaling by sqrt(3N).
    # 1 eV/Å ≈ 23.06 kcal/(mol·Å)
    gnorm = max(0.01, fmax * 23.060547830619026)
    kw = [
        "PM7",
        f"GNORM={gnorm:.3f}",
        "AUX",
        "GEO-OK",
    ]
    if charge != 0:
        kw.append(f"CHARGE={charge}")
    if multiplicity > 1:
        kw.append(mopac_spin_keyword(multiplicity))
        kw.append("UHF")
    if solvent:
        eps = MOPAC_SOLVENT_EPS.get(solvent.lower())
        if eps is None:
            raise ValueError(f"mopac: unknown solvent {solvent!r}")
        kw.append(f"EPS={eps}")
    kw.append("THREADS=1")
    return kw


def _write_mopac_input(
    path: str, keywords: List[str], symbols: List[str], positions,
) -> None:
    lines = [" ".join(keywords), "chemkit geometry optimization", ""]
    for sym, (x, y, z) in zip(symbols, positions):
        lines.append(
            f"{sym:<3s} {x:15.8f} 1 {y:15.8f} 1 {z:15.8f} 1"
        )
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


_HOF_RE = re.compile(
    r"FINAL HEAT OF FORMATION\s*=\s*(-?\d+\.\d+)\s*KCAL"
)
_GNORM_RE = re.compile(
    r"GRADIENT NORM\s*=\s*(-?[\d.]+(?:[eE][+-]?\d+)?)"
)


def _parse_mopac_hof(text: str) -> Optional[float]:
    matches = _HOF_RE.findall(text)
    return float(matches[-1]) if matches else None


def _parse_mopac_gradient_norm(text: str) -> Optional[float]:
    matches = _GNORM_RE.findall(text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def _parse_mopac_convergence(text: str) -> Tuple[bool, Optional[str]]:
    # Success markers MOPAC prints when EF/BFGS reach the gradient target.
    if "GRADIENT TEST PASSED" in text:
        return True, "GRADIENT TEST PASSED"
    # SCF-only success isn't enough — we want geometry to be converged.
    if "GEOMETRY OPTIMISED" in text or "GEOMETRY OPTIMIZED" in text:
        # Check for explicit failure annotations alongside.
        if "HERBERTS TEST" in text or "TRUST RADIUS NOW LESS" in text:
            return False, "EF terminated abnormally (trust radius collapsed)."
        return True, "EF reported geometry optimised."
    if "EXCESS NUMBER OF OPTIMIZATION CYCLES" in text:
        return False, "Exceeded CYCLES limit."
    if "HEAT OF FORMATION IS UNCHANGED" in text:
        return False, "EF stalled (HoF unchanged for several cycles)."
    if "GEOMETRY IS NOT CONVERGED" in text:
        return False, "MOPAC reported geometry not converged."
    # Fall back: assume not converged if we can't find a positive marker.
    return False, None


def _parse_mopac_final_geometry(
    arc_path: str, out_text: str,
) -> Tuple[List[str], List[Tuple[float, float, float]]]:
    """Prefer the .arc 'FINAL GEOMETRY OBTAINED' block; fall back to .out."""
    if os.path.isfile(arc_path):
        with open(arc_path) as f:
            arc_text = f.read()
        syms, pos = _extract_arc_geometry(arc_text)
        if syms:
            return syms, pos
    return _extract_out_geometry(out_text)


_ARC_ATOM_RE = re.compile(
    r"^\s*([A-Z][a-z]?)"
    r"\s+(-?\d+\.\d+)\s*[+\-]?\d?"
    r"\s+(-?\d+\.\d+)\s*[+\-]?\d?"
    r"\s+(-?\d+\.\d+)\s*[+\-]?\d?",
    re.MULTILINE,
)


def _extract_arc_geometry(text: str):
    marker = "FINAL GEOMETRY OBTAINED"
    idx = text.find(marker)
    if idx < 0:
        return [], []
    block = text[idx:]
    syms, pos = [], []
    for m in _ARC_ATOM_RE.finditer(block):
        sym = m.group(1)
        if sym in ("PM", "PM7"):  # skip the keyword line accidentally matched
            continue
        syms.append(sym)
        pos.append((float(m.group(2)), float(m.group(3)), float(m.group(4))))
    return syms, pos


def _extract_out_geometry(text: str):
    # Find the LAST "CARTESIAN COORDINATES" block in the .out file.
    blocks = list(re.finditer(r"CARTESIAN COORDINATES", text))
    if not blocks:
        return [], []
    start = blocks[-1].end()
    tail = text[start:start + 8000]
    syms, pos = [], []
    for line in tail.splitlines():
        m = re.match(
            r"\s*\d+\s+([A-Z][a-z]?)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)",
            line,
        )
        if m:
            syms.append(m.group(1))
            pos.append((float(m.group(2)), float(m.group(3)), float(m.group(4))))
        elif syms and line.strip() == "":
            if len(syms) > 0:
                break
    return syms, pos
''', False),
]

_register_embedded(_EMBEDDED)


# --- launcher ---------------------------------------------------------------
from _engine.cli import main as _main  # noqa: E402

if __name__ == "__main__":
    _sys.exit(_main(["build", *_sys.argv[1:]]))
