"""ASE calculator factory for xtb (xtb-python or CLI), MOPAC, optional COSMO solvation."""
from __future__ import annotations
import os
import shutil
import tempfile
from typing import Optional


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
}

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
}


def build_calculator(
    method: str,
    *,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    workdir: Optional[str] = None,
):
    """Return an ASE calculator for the requested method.

    method: 'xtb' (GFN2-xTB) or 'mopac' (PM7)
    multiplicity: 2S+1 (ASE uses unpaired-electron count internally for some calcs)
    solvent: e.g. 'water' for ALPB (xtb) or COSMO EPS=... (MOPAC). None = gas phase.
    """
    method = method.lower()
    if workdir is None:
        workdir = tempfile.mkdtemp(prefix=f"chemkit_{method}_")

    if method == "xtb":
        return _build_xtb(charge, multiplicity, solvent, workdir)
    if method == "mopac":
        return _build_mopac(charge, multiplicity, solvent, workdir)
    raise ValueError(f"Unknown method {method!r}. Expected 'xtb' or 'mopac'.")


def _build_xtb(charge, multiplicity, solvent, workdir):
    """Prefer xtb-python (compiled); fall back to subprocess via a thin shim."""
    try:
        from xtb.ase.calculator import XTB
        kwargs = {"method": "GFN2-xTB"}
        if solvent:
            sol = XTB_SOLVENT_MAP.get(solvent.lower())
            if sol is None:
                raise ValueError(f"xtb: unknown solvent {solvent!r}")
            kwargs["solvent"] = sol
        # xtb-python takes charge/uhf via Atoms.info or set_parameters; pass below.
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
    """Attach calc to atoms and propagate xtb charge/uhf when needed."""
    if hasattr(calc, "_chemkit_charge"):
        atoms.info["charge"] = calc._chemkit_charge
        atoms.info["uhf"] = calc._chemkit_uhf
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
