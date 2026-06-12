"""Resolve a plain molecule *name* to a SMILES string from online databases.

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
