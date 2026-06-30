"""ASE calculator factory for xtb (xtb-python or CLI), MOPAC, optional COSMO solvation."""
from __future__ import annotations
import os
import shutil
import tempfile
from typing import Optional

import numpy as np

# Solvent tables live in schema.py (single documented home). Re-exported here so
# existing importers (`from ..calculators import MOPAC_SOLVENT_EPS, XTB_SOLVENT_MAP`)
# keep working unchanged.
from .schema import XTB_SOLVENT_MAP, MOPAC_SOLVENT_EPS  # noqa: F401 (re-export)
from .constants import HARTREE_TO_EV

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


def resolve_dielectric(solvent, eps_table, *, backend: str = "") -> float:
    """Resolve a `--solvent` value to a dielectric constant (eps) for a
    continuum-solvation backend (PySCF ddCOSMO, MOPAC COSMO).

    The value may be EITHER:
      * a number (e.g. "2.0") — used directly as the custom dielectric, so a user
        can specify any solvent's eps without it being in `eps_table`; or
      * a known solvent name (e.g. "hexane") — looked up case-insensitively in
        `eps_table` (each backend passes its own table, preserving its values).

    Args:
      solvent: the raw --solvent string (name or numeric dielectric).
      eps_table: the backend's name -> eps mapping (MOPAC_SOLVENT_EPS or the
        PySCF table); used only for the name path.
      backend: short label ("mopac"/"pyscf") for error messages.

    Returns the dielectric as a float.

    Raises ValueError if a non-numeric name is not in `eps_table`, or if a
    numeric dielectric is not strictly positive.
    """
    s = str(solvent).strip()
    try:
        val = float(s)
    except ValueError:
        eps = eps_table.get(s.lower())
        if eps is None:
            raise ValueError(
                f"{backend or 'solvent'}: unknown solvent {solvent!r}. "
                f"Pass a known name ({sorted(eps_table)}) or a numeric "
                f"dielectric constant, e.g. --solvent 2.0."
            )
        return float(eps)
    if val <= 0:
        raise ValueError(
            f"dielectric constant must be positive (got {val}). "
            f"Pass a real solvent eps, e.g. --solvent 2.0."
        )
    return val


def mopac_chemistry_keywords(charge: int, multiplicity: int,
                             solvent: Optional[str] = None) -> list:
    """Build the standard MOPAC chemistry keywords shared by every MOPAC task:
    CHARGE (if nonzero), the spin keyword + UHF (if open-shell), and EPS (if a
    solvent is set). Returns a list to extend a task's keyword list with.

    This consolidates a block that was copy-pasted identically across 7 task
    modules (opt/freq/ts/irc/orbitals/electrostatics/frontier) — so MOPAC's
    charge/spin/solvent handling has one definition, not seven. Task-specific
    keywords (PM7, FORCE, IRC=, MULLIK, THREADS, T=, ...) stay in each task.
    """
    kw: list = []
    if charge != 0:
        kw.append(f"CHARGE={charge}")
    if multiplicity > 1:
        kw.append(mopac_spin_keyword(multiplicity))
        kw.append("UHF")
    if solvent:
        eps = resolve_dielectric(solvent, MOPAC_SOLVENT_EPS, backend="mopac")
        kw.append(f"EPS={eps}")
    return kw


