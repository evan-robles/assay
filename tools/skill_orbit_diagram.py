#!/usr/bin/env python3
"""Render the ASSAY/chemkit agent as a central hub orbited by its 20 skills.

Produces a high-quality "solar-system" diagram: the interactive agent sits at
the centre as a node, and every one of the 20 chemkit skills orbits it like a
planet. Skills are split into two orbits:

  * inner orbit  = PRIMITIVE skills (talk directly to a backend calculator)
  * outer orbit  = COMPOSITE skills (orchestrate primitives in-process)

Solid spokes  : the agent orchestrates every skill (agent -> skill).
Curved arcs   : composite -> primitive in-process composition (e.g. redox
                imports sp/opt/freq). These edges are the real call graph read
                from mcp_server/chemkit_engine/tasks/*.py.

The 20 skill names are the exact `TOOLS` registry from mcp_server/server.py.

Usage:
    # Env: any env with matplotlib + numpy
    python tools/skill_orbit_diagram.py [--out skill_orbit.png] [--light]

Requirements:
    - matplotlib, numpy
"""
from __future__ import annotations

import argparse
import math
from typing import Dict, List, Tuple

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from matplotlib.font_manager import FontProperties


# ---------------------------------------------------------------------------
# Ground-truth data (verified against the repo).
# ---------------------------------------------------------------------------
# The 20 skills, split by whether they touch a backend directly (primitive) or
# orchestrate other skills in-process (composite). Verified from tasks/*.py.
PRIMITIVES: List[str] = [
    "single-point-energy",
    "geometry-optimize",
    "vibrational-analysis",   # freq (also lightly composite: preopt + confsearch)
    "transition-state",
    "intrinsic-reaction-coordinate",
    "conformational-analysis",
    "electrostatics",
    "frontier-orbitals",
    "visualize-orbitals",
    "conformer-search",
    "build-from-smiles",
    "name-to-smiles",
]
COMPOSITES: List[str] = [
    "redox-potential",
    "reaction-profile",
    "reaction-energy",
    "pka-acidity",
    "logp-partition",
    "solvation",
    "binding-energy",
    "fukui-reactivity",
]

# Composite -> primitive skills it calls in-process (from tasks/*.py imports).
DEPENDS: Dict[str, List[str]] = {
    "redox-potential":  ["single-point-energy", "geometry-optimize", "vibrational-analysis"],
    "reaction-profile": ["geometry-optimize", "vibrational-analysis",
                         "transition-state", "intrinsic-reaction-coordinate"],
    "reaction-energy":  ["single-point-energy", "geometry-optimize", "vibrational-analysis"],
    "pka-acidity":      ["vibrational-analysis"],
    "logp-partition":   ["single-point-energy"],
    "solvation":        ["single-point-energy"],
    "binding-energy":   ["single-point-energy"],
    "fukui-reactivity": ["electrostatics"],
}

# Short human labels (kebab-case is long; wrap onto two lines nicely).
def _label(name: str) -> str:
    pretty = {
        "single-point-energy": "single-point\nenergy",
        "geometry-optimize": "geometry\noptimize",
        "vibrational-analysis": "vibrational\nanalysis",
        "transition-state": "transition\nstate",
        "intrinsic-reaction-coordinate": "intrinsic reaction\ncoordinate",
        "conformational-analysis": "conformational\nanalysis",
        "frontier-orbitals": "frontier\norbitals",
        "visualize-orbitals": "visualize\norbitals",
        "conformer-search": "conformer\nsearch",
        "build-from-smiles": "build from\nSMILES",
        "name-to-smiles": "name to\nSMILES",
        "redox-potential": "redox\npotential",
        "reaction-profile": "reaction\nprofile",
        "reaction-energy": "reaction\nenergy",
        "pka-acidity": "pKa\nacidity",
        "logp-partition": "logP\npartition",
        "binding-energy": "binding\nenergy",
        "fukui-reactivity": "fukui\nreactivity",
    }
    return pretty.get(name, name.replace("-", "\n", 1))


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
def palette(light: bool) -> dict:
    if light:
        return dict(
            bg="#ffffff", fg="#161b2e",
            hub="#3b4a8c", hub_glow="#93a2e8", hub_text="#ffffff",
            prim="#12876b", prim_ring="#8fd8c4",     # teal — primitives
            comp="#d1493b", comp_ring="#f0b3ab",     # warm red — composites
            spoke="#c4c8da", dep="#e08a4e", orbit="#e4e6f0",
            sub="#5a5f7a",
        )
    return dict(
        bg="#0b0e1a", fg="#e8ecf5",
        hub="#8ea2ff", hub_glow="#3a4a9e", hub_text="#0b0e1a",
        prim="#39d3a6", prim_ring="#1f6f57",         # teal — primitives
        comp="#ff7a6b", comp_ring="#7a2f28",         # warm coral — composites
        spoke="#2a3350", dep="#ffb26b", orbit="#1b2138",
        sub="#9aa3c7",
    )


