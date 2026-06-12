"""Conformational search via Open Babel (confab diverse generator).

Conformers are sampled with Open Babel's force-field `confab` generator and
ranked by force-field energy (obenergy, MMFF94 with UFF fallback). Force-field
sampling smooths over shallow conformers (e.g. n-pentane's gauche minima
collapse to anti), so we optionally re-optimize the ensemble with a harder
method (PM7 via MOPAC) — plus explicit ring-pucker and dihedral-grid seeding —
to recover those minima and re-rank by heat of formation.
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from ase.io import read as ase_read, write as ase_write

from ..io import read_geometry
from ..schema import base_result


def run(
    input_path: str,
    *,
    method: str = "xtb",          # canonical token; sampler is obabel confab
    solvent: Optional[str] = None,
    n_max_conformers: int = 20,
    postopt: str = "none",        # 'none' or 'mopac'
    postopt_rmsd: float = 0.25,   # Å, dedup threshold after post-optimization
    postopt_ewin: float = 6.0,    # kcal/mol, keep ≤ this from lowest
    charge: int = 0,
    multiplicity: int = 1,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    # tier/functional/basis are accepted for CLI uniformity but ignored:
    # the obabel sampler is force-field based and the post-opt path is
    # xtb/mopac-only.
    del tier, functional, basis
    if method != "xtb":
        # 'xtb' is retained as the canonical method token for CLI uniformity,
        # but the conformer sampler is now Open Babel (force-field confab).
        raise ValueError(
            "confsearch only accepts method='xtb' (the default); the sampler "
            "is Open Babel confab and the optional post-opt is PM7 (MOPAC)."
        )

    workdir = tempfile.mkdtemp(prefix="chemkit_confsearch_")
    src_xyz = os.path.join(workdir, "input.xyz")
    ase_write(src_xyz, read_geometry(input_path))

    preopt_note: Optional[str] = None

    # Sample conformers with Open Babel's confab (diverse FF generator), with
    # graceful fallback to the genetic search and finally the input geometry.
    conformer_xyz, conformers = _run_obabel_confab(
        src_xyz, workdir, n_max=n_max_conformers, rmsd=postopt_rmsd,
    )
    if len(conformers) <= 1:
        preopt_note = (
            "Open Babel produced a single conformer; proceeding with "
            "ring-pucker / dihedral-grid seeding for post-opt."
        )

    # Rank by force-field energy (obenergy MMFF94 -> UFF fallback).
    energies_kcal = _energies_for_frames(conformers, workdir)
    order = sorted(
        range(len(conformers)),
        key=lambda i: (energies_kcal[i] if energies_kcal[i] is not None
                       else float("inf")),
    )
    conformers = [conformers[i] for i in order]
    energies_kcal = [energies_kcal[i] for i in order]

    # Persist the ranked ensemble and the lowest-energy conformer.
    ase_write(conformer_xyz, conformers, format="xyz")
    best_xyz = os.path.join(workdir, "obabel_best.xyz")
    ase_write(best_xyz, conformers[0], format="xyz")

    atoms = read_geometry(input_path)
    result = base_result(
        task="conformational_search",
        method="MMFF94 confab (Open Babel)",
        program="openbabel",
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms),
        atoms=atoms.get_chemical_symbols(),
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent, cli=cli,
    )
    keep = min(len(conformers), n_max_conformers)
    result["n_conformers_found"] = len(conformers)
    result["n_conformers_kept"] = keep
    result["work_directory"] = workdir
    result["best_conformer_xyz"] = best_xyz
    result["all_conformers_xyz"] = conformer_xyz
    if preopt_note:
        result["preoptimization"] = preopt_note
    finite = [e for e in energies_kcal[:keep] if e is not None]
    if finite:
        e0 = finite[0]
        result["conformer_relative_energies_kcal_mol"] = [
            (e - e0) if e is not None else None for e in energies_kcal[:keep]
        ]

    if postopt == "mopac":
        rotatable_bonds = _detect_rotatable_bonds(atoms)
        rings = _detect_rings(atoms)
        seeds, seed_source = _gather_postopt_seeds(
            workdir=workdir,
            base_conformers=conformers,
            max_seeds=max(n_max_conformers, 81),
            rotatable_bonds=rotatable_bonds,
            rings=rings,
            base_atoms=atoms,
        )
        post = _postopt_mopac(
            conformers=seeds,
            workdir=workdir,
            charge=charge,
            multiplicity=multiplicity,
            solvent=solvent,
            rmsd_threshold=postopt_rmsd,
            ewin_kcal=postopt_ewin,
            rotatable_bonds=rotatable_bonds,
        )
        post["seed_source"] = seed_source
        result["postopt"] = post
    elif postopt != "none":
        raise ValueError(f"Unknown --postopt value: {postopt!r}")

    return result


def _require_obabel() -> str:
    """Return the path to the obabel executable or raise a helpful error."""
    exe = shutil.which("obabel")
    if exe is None:
        raise RuntimeError(
            "chemkit confsearch requires Open Babel (`obabel`), which was not "
            "found on PATH. Install with `conda install -c conda-forge openbabel`."
        )
    return exe


def _run_obabel_confab(
    src_xyz: str, workdir: str, *, n_max: int, rmsd: float,
) -> Tuple[str, list]:
    """Generate a diverse conformer ensemble with Open Babel's confab.

    confab is Open Babel's diverse-conformer generator (force-field based). It
    will not write a usable multi-conformer .xyz directly (it emits 0 frames to
    .xyz), so we route through an SDF intermediate and convert to multi-xyz.

    Falls back to the genetic `--conformer` search, and finally to the input
    geometry as a single seed, so the downstream ring-pucker / dihedral-grid
    seeding and PM7 post-opt can still run even when sampling is sparse.

    Returns (multi_xyz_path, list_of_Atoms).
    """
    obabel = _require_obabel()
    multi_xyz = os.path.join(workdir, "obabel_conformers.xyz")

    def _convert_sdf_to_xyz(sdf_path: str) -> list:
        if not (os.path.isfile(sdf_path) and os.path.getsize(sdf_path) > 0):
            return []
        subprocess.run(
            [obabel, sdf_path, "-O", multi_xyz],
            capture_output=True, text=True, cwd=workdir, timeout=600,
        )
        if not (os.path.isfile(multi_xyz) and os.path.getsize(multi_xyz) > 0):
            return []
        frames = ase_read(multi_xyz, index=":")
        return frames if isinstance(frames, list) else [frames]

    # 1. confab diverse search (SDF intermediate).
    confab_sdf = os.path.join(workdir, "confab.sdf")
    subprocess.run(
        [obabel, src_xyz, "-O", confab_sdf, "--confab",
         "--conf", str(max(n_max * 4, 50)),
         "--rcutoff", str(rmsd), "--ecutoff", "50"],
        capture_output=True, text=True, cwd=workdir, timeout=1800,
    )
    frames = _convert_sdf_to_xyz(confab_sdf)
    if len(frames) > 1:
        return multi_xyz, frames

    # 2. Fallback: genetic --conformer search written straight to xyz.
    wc_xyz = os.path.join(workdir, "obabel_conformer_ga.xyz")
    subprocess.run(
        [obabel, src_xyz, "-O", wc_xyz, "--conformer",
         "--nconf", str(max(n_max, 30)), "--writeconformers"],
        capture_output=True, text=True, cwd=workdir, timeout=1800,
    )
    if os.path.isfile(wc_xyz) and os.path.getsize(wc_xyz) > 0:
        ga_frames = ase_read(wc_xyz, index=":")
        ga_frames = ga_frames if isinstance(ga_frames, list) else [ga_frames]
        if len(ga_frames) > 1:
            shutil.copyfile(wc_xyz, multi_xyz)
            return multi_xyz, ga_frames

    # 3. Last resort: the input geometry as a single seed.
    atoms = read_geometry(src_xyz)
    ase_write(multi_xyz, atoms, format="xyz")
    return multi_xyz, [atoms]


def _obenergy(xyz_path: str, ff: str = "MMFF94") -> Optional[float]:
    """Single-point force-field energy (kcal/mol) via Open Babel's obenergy.

    Tries MMFF94 first, falls back to UFF (covers elements MMFF lacks params
    for). Returns None if both fail or no energy could be parsed.
    """
    exe = shutil.which("obenergy")
    if exe is None:
        return None
    for forcefield in (ff, "UFF"):
        try:
            proc = subprocess.run(
                [exe, "-ff", forcefield, xyz_path],
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(
            r"TOTAL ENERGY\s*=\s*([-+]?\d+\.?\d*(?:[Ee][-+]?\d+)?)",
            proc.stdout,
        )
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _energies_for_frames(frames: list, workdir: str) -> List[Optional[float]]:
    """obenergy each conformer frame; returns kcal/mol (None where it failed)."""
    energies: List[Optional[float]] = []
    tmp = os.path.join(workdir, "_score.xyz")
    for atoms in frames:
        ase_write(tmp, atoms, format="xyz")
        energies.append(_obenergy(tmp))
    return energies


def _detect_rings(atoms, *, min_size: int = 4, max_size: int = 8) -> List[Dict[str, Any]]:
    """Find all non-aromatic, all-sp3-ish rings of size [min_size, max_size].

    Pure connectivity-based detection (no RDKit). Returns one dict per ring:
      {"size": k, "atoms": [ordered indices around the ring]}.
    Aromatic / sp2-heavy rings are filtered out by checking that every ring
    carbon has 4 total neighbors (i.e. is sp3). For heteroatoms in the ring
    (O, N, S) we accept any valence consistent with sp3.
    """
    from ase.neighborlist import neighbor_list

    n = len(atoms)
    symbols = atoms.get_chemical_symbols()
    # Element-pair cutoffs (covalent bond max distances).  NeighborList's
    # per-atom natural_cutoffs occasionally overcounts diagonal contacts in
    # small rings (cyclobutane's 2.1 Å C..C diagonals), so we use explicit
    # pair thresholds.
    pair_cut = {
        ("C", "C"): 1.70, ("C", "N"): 1.65, ("C", "O"): 1.65,
        ("C", "S"): 1.95, ("C", "P"): 1.95, ("C", "H"): 1.25,
        ("N", "N"): 1.55, ("N", "O"): 1.55, ("N", "H"): 1.20,
        ("O", "O"): 1.55, ("O", "H"): 1.20,
        ("S", "S"): 2.20, ("S", "H"): 1.45,
        ("P", "P"): 2.30, ("P", "H"): 1.50,
        ("H", "H"): 0.0,
    }
    # symmetric fill
    pair_cut.update({(b, a): v for (a, b), v in list(pair_cut.items())})
    pairs_i, pairs_j = neighbor_list("ij", atoms, cutoff=pair_cut)
    neighbors: List[set] = [set() for _ in range(n)]
    for a_, b_ in zip(pairs_i.tolist(), pairs_j.tolist()):
        neighbors[a_].add(b_)
    neighbors = [sorted(s) for s in neighbors]

    # Atoms that are heavy (non-H) and "puckerable" — sp3-compatible.
    # Heuristic: carbon with exactly 4 neighbors (sp3); O/N/S in ring; skip
    # everything sp2-ish (carbonyl carbons typically have 3 neighbors).
    def is_puckerable(i: int) -> bool:
        sym = symbols[i]
        if sym == "H":
            return False
        if sym == "C":
            return len(neighbors[i]) == 4
        return sym in ("O", "N", "S", "P")

    rings_found: List[Tuple[Tuple[int, ...], List[int]]] = []
    seen_keys = set()

    def dfs(start: int, current: int, depth: int, path: List[int], visited: set):
        if depth > max_size:
            return
        for nb in neighbors[current]:
            if symbols[nb] == "H":
                continue
            if nb == start and depth >= min_size:
                key = tuple(sorted(path))
                if key not in seen_keys:
                    seen_keys.add(key)
                    rings_found.append((key, list(path)))
                continue
            if nb in visited:
                continue
            if depth + 1 > max_size:
                continue
            visited.add(nb)
            path.append(nb)
            dfs(start, nb, depth + 1, path, visited)
            path.pop()
            visited.remove(nb)

    for i in range(n):
        if symbols[i] == "H":
            continue
        visited = {i}
        dfs(i, i, 1, [i], visited)

    # Filter to "puckerable" rings only.
    rings: List[Dict[str, Any]] = []
    for _, path in rings_found:
        if not all(is_puckerable(k) for k in path):
            continue
        rings.append({"size": len(path), "atoms": path})

    # Smallest-set-of-smallest-rings-ish: drop any ring whose atom set is the
    # symmetric difference of two smaller rings (fused-ring envelope).
    rings.sort(key=lambda r: r["size"])
    minimal: List[Dict[str, Any]] = []
    seen_atom_sets: List[set] = []
    for r in rings:
        atoms_set = set(r["atoms"])
        # Skip if this ring is the union of two already-accepted smaller rings.
        is_compound = False
        for i, s1 in enumerate(seen_atom_sets):
            for s2 in seen_atom_sets[i + 1:]:
                if s1.union(s2) == atoms_set and s1.intersection(s2):
                    is_compound = True
                    break
            if is_compound:
                break
        if is_compound:
            continue
        minimal.append(r)
        seen_atom_sets.append(atoms_set)
    return minimal


def _detect_rotatable_bonds(atoms, include_methyl: bool = False) -> List[Dict[str, Any]]:
    """Identify rotatable single bonds (non-ring C-C bonds).

    By default methyl-terminated bonds are skipped because their 3-fold
    symmetric rotation generates no distinct conformers — useful for
    conformer_search. Pass include_methyl=True (e.g. from `scan`) to keep
    them, since the rotation barrier itself is a legitimate observable.

    Returns a list of dicts with keys:
      a, b           — bond endpoint indices (a<b)
      side_b         — indices to rotate when twisting about (a,b)
      i, l           — reference atoms for the dihedral i-a-b-l (heavy if possible)
    """
    from ase.neighborlist import NeighborList, natural_cutoffs

    n = len(atoms)
    cutoffs = natural_cutoffs(atoms, mult=1.15)
    nl = NeighborList(cutoffs, self_interaction=False, bothways=True)
    nl.update(atoms)
    neighbors = [set(nl.get_neighbors(i)[0].tolist()) for i in range(n)]
    symbols = atoms.get_chemical_symbols()

    bonds: List[Dict[str, Any]] = []
    for a in range(n):
        for b in neighbors[a]:
            if b <= a:
                continue
            if symbols[a] != "C" or symbols[b] != "C":
                continue
            if not include_methyl:
                if _is_methyl_end(neighbors, symbols, a, b):
                    continue
                if _is_methyl_end(neighbors, symbols, b, a):
                    continue
            if _bond_in_ring(neighbors, a, b, n):
                continue
            side_b = _component_excluding(neighbors, start=b, blocked=a, n=n)
            if a in side_b or len(side_b) == n:
                continue
            # pick reference atoms — prefer heavy neighbors for clean dihedrals
            def pick(end, exclude):
                cands = neighbors[end] - {exclude}
                heavy = [k for k in cands if symbols[k] != "H"]
                if heavy:
                    return heavy[0]
                return next(iter(cands)) if cands else None
            i_ref = pick(a, b)
            l_ref = pick(b, a)
            if i_ref is None or l_ref is None:
                continue
            bonds.append({
                "a": a, "b": b,
                "side_b": list(side_b),
                "i": i_ref, "l": l_ref,
            })
    return bonds


# Cremer-Pople pucker amplitudes (Å) tuned per ring size from known equilibrium
# values: cyclopentane Q≈0.42, cyclohexane Q≈0.55, cycloheptane Q≈0.65,
# cyclooctane Q≈0.75. Cyclobutane folds with ~0.14 Å out-of-plane displacement.
_RING_PUCKER_AMPLITUDE = {
    4: 0.20,
    5: 0.42,
    6: 0.55,
    7: 0.65,
    8: 0.75,
}


def _cp_pucker_targets(ring_size: int) -> List[Dict[str, Any]]:
    """Canonical Cremer-Pople (q_k, phase) targets per ring size.

    Returns a list of {"label": str, "q": [..]} entries where q is the array
    of pucker amplitudes in the CP basis (length N-3 for an N-membered ring,
    expressed as (q_2 cos φ_2, q_2 sin φ_2, ..., q_N/2) for even N or as
    (q_2 cos φ_2, q_2 sin φ_2, ...) for odd N).

    For practical generation we keep this simple: enumerate a small canonical
    set per ring size matching the well-known minima/saddles.
    """
    A = _RING_PUCKER_AMPLITUDE[ring_size]
    if ring_size == 4:
        # Planar saddle + puckered up/down butterfly
        return [
            {"label": "planar",     "q2": 0.0,  "phi2": 0.0, "q3": 0.0,    "qN2":  0.0},
            {"label": "pucker_up",  "q2": 0.0,  "phi2": 0.0, "q3": 0.0,    "qN2":  A},
            {"label": "pucker_dn",  "q2": 0.0,  "phi2": 0.0, "q3": 0.0,    "qN2": -A},
        ]
    if ring_size == 5:
        # Envelope (E1..E5) at 5 phases + twist (T1..T5) at offset phases.
        # 10 total puckers; subsample to 6 (every other).
        out = []
        for k in range(5):
            phi = 2 * np.pi * k / 5
            out.append({"label": f"envelope_{k}", "q2": A, "phi2": phi})
        for k in range(5):
            phi = 2 * np.pi * k / 5 + np.pi / 5
            out.append({"label": f"twist_{k}", "q2": A, "phi2": phi})
        return out
    if ring_size == 6:
        # CP for 6-ring: (Q, θ, φ) with θ=0 → chair (north pole),
        # θ=180 → inverted chair, θ=90 → equator (6 boat/twist-boat positions).
        out = [
            {"label": "chair",       "q2": 0.0, "phi2": 0.0,         "qN2":  A},  # θ=0
            {"label": "inv_chair",   "q2": 0.0, "phi2": 0.0,         "qN2": -A},  # θ=180
        ]
        # Equator: 6 twist-boat/boat positions at φ = 0, 30, 60, 90, 120, 150°
        # (alternating TB and B every 30°). Sample 4 of them.
        for k, phi_deg in enumerate([30, 90, 150, 210]):
            phi = np.deg2rad(phi_deg)
            out.append({
                "label": f"twist_boat_{k}",
                "q2": A, "phi2": phi, "qN2": 0.0,
            })
        return out
    if ring_size == 7:
        # CP for 7-ring: 2 puckering coords (q_2, q_3) each with phase.
        # Canonical minima: TC (twist-chair, 14 equiv), C (chair),
        # TB (twist-boat), B (boat). Generate by setting q_2 dominant for
        # boat/twist-boat and q_3 dominant for chair/twist-chair.
        out = []
        for k, phi_deg in enumerate([0, 60, 120, 180]):
            phi = np.deg2rad(phi_deg)
            # Chair/twist-chair family (q3 dominant)
            out.append({
                "label": f"chair_{k}",
                "q2": 0.3 * A, "phi2": phi, "q3": 0.9 * A, "phi3": phi,
            })
        for k, phi_deg in enumerate([0, 90, 180]):
            phi = np.deg2rad(phi_deg)
            # Boat/twist-boat family (q2 dominant)
            out.append({
                "label": f"boat_{k}",
                "q2": 0.9 * A, "phi2": phi, "q3": 0.2 * A, "phi3": phi,
            })
        return out
    if ring_size == 8:
        # CP for 8-ring: 3 puckering coords (q_2, q_3, q_4 -- last is q_{N/2}).
        # Known stable: crown (D4d, q_2≈0, q_4≈A), BC (boat-chair, mixed),
        # TBC, TCC. Use a small canonical set.
        out = [
            {"label": "crown",      "q2": 0.0, "phi2": 0.0, "q3": 0.0,    "phi3": 0.0,         "qN2":  A},
            {"label": "anti_crown", "q2": 0.0, "phi2": 0.0, "q3": 0.0,    "phi3": 0.0,         "qN2": -A},
            {"label": "BC_1",       "q2": A,   "phi2": 0.0, "q3": 0.5*A,  "phi3": np.pi/4,     "qN2": 0.3*A},
            {"label": "BC_2",       "q2": A,   "phi2": np.pi/2, "q3": 0.5*A, "phi3": 3*np.pi/4, "qN2": 0.3*A},
            {"label": "TBC",        "q2": 0.7*A, "phi2": np.pi/4, "q3": 0.7*A, "phi3": np.pi/2, "qN2": 0.0},
        ]
        return out
    return []


def _cp_z_displacements(ring_size: int, target: Dict[str, Any]) -> np.ndarray:
    """Given a CP target dict, compute the out-of-plane z-displacements for
    each ring atom (length=ring_size), in the local ring frame (z = normal).

    Implements the inverse Cremer-Pople transformation:

        z_j = sqrt(2/N) Σ_{m=2}^{N/2-1} q_m cos[ 2π m (j-1)/N - φ_m ]
            + (1/sqrt(N)) (-1)^(j-1) q_{N/2}     [only when N is even]

    Indices j run 1..N. We map the target's q_m, phi_m entries to this sum.
    """
    N = ring_size
    z = np.zeros(N)
    # m = 2 mode (always present)
    q2 = target.get("q2", 0.0)
    phi2 = target.get("phi2", 0.0)
    # m = 3 mode (present for N>=6, odd N includes N/2 mode too)
    q3 = target.get("q3", 0.0)
    phi3 = target.get("phi3", 0.0)
    # N/2 mode for even N
    qN2 = target.get("qN2", 0.0)

    for j in range(1, N + 1):
        # m=2 term
        z[j - 1] += np.sqrt(2.0 / N) * q2 * np.cos(2 * np.pi * 2 * (j - 1) / N - phi2)
        # m=3 term (only matters for N >= 6)
        if N >= 6:
            z[j - 1] += np.sqrt(2.0 / N) * q3 * np.cos(2 * np.pi * 3 * (j - 1) / N - phi3)
        # m=N/2 term for even N
        if N % 2 == 0:
            sign = (-1) ** (j - 1)
            z[j - 1] += (1.0 / np.sqrt(N)) * sign * qN2

    # Re-center: CP coordinates are defined with Σ z_j = 0
    z -= z.mean()
    return z


def _xtb_constrained_ring_relax(
    atoms,
    ring_atoms: List[int],
    target_dihedrals_deg: List[float],
    workdir: str,
    label: str,
) -> Optional[Any]:
    """Constrain ring dihedrals at target values and run a brief xtb opt.

    This is the critical step for ring-pucker seeds: starting CP geometries
    have the ring carbons in the right z-pattern but substituent H positions
    are still chair-like, and an unconstrained opt would fall straight back
    to chair. Holding the ring dihedrals while H's relax produces a seed
    that lives in the target pucker's basin of attraction.

    Returns the constrained-optimized Atoms object, or None if xtb fails.
    """
    xtb_exe = shutil.which("xtb")
    if xtb_exe is None:
        return None
    sub = os.path.join(workdir, f"cpseed_{label}")
    os.makedirs(sub, exist_ok=True)
    seed_xyz = os.path.join(sub, "seed.xyz")
    ase_write(seed_xyz, atoms, format="xyz")
    xc_inp = os.path.join(sub, "xc.inp")
    with open(xc_inp, "w") as f:
        f.write("$constrain\n  force constant=1.0\n")
        N = len(ring_atoms)
        for i in range(N):
            i1 = ring_atoms[i] + 1
            i2 = ring_atoms[(i + 1) % N] + 1
            i3 = ring_atoms[(i + 2) % N] + 1
            i4 = ring_atoms[(i + 3) % N] + 1
            f.write(f"  dihedral: {i1},{i2},{i3},{i4}, {target_dihedrals_deg[i]:.2f}\n")
        f.write("$opt\n  maxcycle=80\n$end\n")
    try:
        subprocess.run(
            [xtb_exe, "seed.xyz", "--opt", "--input", "xc.inp", "--gfn", "2"],
            cwd=sub, capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return None
    opt_xyz = os.path.join(sub, "xtbopt.xyz")
    if not os.path.isfile(opt_xyz):
        return None
    try:
        return ase_read(opt_xyz)
    except Exception:
        return None


def _ring_dihedrals(atoms, ring_atoms: List[int]) -> List[float]:
    """Measure the ring dihedrals (one per ring atom)."""
    N = len(ring_atoms)
    out = []
    for i in range(N):
        a0 = ring_atoms[i]
        a1 = ring_atoms[(i + 1) % N]
        a2 = ring_atoms[(i + 2) % N]
        a3 = ring_atoms[(i + 3) % N]
        try:
            d = atoms.get_dihedral(a0, a1, a2, a3)
            if d > 180.0:
                d -= 360.0
            out.append(float(d))
        except Exception:
            out.append(0.0)
    return out


def _ring_pucker_seeds(
    atoms,
    ring: Dict[str, Any],
    *,
    max_per_ring: int = 8,
    workdir: Optional[str] = None,
) -> List[Tuple[str, Any]]:
    """Generate puckered conformer seeds for one ring.

    For each canonical CP target:
      1. Define the local ring frame (origin = ring centroid, z = normal).
      2. Map current ring atoms to that frame.
      3. Replace the z-coordinates with the CP target's z-displacements.
      4. Translate each ring atom by the (new - old) z displacement, in
         world coordinates. Substituent atoms (bonded to ring atoms) follow
         their ring atom rigidly (translate, don't rotate) — this preserves
         local bond lengths well enough that PM7/xtb relaxation recovers a
         clean minimum.
    Returns a list of (label, atoms_copy) tuples.
    """
    ring_atoms = list(ring["atoms"])
    N = len(ring_atoms)
    if N not in _RING_PUCKER_AMPLITUDE:
        return []

    pos = atoms.get_positions()
    ring_pos = pos[ring_atoms]
    centroid = ring_pos.mean(axis=0)

    # Local frame: z = best-fit ring normal via SVD on centered coords
    centered = ring_pos - centroid
    _, _, Vt = np.linalg.svd(centered, full_matrices=False)
    z_axis = Vt[2]

    # Compute current z (in local frame) for each ring atom
    local_z_current = centered @ z_axis  # length N

    # For each substituent (non-ring neighbor of a ring atom), record its
    # parent ring atom — we'll translate substituents along with their parent.
    from ase.neighborlist import NeighborList, natural_cutoffs
    cutoffs = natural_cutoffs(atoms, mult=1.15)
    nl = NeighborList(cutoffs, self_interaction=False, bothways=True)
    nl.update(atoms)
    ring_set = set(ring_atoms)
    substituent_parent: Dict[int, int] = {}
    # Walk outward from each ring atom: every connected non-ring atom inherits
    # the parent's z-shift.
    for ra in ring_atoms:
        stack = [(ra, ra)]
        seen = set([ra])
        while stack:
            parent, current = stack.pop()
            for nb in nl.get_neighbors(current)[0].tolist():
                if nb in seen or nb in ring_set:
                    continue
                seen.add(nb)
                substituent_parent[nb] = ra
                stack.append((ra, nb))

    targets = _cp_pucker_targets(N)[:max_per_ring]
    seeds: List[Tuple[str, Any]] = []
    from ..calculators import register_auto_tempdir
    seed_workdir = workdir or register_auto_tempdir(
        tempfile.mkdtemp(prefix="chemkit_cpseed_"))
    for target in targets:
        z_new = _cp_z_displacements(N, target)
        # Build dz array for each atom
        dz = np.zeros(len(atoms))
        for j, ra in enumerate(ring_atoms):
            dz[ra] = z_new[j] - local_z_current[j]
        for sub, parent in substituent_parent.items():
            j = ring_atoms.index(parent)
            dz[sub] = z_new[j] - local_z_current[j]

        cand = atoms.copy()
        new_pos = cand.get_positions().copy()
        for idx in range(len(atoms)):
            if dz[idx] != 0.0:
                new_pos[idx] = new_pos[idx] + dz[idx] * z_axis
        cand.set_positions(new_pos)

        # Critical: constrained-relax the ring at its CP target dihedrals so
        # substituent H's settle into the pucker before unconstrained optimization
        # rolls everything back to chair.
        seed_dihs = _ring_dihedrals(cand, ring_atoms)
        relaxed = _xtb_constrained_ring_relax(
            cand, ring_atoms, seed_dihs, seed_workdir, target["label"],
        )
        if relaxed is not None:
            seeds.append((target["label"], relaxed))
        else:
            # Fall back to unrelaxed seed if xtb missing; postopt may still recover.
            seeds.append((target["label"], cand))
    return seeds


def _is_eclipsed_saddle(atoms, rotatable_bonds: List[Dict[str, Any]],
                        tol_deg: float = 18.0) -> bool:
    """True if any backbone dihedral is within tol_deg of an eclipsed value
    (0/120/240 mod 360). MOPAC's EF optimizer occasionally terminates at such
    saddles when fed symmetric input; reject those.
    """
    for bond in rotatable_bonds:
        try:
            phi = atoms.get_dihedral(bond["i"], bond["a"], bond["b"], bond["l"])
        except Exception:
            continue
        phi = phi % 360.0
        # eclipsed centers depend on the substituent count; for an sp3-sp3 bond
        # with three substituents per end, eclipsed = where syn-substituents
        # align, i.e. 0/120/240. Staggered minima = 60/180/300.
        for ecl in (0.0, 120.0, 240.0, 360.0):
            if abs(phi - ecl) < tol_deg:
                return True
    return False


def _gather_postopt_seeds(
    *, workdir: str, base_conformers: list, max_seeds: int,
    rotatable_bonds: Optional[List[Dict[str, Any]]] = None,
    rings: Optional[List[Dict[str, Any]]] = None,
    base_atoms: Optional[Any] = None,
) -> Tuple[list, str]:
    """Pick a diverse seed pool for post-optimization.

    Strategy:
      - Always add ring-pucker seeds (chair/twist-boat/etc.) for any detected
        ring. Force-field conformer sampling smooths over shallow ring
        conformers (e.g. cyclohexane's twist-boat at +5.5 kcal/mol); seeding
        the puckers explicitly is the only way to recover them.
      - If the sampler returned multiple conformers, use those plus ring seeds.
      - Otherwise also build seeds by rotating each rotatable single bond
        through {60°, 180°, 300°}.
    """
    parts: List[Any] = []
    sources: List[str] = []

    # Ring-pucker seeds — always generated when rings are detected.
    ring_seeds_added = 0
    if rings and base_atoms is not None:
        for ring in rings:
            try:
                ring_seeds = _ring_pucker_seeds(
                    base_atoms, ring, max_per_ring=8, workdir=workdir,
                )
            except Exception:
                ring_seeds = []
            for label, seed_atoms in ring_seeds:
                if len(parts) >= max_seeds:
                    break
                parts.append(seed_atoms)
                ring_seeds_added += 1
        if ring_seeds_added:
            sources.append(f"ring_pucker ({ring_seeds_added})")

    if len(base_conformers) > 1:
        room = max_seeds - len(parts)
        parts.extend(list(base_conformers[:room]))
        sources.insert(0, f"obabel_conformers ({min(len(base_conformers), room)})")
        return parts[:max_seeds], " + ".join(sources)

    if base_conformers:
        parts.insert(0, base_conformers[0])
        sources.insert(0, "obabel_best")

    base = base_conformers[0] if base_conformers else None
    if base is not None and len(parts) < max_seeds:
        try:
            rotated = _dihedral_grid_seeds(
                base, max_seeds=max_seeds - len(parts),
                rotatable_bonds=rotatable_bonds,
            )
        except Exception:
            rotated = []
        if rotated:
            parts.extend(rotated)
            sources.append(f"dihedral_grid ({len(rotated)})")

    if not sources:
        sources.append("obabel_conformers (single)")
    return parts[:max_seeds], " + ".join(sources)


def _dihedral_grid_seeds(
    atoms, *, max_seeds: int,
    rotatable_bonds: Optional[List[Dict[str, Any]]] = None,
) -> List:
    """Enumerate seeds by rotating each rotatable single bond through
    {gauche+, anti, gauche-}, with a small asymmetric offset so seeds don't
    sit exactly on saddle geometries.
    """
    bonds = rotatable_bonds if rotatable_bonds is not None else _detect_rotatable_bonds(atoms)
    if not bonds:
        return []

    angles_deg = [62.0, 178.0, 298.0]
    seeds = [atoms.copy()]
    pass_offset = 0.0
    for bond in bonds:
        a, b, side = bond["a"], bond["b"], bond["side_b"]
        new_seeds = []
        for s in seeds:
            for k, ang in enumerate(angles_deg):
                cand = s.copy()
                _set_dihedral_about_bond(
                    cand, a, b, side,
                    ang + pass_offset + 0.7 * (k - 1),
                )
                new_seeds.append(cand)
                if len(new_seeds) + (len(seeds) - 1) * 3 >= max_seeds * 3:
                    break
        seeds = new_seeds
        pass_offset += 1.3
        if len(seeds) >= max_seeds:
            seeds = seeds[:max_seeds]
            break
    return seeds[:max_seeds]


def _is_methyl_end(neighbors: List[set], symbols: List[str], end: int, other_end: int) -> bool:
    """True if `end` is a methyl carbon (only H neighbors besides other_end)."""
    others = neighbors[end] - {other_end}
    if not others:
        return True  # bare atom — nothing to rotate, treat as trivial
    return all(symbols[k] == "H" for k in others)


def _bond_in_ring(neighbors: List[set], i: int, j: int, n: int) -> bool:
    """True if removing edge (i,j) still leaves i and j connected (i.e. ring)."""
    visited = {i}
    stack = [i]
    while stack:
        u = stack.pop()
        for v in neighbors[u]:
            if u == i and v == j:
                continue
            if u == j and v == i:
                continue
            if v in visited:
                continue
            if v == j:
                return True
            visited.add(v)
            stack.append(v)
    return False


def _component_excluding(neighbors: List[set], *, start: int, blocked: int, n: int) -> set:
    visited = {start}
    stack = [start]
    while stack:
        u = stack.pop()
        for v in neighbors[u]:
            if v == blocked and u == start:
                continue
            if v in visited:
                continue
            visited.add(v)
            stack.append(v)
    return visited


def _set_dihedral_about_bond(atoms, a: int, b: int, side_indices: List[int], angle_deg: float):
    """Rotate the side_indices atoms about axis (a -> b) by angle_deg.

    NOTE: this sets the rotation angle relative to the *current* geometry, not
    to a specific dihedral measurement. That's fine for seed generation —
    optimization will relax to the nearest minimum regardless of exact phase.
    """
    pos = atoms.get_positions()
    axis = pos[b] - pos[a]
    axis /= np.linalg.norm(axis) + 1e-12
    theta = np.deg2rad(angle_deg)
    c, s = np.cos(theta), np.sin(theta)
    K = np.array([[0, -axis[2], axis[1]],
                  [axis[2], 0, -axis[0]],
                  [-axis[1], axis[0], 0]])
    R = np.eye(3) + s * K + (1 - c) * (K @ K)
    origin = pos[a]
    for idx in side_indices:
        pos[idx] = origin + R @ (pos[idx] - origin)
    atoms.set_positions(pos)


def _postopt_mopac(
    *, conformers, workdir, charge, multiplicity, solvent,
    rmsd_threshold, ewin_kcal,
    rotatable_bonds: Optional[List[Dict[str, Any]]] = None,
):
    """Re-optimize each seed conformer with PM7 (native EF), then dedup."""
    from .opt import _run_mopac

    post_dir = os.path.join(workdir, "postopt_mopac")
    os.makedirs(post_dir, exist_ok=True)

    # Deterministic per-seed Cartesian jitter to break input symmetry so the
    # EF optimizer doesn't terminate at a saddle. 0.05 Å is well above MOPAC's
    # convergence noise so saddles get rolled off, but well below typical
    # basin-of-attraction radii so it doesn't relocate true minima.
    rng = np.random.default_rng(0xC0FFEE)

    optimized: List[Dict[str, Any]] = []
    failures = 0
    saddles_rejected = 0
    for idx, conf in enumerate(conformers):
        seed_xyz = os.path.join(post_dir, f"seed_{idx:03d}.xyz")
        out_xyz = os.path.join(post_dir, f"conf_{idx:03d}_mopac_opt.xyz")
        jittered = conf.copy()
        jittered.set_positions(
            jittered.get_positions()
            + rng.normal(scale=0.02, size=jittered.get_positions().shape)
        )
        ase_write(seed_xyz, jittered, format="xyz")
        try:
            res = _run_mopac(
                input_path=seed_xyz,
                atoms=jittered,
                symbols=jittered.get_chemical_symbols(),
                charge=charge,
                multiplicity=multiplicity,
                solvent=solvent,
                fmax=0.05,
                steps=500,
                out_xyz=out_xyz,
                cli="(internal post-opt)",
            )
        except Exception:
            failures += 1
            continue
        hof = res.get("final_heat_of_formation_kcal_mol")
        if hof is None:
            failures += 1
            continue
        optimized_atoms = ase_read(out_xyz)
        # Reject geometries stuck at eclipsed saddles — the EF optimizer
        # sometimes converges there from highly symmetric inputs.
        if rotatable_bonds and _is_eclipsed_saddle(optimized_atoms, rotatable_bonds):
            saddles_rejected += 1
            continue
        heavy_mask = np.array(
            [s != "H" for s in optimized_atoms.get_chemical_symbols()],
            dtype=bool,
        )
        optimized.append({
            "source_index": idx,
            "hof_kcal_mol": float(hof),
            "atoms": optimized_atoms,
            "heavy_positions": optimized_atoms.get_positions()[heavy_mask],
            "xyz_path": out_xyz,
            "converged": bool(res.get("converged", False)),
        })

    if not optimized:
        return {
            "method": "PM7 (MOPAC)",
            "n_input": len(conformers),
            "n_converged": 0,
            "n_unique": 0,
            "n_failed": failures,
            "conformers": [],
            "note": "All post-optimizations failed.",
        }

    # Sort by HoF, dedup by RMSD-after-Kabsch and energy proximity.
    optimized.sort(key=lambda d: d["hof_kcal_mol"])
    lowest = optimized[0]["hof_kcal_mol"]

    unique: List[Dict[str, Any]] = []
    for cand in optimized:
        if cand["hof_kcal_mol"] - lowest > ewin_kcal:
            continue
        is_dup = False
        for u in unique:
            if _rmsd_kabsch(cand["heavy_positions"],
                            u["heavy_positions"]) < rmsd_threshold:
                u["degeneracy"] += 1
                is_dup = True
                break
        if not is_dup:
            cand["degeneracy"] = 1
            unique.append(cand)

    ensemble_xyz = os.path.join(post_dir, "postopt_ensemble.xyz")
    with open(ensemble_xyz, "w") as f:
        for u in unique:
            with open(u["xyz_path"]) as g:
                content = g.read().rstrip()
            f.write(content + "\n")

    return {
        "method": "PM7 (MOPAC)",
        "n_input": len(conformers),
        "n_converged": len(optimized),
        "n_unique": len(unique),
        "n_failed": failures,
        "n_saddles_rejected": saddles_rejected,
        "rmsd_threshold_A": rmsd_threshold,
        "ewin_kcal_mol": ewin_kcal,
        "lowest_hof_kcal_mol": lowest,
        "ensemble_xyz": ensemble_xyz,
        "best_xyz": unique[0]["xyz_path"] if unique else None,
        "conformers": [
            {
                "source_index": u["source_index"],
                "hof_kcal_mol": u["hof_kcal_mol"],
                "rel_hof_kcal_mol": u["hof_kcal_mol"] - lowest,
                "degeneracy": u["degeneracy"],
                "xyz_path": u["xyz_path"],
                "converged": u["converged"],
            }
            for u in unique
        ],
    }


def _rmsd_kabsch(A: np.ndarray, B: np.ndarray) -> float:
    """Heavy-atom-agnostic RMSD after centroid alignment + Kabsch rotation."""
    A = np.asarray(A, dtype=float)
    B = np.asarray(B, dtype=float)
    if A.shape != B.shape:
        return float("inf")
    A_c = A - A.mean(axis=0)
    B_c = B - B.mean(axis=0)
    H = A_c.T @ B_c
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    A_rot = A_c @ R.T
    return float(np.sqrt(np.mean(np.sum((A_rot - B_c) ** 2, axis=1))))