def resolve_xtb_solvent(solvent) -> str:
    """Resolve a `--solvent` value to an ALPB solvent name for xtb.

    xtb's ALPB solvation is parameterized per *named* solvent and has no
    arbitrary-dielectric mode, so a numeric value cannot be honored — it is
    rejected with guidance rather than silently mishandled. A name is mapped
    (case-insensitively) through XTB_SOLVENT_MAP.

    Raises ValueError for a numeric value (point the user at dft/hf/mopac) or an
    unknown name.
    """
    s = str(solvent).strip()
    try:
        float(s)
    except ValueError:
        name = XTB_SOLVENT_MAP.get(s.lower())
        if name is None:
            raise ValueError(
                f"xtb: unknown solvent {solvent!r}. Known: {sorted(XTB_SOLVENT_MAP)}."
            )
        return name
    raise ValueError(
        f"xtb (ALPB) requires a named solvent, not a numeric dielectric "
        f"({solvent!r}). Use --method dft, hf, or mopac for a custom dielectric "
        f"constant, or pass a named solvent ({sorted(XTB_SOLVENT_MAP)})."
    )


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
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
):
    """Return an ASE calculator for the requested method.

    method: 'xtb' (GFN2-xTB), 'mopac' (PM7), 'dft' (PySCF DFT), 'hf' (PySCF HF)
    multiplicity: 2S+1 (ASE uses unpaired-electron count internally for some calcs)
    solvent: e.g. 'water' for ALPB (xtb) or COSMO EPS=... (MOPAC). None = gas phase.
    tier/functional/basis: PySCF-only knobs. Silently ignored for xtb/mopac.
    density_fit: PySCF-only. Enable the RI density-fitting approximation. OFF by
        default (exact four-center integrals); turned on only by the user's
        explicit --density-fit flag. Ignored for xtb/mopac.
    solvent_model: PySCF-only continuum model ('ddcosmo' default, 'cpcm', or
        'iefpcm'). MOPAC (COSMO) and xtb (ALPB) have their own fixed models; a
        non-default value with those methods AND a solvent set is an error
        (rather than silently ignored).

    If `workdir` is None a fresh tempdir is allocated and registered for
    auto-cleanup at process exit. Callers that want the workdir to persist
    past the chemkit run (e.g. so result['mopac_workdir'] is still readable
    afterwards) must pass `workdir=...` explicitly.
    """
    method = method.lower()
    if workdir is None:
        workdir = tempfile.mkdtemp(prefix=f"chemkit_{method}_")
        _AUTO_TEMPDIRS.append(workdir)

    # --solvent-model is a PySCF-only knob. For xtb/mopac, reject a non-default
    # model when a solvent is actually requested, instead of silently ignoring
    # it (the user would otherwise believe PCM ran when it didn't).
    if method in ("xtb", "mopac") and solvent and (solvent_model or "ddcosmo").lower() != "ddcosmo":
        fixed = "COSMO" if method == "mopac" else "ALPB"
        raise ValueError(
            f"--solvent-model {solvent_model!r} is not available for "
            f"--method {method} (it uses its own continuum model, {fixed}). "
            "The ddcosmo/cpcm/iefpcm choice applies only to --method dft or hf."
        )

    if method == "xtb":
        return _build_xtb(charge, multiplicity, solvent, workdir)
    if method == "mopac":
        return _build_mopac(charge, multiplicity, solvent, workdir)
    if method in ("dft", "hf"):
        return _build_pyscf(
            method, charge, multiplicity, solvent, workdir,
            tier=tier, functional=functional, basis=basis,
            density_fit=density_fit, solvent_model=solvent_model,
        )
    raise ValueError(
        f"Unknown method {method!r}. Expected 'xtb', 'mopac', 'dft', or 'hf'."
    )


def label_calculator(method: str, *, charge: int = 0, multiplicity: int = 1,
                     solvent=None, tier=None, functional=None, basis=None,
                     density_fit: bool = False, solvent_model: str = "ddcosmo"):
    """Build a calculator ONLY for method/provenance labeling (method_label).

    Several tasks (electrostatics/orbitals/frontier/ts/scan) need a calculator
    object up-front purely so `method_label()` can report the resolved DFT/HF
    level of theory (functional/basis/tier) — the actual single point may run via
    a different path. Only dft/hf carry that provenance; xtb/mopac have none, so
    this returns None for them. Consolidates a block that was byte-identical
    across those tasks.
    """
    if method in ("dft", "hf"):
        return build_calculator(
            method, charge=charge, multiplicity=multiplicity, solvent=solvent,
            tier=tier, functional=functional, basis=basis,
            density_fit=density_fit, solvent_model=solvent_model,
        )
    return None


