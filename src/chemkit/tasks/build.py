"""Build 3D molecular geometry from a SMILES string via Open Babel.

Pipeline:
  1. Write the SMILES to a temporary ``.smi`` file.
  2. Run ``obabel <tmp>.smi --gen3d -O <out>.xyz`` to generate 3D coordinates.
  3. Delete the temporary ``.smi`` file.
  4. Optionally hand off to xtb / mopac / dft / hf via the existing opt task
     so the user gets a QM-quality geometry in one command.

The headline output is an .xyz file. JSON records the atom count, the obabel
invocation, and (if requested) the QM-opt convergence + energy.

Why this skill exists: every other chemkit skill takes an .xyz as input.
For users who only have a SMILES (the most common starting point in drug
design / cheminformatics), `chemkit build` closes the on-ramp without
requiring them to fire up Avogadro or paste into PubChem.
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
    smiles: str,
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
    """Build a 3D xyz from SMILES using Open Babel.

    Args:
      smiles: SMILES string (canonical or not — obabel parses both).
      out_xyz: destination .xyz path. Will be overwritten if it exists.
      name: optional title comment for the xyz (defaults to the SMILES).
      opt_method: if set, hand off to chemkit.tasks.opt for a QM refinement
        after the obabel build. One of 'xtb' / 'mopac' / 'dft' / 'hf'.
      opt_solvent: implicit solvent forwarded to opt.
      opt_charge, opt_multiplicity: net charge / spin multiplicity forwarded
        to the QM step. obabel does not infer these here, so they default to
        0 and 1 respectively unless the user supplies them.
      tier, functional, basis: DFT/HF knobs forwarded to opt.

    Returns a result dict; also writes `out_xyz` to disk.
    """
    comment = name or f"chemkit build: {smiles}"
    obabel_cmd = _gen3d_from_smiles(smiles, out_xyz, title=comment)
    out_xyz = os.path.abspath(out_xyz)

    result: Dict[str, Any] = {
        "task": "build_from_smiles",
        "program": "openbabel",
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

    # Optional QM refinement step
    if opt_method:
        from . import opt as opt_task
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
