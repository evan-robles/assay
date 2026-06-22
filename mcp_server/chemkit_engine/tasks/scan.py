"""Relaxed dihedral scan — torsional energy profile.

For each dihedral selected (auto-detected rotatable bond or user-specified
i,j,k,l atoms), sweep the dihedral from 0° to 360° in N steps. At each step
optimize the geometry with that dihedral constrained, then record the
*measured* dihedral and the optimized energy.

Outputs per scanned dihedral:
  <stem>_scan_dih<i>_<a>_<b>_<l>.xyz  — relaxed trajectory (one frame/step)
  <stem>_scan_dih<i>_<a>_<b>_<l>.png  — matplotlib E vs angle plot
  (tabular per-point data is embedded in the JSON under dihedrals[*].points)

Constraint mechanics:
  xtb path  — ASE FixInternals: dihedral is *exactly* held at target.
  mopac path — pre-rotate the side atoms to the target dihedral, then run a
               normal PM7 EF optimization. EF relaxes other internal degrees
               while leaving the (already-near-target) dihedral close to the
               seed value; we report the measured value (a few degrees of
               drift per step is expected and noted in the table).
"""
from __future__ import annotations
import functools
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from ase.io import write as ase_write

from ..io import read_geometry
from ..schema import EV_TO_KCAL, base_result, element_warnings
from .confsearch import (
    _detect_rotatable_bonds,
    _component_excluding,
    _set_dihedral_about_bond,
)


def _pdb_residue_labels(input_path: Optional[str], n_atoms: int) -> Optional[List[str]]:
    """For PDB inputs, return per-atom labels like 'ASP47.CA'. Returns None
    if not a PDB file, parse fails, or atom count mismatches the geometry."""
    if not input_path or not input_path.lower().endswith(".pdb"):
        return None
    try:
        labels: List[str] = []
        with open(input_path) as f:
            for line in f:
                if not (line.startswith("ATOM") or line.startswith("HETATM")):
                    continue
                # PDB fixed columns: atom name 13-16, resname 18-20, resnum 23-26
                atom_name = line[12:16].strip()
                resname = line[17:20].strip()
                resnum = line[22:26].strip()
                labels.append(f"{resname}{resnum}.{atom_name}")
        return labels if len(labels) == n_atoms else None
    except Exception:
        return None


def _rdkit_connectivity(atoms, input_path: Optional[str]):
    """Return (heavy_neigh, all_neigh, canonical_ranks) using RDKit. The dicts
    map atom_idx -> set(neighbor_idx) in the original atoms numbering.
    Returns None if RDKit isn't available or bond perception fails."""
    try:
        from rdkit import Chem
        from rdkit.Chem import rdDetermineBonds
    except Exception:
        return None
    try:
        # Build an XYZ block — RDKit's reader needs the standard header.
        symbols = atoms.get_chemical_symbols()
        lines = [str(len(atoms)), ""]
        for s, (x, y, z) in zip(symbols, atoms.positions):
            lines.append(f"{s} {x:.6f} {y:.6f} {z:.6f}")
        block = "\n".join(lines) + "\n"
        m = Chem.MolFromXYZBlock(block)
        if m is None:
            return None
        rdDetermineBonds.DetermineBonds(m, charge=0)
        n = m.GetNumAtoms()
        all_neigh = {i: set() for i in range(n)}
        for b in m.GetBonds():
            i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
            all_neigh[i].add(j); all_neigh[j].add(i)
        heavy = {i for i in range(n) if m.GetAtomWithIdx(i).GetSymbol() != "H"}
        heavy_neigh = {i: (all_neigh[i] & heavy) for i in heavy}
        ranks = list(Chem.CanonicalRankAtoms(m, breakTies=True))
        return heavy_neigh, all_neigh, ranks
    except Exception:
        return None