def _build_pyscf(method, charge, multiplicity, solvent, workdir,
                 *, tier=None, functional=None, basis=None, density_fit=False,
                 solvent_model="ddcosmo"):
    """Dispatch DFT/HF to the PySCF backend (lazy import).

    The PySCF backend lives in chemkit.backends.pyscf and exposes an
    ASE-compatible Calculator class. We import lazily so users without
    PySCF installed can still use xtb/mopac.

    DFT tier presets bundle (xc, basis, grid_level); explicit
    `--functional`/`--basis` override the tier defaults. HF takes only a
    `--basis` (default def2-tzvp).

    density_fit: when False (the default) chemkit runs EXACT four-center
    integrals — true RKS/UKS and RHF/UHF, matching a hand-written PySCF run.
    The user's explicit --density-fit flag sets this True to enable the RI
    approximation. The tier table's own `density_fit` value is documentation of
    each tier's profile only; this flag is what actually controls the run.
    """
    # PySCF parallelism is governed by the OpenMP thread count, which it reads at
    # import time. In containerized / MCP-spawned subprocesses OMP_NUM_THREADS is
    # frequently 1, which silently single-threads every DFT/HF SCF, gradient, and
    # finite-difference Hessian — the most expensive backend. Default to all cores
    # (override via CHEMKIT_PYSCF_THREADS) BEFORE importing pyscf, and record the
    # effective count on the calculator for reproducibility.
    n_threads_env = os.environ.get("CHEMKIT_PYSCF_THREADS")
    if n_threads_env:
        try:
            n_threads = max(1, int(n_threads_env))
        except ValueError:
            n_threads = os.cpu_count() or 1
    else:
        n_threads = os.cpu_count() or 1
    # Set OMP/MKL env vars only if not already pinned by the user, so an explicit
    # external setting is respected.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, str(n_threads))

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

    # PySCF exposes a runtime setter that also re-pins its internal thread pool;
    # call it so the count takes effect even if numpy/pyscf were imported earlier
    # in this process. Best-effort — never fail a calculation over thread tuning.
    # Note: if PySCF was built without OpenMP, num_threads() always returns 1
    # regardless of the request (a build limitation, not a chemkit bug).
    effective_threads = n_threads
    try:
        import warnings as _warnings
        from pyscf import lib as _pyscf_lib
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            _pyscf_lib.num_threads(n_threads)
            effective_threads = int(_pyscf_lib.num_threads())
    except Exception:
        pass

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
            # Density fitting is controlled by the explicit --density-fit flag
            # (default OFF = exact integrals), NOT by the tier. The auxbasis is
            # left None so build_mean_field() chooses it to match the functional
            # (JK-fit for hybrids, J-fit for pure functionals) when DF is on.
            density_fit=density_fit,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            solvent_model=solvent_model,
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
            density_fit=density_fit,  # off by default; --density-fit opts in
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            solvent_model=solvent_model,
            verbose=pyscf_verbose,
        )
        calc._chemkit_tier = hf_tier
        calc._chemkit_functional = None
        calc._chemkit_basis = calc.basis  # honors anion auto-promotion

    calc._chemkit_method = method
    calc._chemkit_workdir = workdir
    calc._chemkit_threads = effective_threads
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
        # Integration-grid and SCF-tolerance provenance (method-block fields the
        # reporting standard requires). These live on the PySCFCalculator; the
        # calculator-driven task path never surfaced them before (only the unused
        # run_sp path did).
        grid_level = getattr(calc, "_grid_level", None)
        scf_tol = getattr(calc, "_scf_tol", None)
        max_cycle = getattr(calc, "_max_cycle", None)
        if grid_level is not None and m == "dft":
            extras["grid_level"] = grid_level
        if scf_tol is not None:
            extras["scf_tol"] = scf_tol
        if max_cycle is not None:
            extras["scf_max_cycle"] = max_cycle
        threads = getattr(calc, "_chemkit_threads", None)
        if threads is not None:
            extras["n_threads"] = threads
    return extras


def _build_xtb(charge, multiplicity, solvent, workdir):
    """Prefer xtb-python (compiled); fall back to subprocess via a thin shim.

    For solvents the xtb-python Solvent enum doesn't expose (octanol etc.) we
    route through the CLI even when xtb-python is installed — otherwise the
    ASE wrapper silently drops the solvent and reports gas-phase energies.
    """
    # Resolve the name to its ALPB form (also rejects a numeric dielectric: xtb
    # has no arbitrary-eps mode). None stays None (gas phase).
    alpb_name = resolve_xtb_solvent(solvent) if solvent else None
    if alpb_name in XTB_PYTHON_UNSUPPORTED_SOLVENTS:
        return _XtbCliCalculator(
            charge=charge, uhf=max(0, multiplicity - 1),
            solvent=solvent, workdir=workdir,
        )
    try:
        from xtb.ase.calculator import XTB
        kwargs = {"method": "GFN2-xTB"}
        if alpb_name:
            kwargs["solvent"] = alpb_name
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
        eps = resolve_dielectric(solvent, MOPAC_SOLVENT_EPS, backend="mopac")
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
            sol = resolve_xtb_solvent(self.solvent)  # ALPB name; rejects numeric eps
            cmd += ["--alpb", sol]
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=self.workdir, timeout=300)
        m = re.search(r"total energy\s+([-+]?\d+\.\d+)\s*Eh", res.stdout)
        if not m:
            raise RuntimeError("xtb CLI: could not parse total energy.\n" + res.stdout[-2000:])
        # Convert Hartree -> eV to match ASE convention.
        energy_eV = float(m.group(1)) * HARTREE_TO_EV
        self.results["energy"] = energy_eV
        return energy_eV

    def calculate(self, atoms, properties, system_changes):
        self.atoms = atoms
        self.get_potential_energy(atoms)

    def get_property(self, name, atoms=None, allow_calculation=True):
        if name == "energy":
            return self.get_potential_energy(atoms)
        raise NotImplementedError(name)
