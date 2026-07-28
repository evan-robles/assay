#!/usr/bin/env python3
"""build-from-smiles — self-contained assay skill.

Generate a 3D .xyz geometry from a SMILES string (Open Babel --gen3d), optionally
refining it with a QM geometry optimization (--opt). Owns its workflow; depends
only on the shared `assay_core` physics library (and, for --opt, the
geometry-optimize sibling via its task shim). Runnable stand-alone:

    python skills/build_from_smiles/scripts/run.py 'CCO' --out-xyz ethanol.xyz
"""
from __future__ import annotations

# Skill discovery manifest (read by assay_core.discovery / the MCP server).
SKILL_NAME = "build-from-smiles"      # kebab display name (matches SKILL.md frontmatter)
SUBCOMMAND = "build"      # engine subcommand this skill implements
import argparse
import os
import re as _re
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional

from assay_core.integrity import finalize


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

    This is the validity gate for the SMILES-only builder: a plain molecule
    name (e.g. 'ethanol') is rejected by obabel with '0 molecules converted',
    so this returns False and `run()` raises a helpful error. Short strings
    like 'C' (methane) or 'O' (water) are valid SMILES and pass.

    Caveat: some short letter strings parse as *unintended* SMILES rather than
    the element/name a user might mean — e.g. 'Co' parses as C[O] (carbon +
    oxygen), not cobalt, and 'no' parses as N=O. This is inherent to SMILES
    syntax; a user who means an element symbol or a name should resolve it via
    the name-to-smiles skill first and pass the returned SMILES here.
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
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    cli: str = "",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Build a 3D xyz from a SMILES string, using Open Babel.

    Args:
      molecule: a SMILES string (e.g. 'CCO'). This skill is SMILES-only: if the
        input does not parse as a SMILES (e.g. a plain molecule name like
        'ethanol'), a ValueError is raised pointing at the name-to-smiles skill.
        Resolve a name to SMILES there first, then pass the resolved SMILES here.
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

    # SMILES-only: reject anything Open Babel cannot parse as SMILES (e.g. a
    # plain molecule name). Name -> structure is a two-step workflow: resolve the
    # name to SMILES with the name-to-smiles skill first, then build from that
    # SMILES here. We do NOT do a network name lookup inside the builder.
    if not _looks_like_smiles(molecule):
        raise ValueError(
            f"{molecule!r} is not a valid SMILES string. build-from-smiles "
            "accepts SMILES only. To build from a molecule name, first resolve "
            "it to a SMILES with the name-to-smiles skill (which records the "
            "source and an ACS citation), then pass the resolved SMILES here. "
            "See the name-to-3d-structure workflow for the two-step recipe."
        )
    smiles = molecule

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

    # Optional QM refinement step
    if opt_method:
        from assay_core.tasks import opt as opt_task
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
            tier=tier, functional=functional, basis=basis, density_fit=density_fit,
            solvent_model=solvent_model,
            gate_integrity=False,  # surface opt convergence in the build block, don't abort the build
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

    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def build_parser() -> argparse.ArgumentParser:
    from assay_core import argkit
    p = argparse.ArgumentParser(
        prog="build-from-smiles",
        description="Build a 3D xyz from a SMILES string (Open Babel --gen3d).",
    )
    p.add_argument("smiles",
                   help="SMILES string (e.g. 'CCO'). SMILES-only; resolve a plain "
                        "name with name-to-smiles first.")
    p.add_argument("--out-xyz", default=None,
                   help="Destination .xyz path. Default: <sanitized-smiles>.xyz in cwd.")
    p.add_argument("--name", default=None, help="Title comment for the xyz.")
    p.add_argument("--opt", dest="opt_method", type=argkit._norm_method,
                   choices=["xtb", "mopac", "dft", "hf"], default=None,
                   help="Optional QM refinement after the obabel build.")
    p.add_argument("--solvent", default=None,
                   help="Implicit solvent for the optional QM step (ignored without --opt).")
    p.add_argument("--solvent-model", dest="solvent_model",
                   choices=["ddcosmo", "cpcm", "iefpcm"], default="ddcosmo",
                   help="PySCF continuum model for the optional dft/hf --opt step.")
    p.add_argument("--charge", type=int, default=None,
                   help="Net charge forwarded to the QM step (default 0).")
    p.add_argument("--mult", "--multiplicity", dest="multiplicity", type=int, default=None,
                   help="Spin multiplicity forwarded to the QM step (default 1).")
    p.add_argument("--tier", type=argkit._norm_tier,
                   choices=["fast", "standard", "accurate"], default=None)
    p.add_argument("--functional", default=None)
    p.add_argument("--basis", default=None)
    p.add_argument("--density-fit", dest="density_fit", action="store_true", default=False,
                   help="Enable DFT/HF density fitting (RI) for the optional --opt step.")
    p.add_argument("--out", default=None, help="Result JSON path.")
    argkit._add_stdout_option(p)
    argkit._add_gate_option(p)
    return p


def _resolved_out_xyz(args):
    if args.out_xyz:
        return args.out_xyz
    safe = _re.sub(r"[^A-Za-z0-9_-]", "_", args.smiles)[:60] or "molecule"
    return os.path.abspath(f"{safe}.xyz")


def _call_run(args, cli, pyscf_kwargs):
    return run(
        molecule=args.smiles, out_xyz=_resolved_out_xyz(args), name=args.name,
        opt_method=args.opt_method, opt_solvent=args.solvent,
        opt_charge=args.charge, opt_multiplicity=args.multiplicity,
        tier=args.tier, functional=args.functional, basis=args.basis,
        density_fit=getattr(args, "density_fit", False),
        solvent_model=getattr(args, "solvent_model", "ddcosmo"),
        cli=cli,
        allow_unconverged=pyscf_kwargs.get("allow_unconverged", False),
    )


def _resolve_out(args, result):
    if getattr(args, "out", None):
        return args.out
    xyz = (result or {}).get("xyz_path")
    if xyz:
        return os.path.abspath(f"{os.path.splitext(xyz)[0]}_build.json")
    return os.path.abspath(f"{str(getattr(args, 'smiles', 'build'))}_build.json")


if __name__ == "__main__":
    from assay_core import argkit
    raise SystemExit(argkit.run_cli(build_parser(), run, task="build",
                                    call_run=_call_run, resolve_out=_resolve_out))
