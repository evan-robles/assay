#!/usr/bin/env python3
"""Fetch 3D geometries from PubChem for the fidelity spec folders.

For each spec under benchmarks/fidelity/*-validation/<mol>/ that references an
xyz which doesn't yet exist, resolve the molecule name to a PubChem CID, download
the 3D SDF, convert to .xyz, and write a SOURCES.md (DB, CID, URL, accessed date)
plus a provenance comment on the xyz title line — per research-standards.

Molecules that PubChem cannot supply a clean 3D record for (transition states,
the glycine ZWITTERION specifically, organometallics, etc.) are SKIPPED and
listed at the end for the user to supply.

Usage:
    # Env: anl_env
    python benchmarks/fetch_geometries.py            # fetch all missing
    python benchmarks/fetch_geometries.py --dry-run  # just report what would happen
"""
from __future__ import annotations
import argparse, glob, json, os, subprocess, sys, urllib.parse, urllib.request
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# folder-slug -> PubChem query name (when they differ)
NAME_MAP = {
    "12-dichloroethane": "1,2-dichloroethane",
    "carbon-dioxide": "carbon dioxide",
    "carbon-monoxide": "carbon monoxide",
    "hydrogen-peroxide": "hydrogen peroxide",
    "ethylene-glycol": "ethylene glycol",
    "propylene-glycol": "propylene glycol",
    "diethyl-ether": "diethyl ether",
    "alanine-dipeptide": "alanine dipeptide",
    "acetic-acid": "acetic acid",
    "1-butanol": "1-butanol",
    "tempo": "TEMPO",
    "oxygen": "oxygen",
    "acetate": "acetate",
    # redox-potential suite
    "methyl-viologen": "methyl viologen",
    "tetracyanoethylene": "tetracyanoethylene",
    # conformational-analysis suite
    "12-difluoroethane": "1,2-difluoroethane",
    # pka-acidity suite (HA = the folder's acid name)
    "methylammonium": "methylamine",  # protonated form built from methylamine
    "hydrogen-cyanide": "hydrogen cyanide",
    "trifluoroacetic-acid": "trifluoroacetic acid",
    "benzoic-acid": "benzoic acid",
    "formic-acid": "formic acid",
}

# folders PubChem can't give a usable 3D record for -> user must supply.
# NOTE: the per-file logic (FLAG_FILE_PREFIXES) flags derived geometry FILES
# (TS guesses, A- forms, complexes) inside multi-input folders. This SKIP set is
# for whole single-xyz folders whose positional xyz PubChem can't supply.
SKIP = {
    "ammonia-planar-ts": "transition-state geometry (no DB record)",
    "glycine-zwitterion": "PubChem gives NEUTRAL glycine, not the zwitterion",
    "ferrocene": "organometallic; PubChem 3D unreliable for Fe sandwich",
    # transition-state + IRC single-xyz suites: every geometry is a TS guess/saddle.
    "ammonia-inversion": "transition-state/saddle geometry (no DB record)",
    "ethane-rotation": "transition-state/saddle geometry (no DB record)",
    "hcn-isomerization": "transition-state/saddle geometry (no DB record)",
    "methanol-torsion": "transition-state/saddle geometry (no DB record)",
    "hydrogen-peroxide-torsion": "transition-state/saddle geometry (no DB record)",
    "formamide-rotation": "transition-state/saddle geometry (no DB record)",
    "formic-acid-ze": "transition-state/saddle geometry (no DB record)",
    "sn2-chloride": "charged transition-state geometry (no DB record)",
    # redox edge case: organic-acceptor radical anion geometry is fine to fetch
    # neutral; keep tetracyanoethylene fetchable. (no skip)
}


def _http(url: str, timeout: int = 30) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status == 200:
                return r.read()
    except Exception as e:  # noqa: BLE001
        print(f"    http error: {e}")
    return None


def cid_for(name: str) -> str | None:
    q = urllib.parse.quote(name)
    data = _http(f"{PUBCHEM}/compound/name/{q}/cids/TXT")
    if not data:
        return None
    cid = data.decode().split()[0].strip()
    return cid or None


def sdf3d_for(cid: str) -> bytes | None:
    return _http(f"{PUBCHEM}/compound/cid/{cid}/SDF?record_type=3d")