def _ase_connectivity(atoms):
    """Fallback connectivity from ASE covalent-radius neighbor lists."""
    from ase.neighborlist import NeighborList, natural_cutoffs
    n = len(atoms)
    cutoffs = natural_cutoffs(atoms, mult=1.15)
    nl = NeighborList(cutoffs, self_interaction=False, bothways=True)
    nl.update(atoms)
    all_neigh = {i: set(nl.get_neighbors(i)[0].tolist()) for i in range(n)}
    symbols = atoms.get_chemical_symbols()
    heavy = {i for i in range(n) if symbols[i] != "H"}
    heavy_neigh = {i: (all_neigh[i] & heavy) for i in heavy}
    return heavy_neigh, all_neigh, None


def _canonical_reorder(atoms, input_path: Optional[str] = None):
    """Reorder atoms so heavy atoms appear in chain order — longest simple path
    in the heavy-atom graph — followed by hydrogens grouped by parent.

    Prefers RDKit for bond perception (catches double bonds, aromaticity, etc.)
    and uses RDKit canonical ranks to break direction/tie ambiguities. Falls
    back to ASE covalent-radius neighbor lists if RDKit can't perceive bonds.

    Note: this is a "main-chain + chain-order" reordering, not strict IUPAC
    nomenclature. It coincides with IUPAC numbering for n-alkanes; for
    substituted/heteroatom/cyclic molecules the labels are chain-order but may
    not match IUPAC locants exactly.

    Returns (new_atoms, mapping) where mapping[new_idx] = old_idx; (atoms, None)
    on no-op or failure."""
    try:
        from ase import Atoms
        n = len(atoms)
        if n < 4:
            return atoms, None
        conn = _rdkit_connectivity(atoms, input_path) or _ase_connectivity(atoms)
        heavy_neigh, all_neigh, ranks = conn
        heavy = list(heavy_neigh.keys())
        if len(heavy) < 2:
            return atoms, None
        symbols = atoms.get_chemical_symbols()

        terminals = [i for i in heavy if len(heavy_neigh[i]) <= 1]
        n_heavy = len(heavy)
        n_heavy_edges = sum(len(heavy_neigh[i]) for i in heavy) // 2
        is_tree = (n_heavy_edges == n_heavy - 1)

        from collections import deque

        def bfs_farthest(src):
            """Return (farthest_node, parent_map) from src in heavy subgraph."""
            dist = {src: 0}; parent = {src: None}; dq = deque([src])
            far = src
            while dq:
                u = dq.popleft()
                for v in sorted(heavy_neigh[u]):
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        parent[v] = u
                        dq.append(v)
                        if dist[v] > dist[far]:
                            far = v
            return far, parent, dist

        heavy_order: List[int] = []
        if is_tree:
            # Two-pass BFS gives exact longest path in O(N).
            seed = terminals[0] if terminals else heavy[0]
            u, _, _ = bfs_farthest(seed)
            v, parent, _ = bfs_farthest(u)
            path = []
            cur = v
            while cur is not None:
                path.append(cur); cur = parent[cur]
            path.reverse()
            heavy_order = path
            # Walk remaining (branched) heavy atoms in BFS layers off the main chain.
            if len(heavy_order) < n_heavy:
                seen = set(heavy_order)
                dq = deque(heavy_order)
                while dq:
                    u = dq.popleft()
                    for w in sorted(heavy_neigh[u]):
                        if w not in seen:
                            seen.add(w); heavy_order.append(w); dq.append(w)
        else:
            # Cyclic graph: BFS from the lowest-degree (or first) terminal/heavy.
            # Avoids O(N!) longest-simple-path on cyclic molecules (NP-hard).
            seed = (sorted(terminals)[0] if terminals
                    else min(heavy, key=lambda i: (len(heavy_neigh[i]), i)))
            _, _, _ = bfs_farthest(seed)  # warmup; not used
            seen = {seed}; heavy_order = [seed]; dq = deque([seed])
            while dq:
                u = dq.popleft()
                for v in sorted(heavy_neigh[u]):
                    if v not in seen:
                        seen.add(v); heavy_order.append(v); dq.append(v)
            for h in heavy:
                if h not in seen:
                    heavy_order.append(h)

        # Direction tie-break: if RDKit gave us canonical ranks, walk so that
        # the lower-ranked endpoint becomes C0 (gives lowest-locant-style start).
        if ranks is not None and len(heavy_order) >= 2:
            a, b = heavy_order[0], heavy_order[-1]
            if ranks[b] < ranks[a]:
                heavy_order = list(reversed(heavy_order))

        new_order: List[int] = list(heavy_order)
        used = set(new_order)
        for h in heavy_order:
            for nb in sorted(all_neigh[h]):
                if symbols[nb] == "H" and nb not in used:
                    new_order.append(nb); used.add(nb)
        for i in range(n):
            if i not in used:
                new_order.append(i)
        if new_order == list(range(n)):
            return atoms, None
        new = Atoms(
            symbols=[symbols[i] for i in new_order],
            positions=[atoms.positions[i] for i in new_order],
        )
        return new, new_order
    except Exception:
        return atoms, None