def ring_positions(n: int, radius: float, phase: float) -> List[Tuple[float, float]]:
    """Evenly spaced points on a full circle of given radius, starting at `phase`."""
    return [
        (radius * math.cos(phase + 2 * math.pi * i / n),
         radius * math.sin(phase + 2 * math.pi * i / n))
        for i in range(n)
    ]


def arc_positions(n: int, radius: float, a0: float, a1: float) -> List[Tuple[float, float]]:
    """n points evenly spread over the angular arc [a0, a1] at `radius`.

    Used to keep each ring a CONTIGUOUS group (primitives in one sweep,
    composites in another) instead of interleaving around the full circle.
    """
    if n == 1:
        angs = [(a0 + a1) / 2.0]
    else:
        angs = [a0 + (a1 - a0) * i / (n - 1) for i in range(n)]
    return [(radius * math.cos(a), radius * math.sin(a)) for a in angs]


def draw(out_path: str, light: bool) -> None:
    C = palette(light)
    # Two clearly-separated rings so the inner/outer split reads at a glance:
    # primitives hug the hub, composites sit well outside them. Each ring is a
    # CONTIGUOUS group (no interleaving), so the eye reads two distinct orbits.
    R_PRIM, R_COMP = 3.15, 5.35
    # The 12 primitives form a COMPLETE, evenly-spaced inner circle (no gaps).
    # The 8 composites form a COMPLETE, evenly-spaced outer circle. Two clean
    # concentric orbits: inner primitives, outer composites.
    prim_pos = dict(zip(PRIMITIVES,
                        ring_positions(len(PRIMITIVES), R_PRIM, math.pi / 2)))
    comp_pos = dict(zip(COMPOSITES,
                        ring_positions(len(COMPOSITES), R_COMP,
                                       math.pi / 2 + math.pi / len(COMPOSITES))))
    pos = {**prim_pos, **comp_pos}

    fig, ax = plt.subplots(figsize=(14, 14), dpi=200)
    fig.patch.set_facecolor(C["bg"])
    ax.set_facecolor(C["bg"])
    ax.set_xlim(-7.4, 7.4)
    ax.set_ylim(-7.6, 6.9)
    ax.set_aspect("equal")
    ax.axis("off")

    # --- orbit guide rings ------------------------------------------------
    for r in (R_PRIM, R_COMP):
        ax.add_patch(Circle((0, 0), r, fill=False, ec=C["orbit"], lw=1.1,
                            ls=(0, (1, 4)), zorder=0))

    # (connecting lines intentionally omitted — planets only)

    # --- the hub: the agent (enlarged) -----------------------------------
    # layered glow
    for rr, aa in [(2.25, 0.05), (1.92, 0.08), (1.62, 0.13), (1.40, 0.20)]:
        ax.add_patch(Circle((0, 0), rr, color=C["hub_glow"], alpha=aa, zorder=3, lw=0))
    ax.add_patch(Circle((0, 0), 1.30, color=C["hub"], zorder=4,
                        ec=C["hub_glow"], lw=3.0))
    ax.text(0, 0, "agent", ha="center", va="center", color=C["hub_text"],
            fontsize=34, fontweight="bold", zorder=5,
            fontproperties=FontProperties(family="DejaVu Sans", weight="bold"))

    # --- planets ----------------------------------------------------------
    def planet(name, xy, face, ring, r):
        x, y = xy
        ax.add_patch(Circle((x, y), r + 0.10, color=ring, alpha=0.35, zorder=5, lw=0))
        ax.add_patch(Circle((x, y), r, color=face, zorder=6,
                            ec=C["bg"], lw=2.0))
        # label sits just outside the planet, pushed radially outward
        d = math.hypot(x, y)
        ox, oy = (x / d, y / d)
        lx, ly = x + ox * (r + 0.42), y + oy * (r + 0.42)
        ax.text(lx, ly, _label(name), ha="center", va="center",
                color=C["fg"], fontsize=9.3, zorder=7, linespacing=0.95,
                fontweight="medium")

    for s in PRIMITIVES:
        planet(s, prim_pos[s], C["prim"], C["prim_ring"], 0.30)
    for s in COMPOSITES:
        planet(s, comp_pos[s], C["comp"], C["comp_ring"], 0.34)

    # --- legend (compact, tucked into the bottom-left corner) -------------
    lx = -7.25
    ly = -6.95
    dy = 0.42
    r_chip = 0.10
    def chip(y, color, text):
        ax.add_patch(Circle((lx + 0.10, y), r_chip, color=color, zorder=8,
                            ec=C["bg"], lw=0.9))
        ax.text(lx + 0.34, y, text, ha="left", va="center", color=C["fg"],
                fontsize=7.2, zorder=8)
    chip(ly + dy, C["prim"], "primitive skill — drives a QC backend (xtb / PM7 / DFT / HF)")
    chip(ly, C["comp"], "composite skill — orchestrates primitives in-process")

    fig.savefig(out_path, facecolor=C["bg"], bbox_inches="tight", pad_inches=0.3)
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="skill_orbit.png", help="output image path")
    ap.add_argument("--dark", action="store_true", help="dark theme (default: light/white)")
    args = ap.parse_args()
    draw(args.out, light=not args.dark)


if __name__ == "__main__":
    main()