def sdf_to_xyz(sdf: bytes, title: str) -> str | None:
    """Convert an SDF (bytes) to xyz via obabel."""
    proc = subprocess.run(["obabel", "-isdf", "-oxyz", "--title", title],
                          input=sdf, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout.decode()


# --------------------------------------------------------------------------- #
# Per-FILE handling for multi-input skills.
#
# A spec may reference geometries beyond the single positional `xyz`: an `inputs`
# list with monomers (binding-energy), species specs (reaction-energy), HA/A-
# (pka), and reactant/product/ts-guess (reaction-profile). We fetch the ones that
# are plain single-molecule PubChem lookups and FLAG the derived ones.
#
# Each target carries a `key` used to look up its PubChem query name (FILE_NAME_MAP)
# and to decide whether it is derived/flagged (FLAG_PATTERNS). The key is the xyz
# basename without extension (e.g. 'h2', 'o2', 'monomer_1_water', 'complex',
# 'ha', 'a_minus', 'ts_guess').
# --------------------------------------------------------------------------- #

# Geometry FILES that are DERIVED and cannot be a clean PubChem lookup -> flagged
# for the user to supply manually. Matched against the basename (no extension).
FLAG_FILE_PREFIXES = {
    "complex": "dimer/complex geometry (assemble the two monomers; not a single PubChem record)",
    "a_minus": "deprotonated A- form (build via build-from-smiles anion SMILES, or delete the acidic proton)",
    "ts_guess": "transition-state guess geometry (construct by hand or from a scan's energy-max frame)",
}

# Reaction-energy species filenames -> PubChem query name (small molecules).
# pka HA filenames are just 'ha' -> use the folder slug's acid name.
FILE_NAME_MAP = {
    "h2": "hydrogen", "n2": "nitrogen", "o2": "oxygen", "co2": "carbon dioxide",
    "ch4": "methane", "h2o": "water", "nh3": "ammonia", "hcn": "hydrogen cyanide",
    "hnc": "hydrogen isocyanide", "ethane": "ethane", "ethylene": "ethylene",
    "butane": "butane", "isobutane": "isobutane", "ammonium": "ammonium",
    "acetate": "acetate", "acetic-acid": "acetic acid", "borane": "borane",
    "hydrogen-fluoride": "hydrogen fluoride", "formic-acid": "formic acid",
    "benzene": "benzene", "water": "water", "ammonia": "ammonia", "methane": "methane",
}


def _file_key(xyz_basename: str) -> str:
    """basename without extension, lowercased."""
    return os.path.splitext(os.path.basename(xyz_basename))[0].lower()


def _query_for_file(key: str, slug: str, is_positional: bool) -> str | None:
    """PubChem query name for a per-file target, or None if it should be flagged.
    For monomer files like 'monomer_1_water' the trailing token is the molecule.
    `is_positional` True means this is the spec's single positional xyz — a plain
    single-molecule lookup named by the folder slug (the original fetcher behavior)."""
    for pref, _reason in FLAG_FILE_PREFIXES.items():
        if key == pref or key.startswith(pref):
            return None  # flagged
    # The single positional xyz of a single-input suite (redox, conformational,
    # transition-state, IRC): named by the folder slug. Whole-folder SKIP is
    # applied by the caller; here just resolve the slug -> query name.
    if is_positional:
        return NAME_MAP.get(slug, slug.replace("-", " "))
    if key in FILE_NAME_MAP:
        return FILE_NAME_MAP[key]
    if key.startswith("monomer"):
        token = key.split("_")[-1]
        return FILE_NAME_MAP.get(token, token.replace("-", " "))
    if key == "ha":  # pka protonated form = the folder's acid
        return NAME_MAP.get(slug, slug.replace("-", " "))
    if key in ("reactant", "product"):
        return None  # reaction-profile R/P are reaction-specific; flag for manual
    return None


def folders_needing_xyz():
    """Yield (folder_path, slug, target_xyz, query_or_None) for every missing xyz
    a spec references — the single positional `xyz` AND each `inputs[].xyz`. A
    `query_or_None` of None means the file is derived and should be flagged."""
    out = []
    seen = set()
    for f in glob.glob(str(_REPO / "benchmarks/fidelity/*-validation/*/*.spec.json")):
        s = json.loads(Path(f).read_text())
        folder = os.path.dirname(f)
        slug = os.path.basename(folder)
        # 1) the single positional xyz (existing behavior)
        candidates = []  # (xyz, key, is_positional)
        if s.get("xyz"):
            candidates.append((s["xyz"], _file_key(s["xyz"]), True))
        # 2) multi-input geometry files (xyz entries only; species 'spec' strings
        #    carry their own paths and are handled by the reaction-energy author).
        for item in s.get("inputs", []) or []:
            if item.get("xyz"):
                candidates.append((item["xyz"], _file_key(item["xyz"]), False))
            elif item.get("spec"):
                # species spec: '[COEF*]PATH[,..]' — extract PATH
                sp = item["spec"]
                if "*" in sp.split(",", 1)[0]:
                    sp = sp.split("*", 1)[1]
                path = sp.split(",", 1)[0]
                candidates.append((path, _file_key(path), False))
        for xyz, key, is_positional in candidates:
            xyz_abs = _REPO / xyz if not os.path.isabs(xyz) else Path(xyz)
            if str(xyz_abs) in seen or xyz_abs.is_file():
                continue
            seen.add(str(xyz_abs))
            # Whole-folder SKIP applies only to the positional xyz of a single-input
            # suite (e.g. transition-state/IRC geometries flagged outright).
            if slug in SKIP and is_positional:
                query = None
            else:
                query = _query_for_file(key, slug, is_positional)
            out.append((folder, slug, str(xyz_abs), query))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = folders_needing_xyz()
    fetched, skipped, failed = [], [], []
    today = date.today().isoformat()

    for folder, slug, xyz_path, query in targets:
        fname = os.path.basename(xyz_path)
        label = f"{slug}/{fname}"
        # A None query means this geometry is derived (TS guess, A-, complex,
        # reaction R/P) or a whole-folder SKIP — flag it for the user.
        if query is None:
            key = _file_key(fname)
            reason = (SKIP.get(slug)
                      or next((r for p, r in FLAG_FILE_PREFIXES.items()
                               if key == p or key.startswith(p)), None)
                      or "derived geometry — supply manually")
            skipped.append((label, reason)); continue
        name = query
        print(f"[fetch] {label}  (PubChem name: {name!r})")
        if args.dry_run:
            continue
        cid = cid_for(name)
        if not cid:
            failed.append((slug, "no PubChem CID")); print("    no CID"); continue
        sdf = sdf3d_for(cid)
        if not sdf:
            failed.append((slug, f"no 3D SDF for CID {cid}")); print("    no 3D SDF"); continue
        url = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
        title = f"{name} | PubChem CID {cid} | {url} | accessed {today}"
        xyz = sdf_to_xyz(sdf, title)
        if not xyz:
            failed.append((slug, "obabel sdf->xyz failed")); print("    convert failed"); continue
        Path(xyz_path).write_text(xyz)
        # provenance file (ACS-style database citation)
        (Path(folder) / "SOURCES.md").write_text(
            f"# Geometry source\n\n"
            f"- Molecule: {name}\n"
            f"- Source: PubChem (3D conformer)\n"
            f"- PubChem CID: {cid}\n"
            f"- URL: {url}\n"
            f"- Accessed: {today}\n\n"
            f"National Center for Biotechnology Information. PubChem Compound "
            f"Summary for CID {cid}, {name}. {url} (accessed {today}).\n\n"
            f"Note: this is an unoptimized PubChem 3D conformer (a starting "
            f"geometry), not a structure optimized at the calculation's level of "
            f"theory.\n"
        )
        fetched.append((slug, cid)); print(f"    -> CID {cid}, wrote {os.path.basename(xyz_path)}")

    print("\n===== summary =====")
    print(f"fetched: {len(fetched)}")
    for s, c in fetched: print(f"  ok    {s} (CID {c})")
    print(f"skipped (user must supply): {len(skipped)}")
    for s, why in skipped: print(f"  skip  {s} — {why}")
    print(f"failed: {len(failed)}")
    for s, why in failed: print(f"  FAIL  {s} — {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
