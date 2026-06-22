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
}

# folders PubChem can't give a usable 3D record for -> user must supply.
SKIP = {
    "ammonia-planar-ts": "transition-state geometry (no DB record)",
    "glycine-zwitterion": "PubChem gives NEUTRAL glycine, not the zwitterion",
    "ferrocene": "organometallic; PubChem 3D unreliable for Fe sandwich",
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


def folders_needing_xyz():
    """Yield (folder_path, slug, target_xyz) for specs whose xyz is missing."""
    out = {}
    for f in glob.glob(str(_REPO / "benchmarks/fidelity/*-validation/*/*.spec.json")):
        s = json.loads(Path(f).read_text())
        xyz = s.get("xyz")
        if not xyz:
            continue
        xyz_abs = _REPO / xyz if not os.path.isabs(xyz) else Path(xyz)
        if xyz_abs.is_file():
            continue  # already have it
        folder = os.path.dirname(f)
        slug = os.path.basename(folder)
        out[slug] = (folder, slug, str(xyz_abs))
    return list(out.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = folders_needing_xyz()
    fetched, skipped, failed = [], [], []
    today = date.today().isoformat()

    for folder, slug, xyz_path in targets:
        if slug in SKIP:
            skipped.append((slug, SKIP[slug])); continue
        name = NAME_MAP.get(slug, slug.replace("-", " "))
        print(f"[fetch] {slug}  (PubChem name: {name!r})")
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
