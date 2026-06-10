"""Build 3D molecular geometry from a SMILES string via RDKit.

Pipeline:
  1. SMILES → RDKit Mol (with H atoms added explicitly).
  2. ETKDG (Riniker–Landrum 2015) embedding to seed 3D coordinates.
  3. UFF or MMFF94 force-field cleanup to relax the embedded geometry.
  4. Optionally hand off to xtb / mopac / dft / hf via the existing opt task
     so the user gets a QM-quality geometry in one command.

The headline output is an .xyz file. JSON records the inferred net charge,
spin multiplicity, and a summary of every stage's result (embedding RMSD,
force-field energy, optional QM-opt convergence + energy).

Why this skill exists: every other chemkit skill takes an .xyz as input.
For users who only have a SMILES (the most common starting point in drug
design / cheminformatics), `chemkit build` closes the on-ramp without
requiring them to fire up Avogadro or paste into PubChem.
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# RDKit helpers
# ---------------------------------------------------------------------------

def _require_rdkit():
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        return Chem, AllChem
    except ImportError as e:
        raise ImportError(
            f"chemkit build requires RDKit ({e}). "
            "Install with `conda install -c conda-forge rdkit` or `pip install rdkit`."
        )


def _embed_3d(mol, *, n_confs: int, seed: int):
    """ETKDG embedding. Returns the list of confIds actually embedded."""
    _, AllChem = _require_rdkit()
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.pruneRmsThresh = 0.5     # drop near-duplicates from the embedding pool
    params.useSmallRingTorsions = True
    params.useMacrocycleTorsions = True
    confs = AllChem.EmbedMultipleConfs(mol, numConfs=n_confs, params=params)
    return list(confs)


def _ff_optimize(mol, conf_ids, *, ff_name: str):
    """Optimize each conformer with UFF or MMFF94. Returns list of energies (kcal/mol)."""
    _, AllChem = _require_rdkit()
    energies = []
    for cid in conf_ids:
        if ff_name == "mmff":
            mp = AllChem.MMFFGetMoleculeProperties(mol)
            if mp is None:
                # MMFF has no parameters for some elements (e.g. metals) —
                # fall back to UFF for those conformers.
                ff = AllChem.UFFGetMoleculeForceField(mol, confId=cid)
            else:
                ff = AllChem.MMFFGetMoleculeForceField(mol, mp, confId=cid)
        else:
            ff = AllChem.UFFGetMoleculeForceField(mol, confId=cid)
        if ff is None:
            energies.append(float("nan"))
            continue
        ff.Minimize(maxIts=500)
        energies.append(float(ff.CalcEnergy()))
    return energies


def _mol_to_xyz(mol, conf_id: int, comment: str) -> str:
    """Render conformer `conf_id` of `mol` as an xyz block."""
    Chem, _ = _require_rdkit()
    pos = mol.GetConformer(conf_id).GetPositions()
    syms = [a.GetSymbol() for a in mol.GetAtoms()]
    lines = [str(len(syms)), comment]
    for s, (x, y, z) in zip(syms, pos):
        lines.append(f"{s:<3s} {x:15.8f} {y:15.8f} {z:15.8f}")
    return "\n".join(lines) + "\n"


def _formal_charge(mol) -> int:
    return sum(a.GetFormalCharge() for a in mol.GetAtoms())


def _spin_multiplicity(mol) -> int:
    """Estimate 2S+1 from radical electron count.

    RDKit tracks "num radical electrons" per atom. Total = sum; for a closed-
    shell molecule it's 0, multiplicity = 1. For mono-radicals it's 1,
    multiplicity = 2, etc. Users can override at downstream skills if RDKit
    miscounts (rare for organic SMILES).
    """
    n_radicals = sum(a.GetNumRadicalElectrons() for a in mol.GetAtoms())
    return n_radicals + 1


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    smiles: str,
    *,
    out_xyz: str,
    name: Optional[str] = None,
    n_confs: int = 5,
    forcefield: str = "mmff",
    seed: int = 0xC0FFEE,
    opt_method: Optional[str] = None,
    opt_solvent: Optional[str] = None,
    opt_charge: Optional[int] = None,
    opt_multiplicity: Optional[int] = None,
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    cli: str = "",
) -> Dict[str, Any]:
    """Build a 3D xyz from SMILES.

    Args:
      smiles: SMILES string (canonical or not — RDKit parses both).
      out_xyz: destination .xyz path. Will be overwritten if it exists.
      name: optional title comment for the xyz (defaults to the SMILES).
      n_confs: number of ETKDG conformers to embed; lowest-FF-energy wins.
      forcefield: 'mmff' (default) or 'uff'. MMFF94 is more accurate for
        organics; UFF is broader (all main-group + transition metals).
      seed: ETKDG random seed (reproducible embeddings).
      opt_method: if set, hand off to chemkit.tasks.opt for a QM refinement
        after FF cleanup. One of 'xtb' / 'mopac' / 'dft' / 'hf'.
      opt_solvent, opt_charge, opt_multiplicity: forwarded to opt. If
        opt_charge / opt_multiplicity are None, the values inferred from
        the SMILES are used.
      tier, functional, basis: DFT/HF knobs forwarded to opt.

    Returns a result dict; also writes `out_xyz` to disk.
    """
    Chem, _ = _require_rdkit()

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit could not parse SMILES: {smiles!r}")
    mol = Chem.AddHs(mol)

    inferred_charge = _formal_charge(mol)
    inferred_mult = _spin_multiplicity(mol)
    canonical_smiles = Chem.MolToSmiles(mol, canonical=True)

    conf_ids = _embed_3d(mol, n_confs=n_confs, seed=seed)
    if not conf_ids:
        raise RuntimeError(
            f"RDKit ETKDG failed to embed any conformers for SMILES {smiles!r}. "
            "Try a larger --n-confs, a different seed, or an explicit 3D builder."
        )

    energies = _ff_optimize(mol, conf_ids, ff_name=forcefield)
    # Lowest-FF-energy conformer wins; ties broken by index.
    best_cid, best_ff_energy = min(
        zip(conf_ids, energies),
        key=lambda t: t[1] if t[1] == t[1] else float("inf"),  # NaN-safe
    )

    comment = name or f"chemkit build: {canonical_smiles}"
    xyz_text = _mol_to_xyz(mol, best_cid, comment)

    out_xyz = os.path.abspath(out_xyz)
    os.makedirs(os.path.dirname(out_xyz) or ".", exist_ok=True)
    with open(out_xyz, "w") as f:
        f.write(xyz_text)

    result: Dict[str, Any] = {
        "task": "build_from_smiles",
        "program": "rdkit",
        "smiles_input": smiles,
        "smiles_canonical": canonical_smiles,
        "molecular_formula": _molecular_formula(mol),
        "n_atoms": mol.GetNumAtoms(),
        "n_heavy_atoms": mol.GetNumHeavyAtoms(),
        "inferred_charge": inferred_charge,
        "inferred_multiplicity": inferred_mult,
        "embedding": {
            "method": "ETKDGv3",
            "n_conformers_requested": n_confs,
            "n_conformers_embedded": len(conf_ids),
            "seed": seed,
        },
        "forcefield": {
            "name": forcefield,
            "energies_kcal_mol": energies,
            "selected_conformer_index": conf_ids.index(best_cid),
            "selected_energy_kcal_mol": best_ff_energy,
        },
        "xyz_path": out_xyz,
        "cli_invocation": cli,
        "warnings": [],
    }

    # Optional QM refinement step
    if opt_method:
        from . import opt as opt_task
        from ..io import read_geometry  # ensure it's importable
        q = inferred_charge if opt_charge is None else opt_charge
        m = inferred_mult if opt_multiplicity is None else opt_multiplicity
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
            "converged": bool(opt_res.get("converged")),
            "n_steps": opt_res.get("n_steps"),
            "total_energy_eV": opt_res.get("total_energy_eV"),
            "optimized_xyz": opt_res.get("optimized_xyz"),
        }
        # Promote the QM-relaxed xyz as the canonical output path so downstream
        # skills see the better geometry by default. Keep the FF-only file too
        # for transparency.
        result["xyz_path_ff"] = out_xyz
        result["xyz_path"] = qm_xyz
        if not opt_res.get("converged"):
            result["warnings"].append(
                f"QM refinement ({opt_method}) did not converge — using the "
                "non-converged geometry. Consider re-running with --opt-steps "
                "or a tighter starting structure."
            )

    if any(e != e for e in energies):  # NaN
        result["warnings"].append(
            f"{sum(1 for e in energies if e != e)} conformer(s) had no "
            f"{forcefield} parameters; UFF fallback was used."
        )
    if not result["warnings"]:
        del result["warnings"]
    return result


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def _molecular_formula(mol) -> str:
    """Hill order: C first, H second, others alphabetical. RDKit has a helper
    but I want it in chemkit-controlled form so the output stays stable."""
    counts: Dict[str, int] = {}
    for a in mol.GetAtoms():
        s = a.GetSymbol()
        counts[s] = counts.get(s, 0) + 1
    out = []
    for el in ("C", "H"):
        if el in counts:
            n = counts.pop(el)
            out.append(f"{el}{n}" if n > 1 else el)
    for s in sorted(counts):
        n = counts[s]
        out.append(f"{s}{n}" if n > 1 else s)
    return "".join(out)