def _lookup_molecule_name(input_path: str) -> Optional[str]:
    """Try to resolve a human-readable molecule name (e.g. 'pentane') from the
    input geometry. Pipeline: xyz -> InChI (Open Babel) -> PubChem IUPAC name.
    Returns None if any step fails or no network."""
    try:
        import subprocess, urllib.request, urllib.parse
        r = subprocess.run(
            ["obabel", input_path, "-oinchi"],
            capture_output=True, text=True, timeout=10,
        )
        inchi = (r.stdout or "").strip().split()[0] if r.stdout else ""
        if not inchi.startswith("InChI="):
            return None
        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchi/"
            "property/IUPACName/TXT"
        )
        data = urllib.parse.urlencode({"inchi": inchi}).encode()
        with urllib.request.urlopen(url, data=data, timeout=5) as resp:
            name = resp.read().decode().strip().splitlines()[0]
        return name or None
    except Exception:
        return None


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    dihedral: Optional[Tuple[int, int, int, int]] = None,
    n_steps: int = 24,
    fmax: float = 0.05,
    opt_steps: int = 200,
    out_stem: Optional[str] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    method = method.lower()
    if method not in {"xtb", "mopac", "dft", "hf"}:
        raise ValueError(f"scan: unsupported method {method!r}")

    atoms = read_geometry(input_path)
    atoms, reorder_map = _canonical_reorder(atoms, input_path)
    symbols = atoms.get_chemical_symbols()
    if dihedral is not None and reorder_map is not None:
        inv = {old: new for new, old in enumerate(reorder_map)}
        try:
            dihedral = tuple(inv[k] for k in dihedral)
        except KeyError:
            pass

    bonds = _resolve_dihedrals(atoms, dihedral)

    if out_stem is None:
        out_stem = os.path.splitext(os.path.abspath(input_path))[0] + f"_scan_{method}"

    from ..calculators import method_label as _ml, program_label as _pl, build_calculator as _bc
    _calc_for_label = None
    if method in ("dft", "hf"):
        _calc_for_label = _bc(method, charge=charge, multiplicity=multiplicity,
                              solvent=solvent, tier=tier, functional=functional,
                              basis=basis, density_fit=density_fit,
                              solvent_model=solvent_model)
    result = base_result(
        task="conformational_analysis",
        method=_ml(method, _calc_for_label),
        program=_pl(method),
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=symbols,
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )
    result["scan_type"] = "relaxed_dihedral"
    result["constraint"] = (
        "exact (ASE FixInternals)" if method == "xtb"
        else "soft (pre-rotated seed + PM7 EF relaxation)"
    )
    result["steps_per_scan"] = n_steps
    result["n_dihedrals_scanned"] = len(bonds)

    warns = element_warnings(symbols, method)
    if not bonds:
        warns.append(
            "No rotatable dihedrals detected (or supplied) — nothing to scan. "
            "For rigid molecules use --dihedral i,j,k,l to force a scan."
        )
        result["dihedrals"] = []
        if warns:
            result["warnings"] = warns
        from ..integrity import finalize
        return finalize(result, gate_integrity=gate_integrity,
                        allow_unconverged=allow_unconverged)

    dihedral_records: List[Dict[str, Any]] = []
    for bond in bonds:
        points, traj_atoms = _scan_one_dihedral(
            atoms.copy(), bond,
            method=method, charge=charge, multiplicity=multiplicity,
            solvent=solvent, n_steps=n_steps, fmax=fmax, opt_steps=opt_steps,
            tier=tier, functional=functional, basis=basis, density_fit=density_fit,
            solvent_model=solvent_model,
        )
        if not points:
            dihedral_records.append({
                "atoms_1based": [bond["i"]+1, bond["a"]+1, bond["b"]+1, bond["l"]+1],
                "n_points": 0, "n_converged": 0,
                "note": "All scan-point optimizations failed.",
            })
            continue

        tag = _dihedral_tag(bond)
        traj_path = f"{out_stem}_dih{tag}.xyz"
        plot_path = f"{out_stem}_dih{tag}.png"

        _write_trajectory(traj_path, traj_atoms, points)
        if "_mol_name_cache" not in locals():
            _mol_name_cache = _lookup_molecule_name(input_path)
        if "_res_label_cache" not in locals():
            res_labels = _pdb_residue_labels(input_path, len(atoms))
            if res_labels is not None and reorder_map is not None:
                res_labels = [res_labels[old] for old in reorder_map]
            _res_label_cache = res_labels
        _write_plot(
            plot_path, bond, points,
            method=method, input_path=input_path,
            atom_symbols=list(atoms.get_chemical_symbols()),
            molecule_name=_mol_name_cache,
            atom_labels=_res_label_cache,
        )

        e_min = min(p["energy_kcal_mol"] for p in points if p["energy_kcal_mol"] is not None)
        valid = [p for p in points if p["energy_kcal_mol"] is not None]
        for p in valid:
            p["delta_E_kcal_mol"] = p["energy_kcal_mol"] - e_min
        e_max_pt = max(valid, key=lambda p: p["energy_kcal_mol"])
        e_min_pt = min(valid, key=lambda p: p["energy_kcal_mol"])
        grid_barrier = e_max_pt["energy_kcal_mol"] - e_min_pt["energy_kcal_mol"]

        # The grid-point barrier underestimates a sharp torsional maximum that
        # falls BETWEEN sampled angles (default 15° spacing). A relaxed dihedral
        # profile is periodic in 360°, so fit a truncated Fourier series and read
        # the barrier off a dense evaluation — this removes the grid-alignment
        # dependence (different n_steps no longer give different barriers) without
        # adding a scipy dependency.
        interp = _interpolated_barrier(
            [p.get("target_deg") if p.get("target_deg") is not None
             else p.get("measured_deg") for p in valid],
            [p["energy_kcal_mol"] for p in valid],
        )

        record = {
            "atoms_1based": [bond["i"]+1, bond["a"]+1, bond["b"]+1, bond["l"]+1],
            "n_points": len(points),
            "n_converged": sum(1 for p in points if p["converged"]),
            "min_angle_deg": e_min_pt["measured_deg"],
            "min_energy_kcal_mol": e_min_pt["energy_kcal_mol"],
            "max_angle_deg": e_max_pt["measured_deg"],
            "max_energy_kcal_mol": e_max_pt["energy_kcal_mol"],
            # Headline barrier: never below the grid evidence (a smoothing fit
            # can dip under a sharp sampled peak); take the larger of the
            # interpolated and grid values. Both are reported for auditability.
            "barrier_kcal_mol": (
                max(interp["barrier_kcal_mol"], grid_barrier) if interp
                else grid_barrier
            ),
            "barrier_grid_kcal_mol": grid_barrier,
            "trajectory_xyz": traj_path,
            "plot": plot_path,
            "points": valid,
        }
        if interp:
            record["barrier_interpolated_kcal_mol"] = interp["barrier_kcal_mol"]
            record["max_angle_interpolated_deg"] = interp["max_angle_deg"]
            record["min_angle_interpolated_deg"] = interp["min_angle_deg"]
            # If the interpolated maximum sits noticeably above the grid max, the
            # true barrier was between sampled points — flag so the user can
            # densify the scan if they need the precise barrier.
            if interp["barrier_kcal_mol"] - grid_barrier > 0.3:
                warns.append(
                    f"Dihedral {record['atoms_1based']}: the interpolated barrier "
                    f"({interp['barrier_kcal_mol']:.2f} kcal/mol) exceeds the grid "
                    f"barrier ({grid_barrier:.2f}) by "
                    f"{interp['barrier_kcal_mol']-grid_barrier:.2f} kcal/mol — the "
                    "true maximum lies between sampled angles. Increase --steps "
                    "for a more precise barrier."
                )
        dihedral_records.append(record)

    result["dihedrals"] = dihedral_records
    if warns:
        result["warnings"] = warns

    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def _interpolated_barrier(angles_deg, energies_kcal):
    """Fit a periodic (360°) truncated Fourier series to a relaxed dihedral
    profile and return the barrier from a dense evaluation.

    Returns {"barrier_kcal_mol", "max_angle_deg", "min_angle_deg"} or None when
    a fit isn't warranted (too few points, missing angles, non-finite values).
    numpy-only; robust to non-uniform angle spacing via least squares.
    """
    pts = [
        (a, e) for a, e in zip(angles_deg, energies_kcal)
        if a is not None and e is not None and np.isfinite(e)
    ]
    # Need a handful of points for a meaningful periodic fit.
    if len(pts) < 6:
        return None
    theta = np.radians(np.array([a for a, _ in pts], dtype=float))
    y = np.array([e for _, e in pts], dtype=float)

    # Number of harmonics: enough to capture 1-, 2-, 3-fold torsional terms but
    # bounded well below the Nyquist limit of the sample count to avoid ringing.
    n_harm = min(6, (len(pts) - 1) // 2)
    if n_harm < 1:
        return None
    # Design matrix: [1, cos θ, sin θ, cos 2θ, sin 2θ, ...]
    cols = [np.ones_like(theta)]
    for k in range(1, n_harm + 1):
        cols.append(np.cos(k * theta))
        cols.append(np.sin(k * theta))
    A = np.vstack(cols).T
    try:
        coeffs, *_ = np.linalg.lstsq(A, y, rcond=None)
    except Exception:
        return None

    # Evaluate on a dense uniform grid over the full period.
    grid = np.radians(np.linspace(0.0, 360.0, 1441, endpoint=False))  # 0.25° step
    gcols = [np.ones_like(grid)]
    for k in range(1, n_harm + 1):
        gcols.append(np.cos(k * grid))
        gcols.append(np.sin(k * grid))
    fit = np.vstack(gcols).T @ coeffs
    if not np.all(np.isfinite(fit)):
        return None
    imax = int(np.argmax(fit))
    imin = int(np.argmin(fit))
    barrier = float(fit[imax] - fit[imin])
    # Guard against a pathological fit returning a smaller barrier than the data.
    if barrier < 0:
        return None
    return {
        "barrier_kcal_mol": barrier,
        "max_angle_deg": float(np.degrees(grid[imax])),
        "min_angle_deg": float(np.degrees(grid[imin])),
    }


# ---------------------------------------------------------------------------
# Dihedral resolution
# ---------------------------------------------------------------------------

def _resolve_dihedrals(
    atoms, user_dihedral: Optional[Sequence[int]],
) -> List[Dict[str, Any]]:
    """Return a list of bond dicts in the format _detect_rotatable_bonds yields."""
    if user_dihedral is not None:
        i, a, b, l = (int(x) for x in user_dihedral)
        n = len(atoms)
        for k in (i, a, b, l):
            if not 0 <= k < n:
                raise ValueError(
                    f"--dihedral atom index {k} out of range (molecule has {n} atoms)"
                )
        # Build neighbor sets the same way confsearch does so we can find which
        # atoms move with l (the "side_b" set).
        from ase.neighborlist import NeighborList, natural_cutoffs
        cutoffs = natural_cutoffs(atoms, mult=1.15)
        nl = NeighborList(cutoffs, self_interaction=False, bothways=True)
        nl.update(atoms)
        neighbors = [set(nl.get_neighbors(k)[0].tolist()) for k in range(n)]
        side_b = _component_excluding(neighbors, start=b, blocked=a, n=n)
        if i in side_b:
            # Pathological selection — bond doesn't actually separate sides
            raise ValueError(
                f"--dihedral {i},{a},{b},{l}: rotating about (a={a},b={b}) "
                f"doesn't isolate atom {l} from atom {i} (likely a ring bond)"
            )
        return [{
            "a": a, "b": b, "side_b": list(side_b), "i": i, "l": l,
        }]
    return _detect_rotatable_bonds(atoms, include_methyl=True)


def _dihedral_tag(bond: Dict[str, Any]) -> str:
    # 1-based for user-facing filenames; internal bond dict stays 0-based.
    return f"{bond['i']+1}_{bond['a']+1}_{bond['b']+1}_{bond['l']+1}"


# ---------------------------------------------------------------------------
# Per-dihedral scan driver
# ---------------------------------------------------------------------------

def _scan_one_dihedral(
    atoms, bond: Dict[str, Any], *, method: str, charge: int, multiplicity: int,
    solvent: Optional[str], n_steps: int, fmax: float, opt_steps: int,
    tier: Optional[str] = None, functional: Optional[str] = None,
    basis: Optional[str] = None, density_fit: bool = False,
    solvent_model: str = "ddcosmo",
) -> Tuple[List[Dict[str, Any]], List]:
    """Sweep one dihedral through n_steps equally-spaced target angles.

    Returns (point_records, trajectory_frames). Each point_record has:
      step, target_deg, measured_deg, energy_kcal_mol (or None), converged
    """
    i, a, b, l = bond["i"], bond["a"], bond["b"], bond["l"]
    side_b = bond["side_b"]
    targets = np.linspace(0.0, 360.0, n_steps, endpoint=False).tolist()

    points: List[Dict[str, Any]] = []
    frames: List = []

    # For dft/hf, build ONE PySCFCalculator for the whole dihedral and reuse it
    # across every scan point. Consecutive points are seeded from the previous
    # optimized geometry, so they are electronically very close — reusing the
    # calculator lets its cached converged density matrix warm-start each point's
    # SCF (cutting iterations ~2-3x). A fresh calculator per point (the old
    # behavior) threw that cache away every step. xtb/mopac have no DM cache to
    # preserve, so they keep building per-point (None => per-point build).
    shared_calc = None
    if method in ("dft", "hf"):
        from ..calculators import build_calculator
        shared_calc = build_calculator(
            method, charge=charge, multiplicity=multiplicity, solvent=solvent,
            tier=tier, functional=functional, basis=basis, density_fit=density_fit,
            solvent_model=solvent_model,
        )

    # Seed each step from the previous *optimized* geometry — gives smoother
    # profiles (the optimizer already knows where the H's want to be) and is
    # what a hand-rolled scan would do.
    current = atoms.copy()

    for step, target in enumerate(targets):
        seed = current.copy()
        # Pre-rotate to bring the dihedral close to target. For the xtb path
        # this just gives the constraint a clean starting guess; for mopac
        # it *is* the constraint mechanism.
        try:
            current_phi = seed.get_dihedral(i, a, b, l)
        except Exception:
            current_phi = 0.0
        delta = target - current_phi
        _set_dihedral_about_bond(seed, a, b, side_b, delta)

        if method == "mopac":
            opt_atoms, energy_eV, converged = _opt_with_mopac_relaxation(
                seed, charge=charge, multiplicity=multiplicity, solvent=solvent,
                fmax=fmax, steps=opt_steps,
            )
        else:
            # xtb, dft, hf: ASE BFGS + FixInternals constraint via build_calculator.
            opt_atoms, energy_eV, converged = _opt_with_ase_dihedral_constraint(
                seed, dihedral=(i, a, b, l), target_deg=target,
                method=method,
                charge=charge, multiplicity=multiplicity, solvent=solvent,
                fmax=fmax, steps=opt_steps,
                tier=tier, functional=functional, basis=basis,
                density_fit=density_fit,
                solvent_model=solvent_model,
                calc=shared_calc,
            )

        if opt_atoms is None or energy_eV is None or not np.isfinite(energy_eV):
            points.append({
                "step": step, "target_deg": float(target),
                "measured_deg": None, "energy_kcal_mol": None,
                "converged": False,
            })
            # Don't update `current` — re-seed next step from previous good.
            continue

        try:
            measured = float(opt_atoms.get_dihedral(i, a, b, l))
        except Exception:
            measured = None

        energy_kcal = float(energy_eV) * EV_TO_KCAL
        points.append({
            "step": step, "target_deg": float(target),
            "measured_deg": measured, "energy_kcal_mol": energy_kcal,
            "converged": bool(converged),
        })
        frames.append(opt_atoms.copy())
        current = opt_atoms

    return points, frames


def _opt_with_ase_dihedral_constraint(
    atoms, *, dihedral: Tuple[int, int, int, int], target_deg: float,
    method: str,
    charge: int, multiplicity: int, solvent: Optional[str],
    fmax: float, steps: int,
    tier: Optional[str] = None, functional: Optional[str] = None,
    basis: Optional[str] = None, density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    calc=None,
) -> Tuple[Optional[Any], Optional[float], bool]:
    from ase.constraints import FixInternals
    from ase.optimize import BFGS
    from ..calculators import build_calculator, apply_calc_to_atoms

    # Reuse a caller-supplied calculator (dft/hf warm-start across scan points);
    # otherwise build a fresh one (xtb/mopac, or standalone calls).
    if calc is None:
        calc = build_calculator(
            method, charge=charge, multiplicity=multiplicity, solvent=solvent,
            tier=tier, functional=functional, basis=basis, density_fit=density_fit,
            solvent_model=solvent_model,
        )
    apply_calc_to_atoms(atoms, calc)

    # FixInternals expects radians for dihedrals.
    target_rad = float(np.deg2rad(target_deg))
    constraint = FixInternals(dihedrals_deg=[[float(target_deg), list(dihedral)]]) \
        if _fixinternals_takes_degrees() \
        else FixInternals(dihedrals=[[target_rad, list(dihedral)]])
    atoms.set_constraint(constraint)

    try:
        dyn = BFGS(atoms, logfile=None)
        converged = dyn.run(fmax=fmax, steps=steps)
        energy_eV = float(atoms.get_potential_energy())
    except Exception:
        return None, None, False
    finally:
        # Strip constraint so downstream measurements (.get_dihedral, .copy())
        # behave like an unconstrained Atoms object.
        atoms.set_constraint()
    return atoms, energy_eV, bool(converged)


@functools.lru_cache(maxsize=1)
def _fixinternals_takes_degrees() -> bool:
    """Newer ASE FixInternals uses `dihedrals_deg`; older uses `dihedrals`."""
    try:
        from ase.constraints import FixInternals
        import inspect
        sig = inspect.signature(FixInternals.__init__)
        return "dihedrals_deg" in sig.parameters
    except Exception:
        return False


def _opt_with_mopac_relaxation(
    atoms, *, charge: int, multiplicity: int, solvent: Optional[str],
    fmax: float, steps: int,
) -> Tuple[Optional[Any], Optional[float], bool]:
    """Run a single-shot MOPAC EF optimization on the pre-rotated geometry."""
    import tempfile
    from ase.io import read as ase_read
    from .opt import _run_mopac

    # _run_mopac wants an input_path (used for bookkeeping). Write a temp xyz.
    workdir = tempfile.mkdtemp(prefix="chemkit_scan_mop_")
    seed_xyz = os.path.join(workdir, "seed.xyz")
    out_xyz = os.path.join(workdir, "opt.xyz")
    ase_write(seed_xyz, atoms, format="xyz")

    try:
        res = _run_mopac(
            input_path=seed_xyz,
            atoms=atoms,
            symbols=atoms.get_chemical_symbols(),
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            fmax=fmax,
            steps=steps,
            out_xyz=out_xyz,
            cli="(internal scan-point)",
        )
    except Exception:
        return None, None, False

    hof_kcal = res.get("final_heat_of_formation_kcal_mol")
    if hof_kcal is None:
        return None, None, False

    opt_atoms = ase_read(out_xyz)
    energy_eV = hof_kcal / 23.060547830619026  # kcal/mol -> eV
    return opt_atoms, energy_eV, bool(res.get("converged"))


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _write_trajectory(path: str, frames: List, points: List[Dict[str, Any]]) -> None:
    """Multi-frame XYZ with informative comment lines."""
    if not frames:
        return
    valid_points = [p for p in points if p["energy_kcal_mol"] is not None]
    with open(path, "w") as f:
        for atoms, p in zip(frames, valid_points):
            symbols = atoms.get_chemical_symbols()
            pos = atoms.get_positions()
            f.write(f"{len(symbols)}\n")
            f.write(
                f"step={p['step']} "
                f"target={p['target_deg']:.2f}deg "
                f"measured={p['measured_deg']:.2f}deg "
                f"E={p['energy_kcal_mol']:.4f}kcal/mol "
                f"converged={p['converged']}\n"
            )
            for sym, (x, y, z) in zip(symbols, pos):
                f.write(f"{sym:<3s} {x:15.8f} {y:15.8f} {z:15.8f}\n")


def _write_plot(
    path: str, bond: Dict[str, Any], points: List[Dict[str, Any]],
    *, method: str, input_path: Optional[str] = None,
    atom_symbols: Optional[List[str]] = None,
    molecule_name: Optional[str] = None,
    atom_labels: Optional[List[str]] = None,
) -> None:
    """Energy-vs-angle line plot. Falls back to no-op if matplotlib missing."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    valid = [p for p in points if p["energy_kcal_mol"] is not None
             and p["measured_deg"] is not None]
    if not valid:
        return

    # Sort by measured angle so the line traces the profile, not the scan order.
    valid.sort(key=lambda p: p["measured_deg"])
    angles = [p["measured_deg"] for p in valid]
    e_min = min(p["energy_kcal_mol"] for p in valid)
    dE = [p["energy_kcal_mol"] - e_min for p in valid]

    idxs = (bond["i"], bond["a"], bond["b"], bond["l"])
    # User-facing labels are 1-based; bond indices stay 0-based internally.
    if atom_labels is not None:
        labeled = "–".join(atom_labels[k] for k in idxs)
    elif atom_symbols is not None:
        labeled = "–".join(f"{atom_symbols[k]}{k+1}" for k in idxs)
    else:
        labeled = "–".join(str(k + 1) for k in idxs)

    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    ax.plot(angles, dE, "o-", color="#2e6fdf", markersize=5, linewidth=1.4)
    ax.set_xlabel(f"Dihedral {labeled} (degrees)")
    ax.set_ylabel(r"$\Delta E$ (kcal/mol)")
    if method == "xtb":
        method_label = "GFN2-xTB"
    elif method == "mopac":
        method_label = "PM7 / MOPAC"
    elif method == "dft":
        method_label = "DFT (PySCF)"
    elif method == "hf":
        method_label = "HF (PySCF)"
    else:
        method_label = method.upper()
    if molecule_name:
        mol_name = molecule_name
    elif input_path:
        mol_name = os.path.splitext(os.path.basename(input_path))[0]
    else:
        mol_name = ""
    title_main = f"Relaxed dihedral scan — {method_label}"
    if mol_name:
        ax.set_title(f"{mol_name}\n{title_main}\ndihedral: {labeled}", fontsize=10)
    else:
        ax.set_title(f"{title_main}\ndihedral: {labeled}", fontsize=10)
    ax.set_xlim(0, 360)
    ax.set_xticks(range(0, 361, 60))
    ax.grid(True, alpha=0.3)
    ax.axhline(0.0, color="gray", linewidth=0.6, alpha=0.5)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
