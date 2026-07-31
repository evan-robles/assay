#!/usr/bin/env python3
"""ASSAY/chemkit architecture — a concise layered block diagram.

A tight, professional systems figure: the layers a call passes through, top to
bottom, as labeled rows of boxes. No orbits, no illustration — hairline rules,
monospace labels, one accent reserved for the integrity gate.

    agent  ->  20 skills  ->  engine spine  ->  integrity gate  ->  backends

Content is read from the repo (server.py TOOLS, tasks/*.py, engine modules).

Usage:
    # Env: any env with matplotlib
    python tools/architecture_diagram.py [--out arch.png] [--dark]
"""
from __future__ import annotations

import argparse
from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch
from matplotlib.font_manager import FontProperties


PRIMITIVES: List[str] = [
    "single-point", "geometry-opt", "vib-analysis", "transition-state",
    "irc", "conf-scan", "electrostatics", "frontier-orbitals",
    "viz-orbitals", "conformer-search", "build-smiles", "name-to-smiles",
]
COMPOSITES: List[str] = [
    "redox", "reaction-profile", "reaction-energy", "pka",
    "logp", "solvation", "binding", "fukui",
]
SPINE: List[str] = ["calculators", "schema", "result_schema", "io", "resolve", "constants"]
BACKENDS: List[str] = ["xTB", "MOPAC", "OpenBabel", "ASE", "PySCF", "Sella"]


def theme(dark: bool) -> dict:
    if dark:
        return dict(
            bg="#111318", card="#1a1e27", ink="#e8eaf0", sub="#8a92a4",
            rule="#2c3340", chip="#20252f", chip_ink="#d6dae3",
            accent="#e06a4e", accent_bg="#2a1e1b", agent="#232a3a",
            prim="#4a5a72", comp="#6b4a3a",
        )
    return dict(
        bg="#ffffff", card="#ffffff", ink="#1a1c22", sub="#71767f",
        rule="#dcdce0", chip="#f6f6f4", chip_ink="#2a2c33",
        accent="#c0442a", accent_bg="#faeae5", agent="#eef1f7",
        prim="#e7ecf2", comp="#f4ece2",
    )


def draw(out_path: str, dark: bool) -> None:
    C = theme(dark)
    MONO = FontProperties(family="DejaVu Sans Mono")
    MONO_B = FontProperties(family="DejaVu Sans Mono", weight="bold")
    SANS_B = FontProperties(family="DejaVu Sans", weight="bold")

    fig, ax = plt.subplots(figsize=(11, 11.6), dpi=200)
    fig.patch.set_facecolor(C["bg"])
    ax.set_facecolor(C["bg"])
    W, H = 100.0, 106.0
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    xL, xR = 4, 96
    cw = xR - xL

    def rrect(x, y, w, h, face, edge, lw=1.1, z=2, radius=0.9):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                     boxstyle=f"round,pad=0,rounding_size={radius}",
                     facecolor=face, edgecolor=edge, lw=lw, zorder=z,
                     mutation_aspect=1.0))

    def tier_label(y, h, txt):
        ax.text(xL - 1.4, y + h / 2, txt, ha="right", va="center",
                color=C["sub"], fontsize=8, rotation=90, fontproperties=MONO)

    # ---- header ----------------------------------------------------------
    ax.text(xL, H - 2.5, "ASSAY", ha="left", va="top", color=C["ink"],
            fontsize=20, fontproperties=SANS_B)
    ax.text(xL, H - 7.4, "architecture", ha="left", va="top", color=C["sub"],
            fontsize=10.5, fontproperties=MONO)
    ax.text(xR, H - 3.0, "one call · top → bottom", ha="right", va="top",
            color=C["sub"], fontsize=9, fontproperties=MONO)

    top = H - 12
    gap = 2.4

    def band_title(x, y, name, note=None):
        ax.text(x + 2.4, y - 2.4, name, ha="left", va="top", color=C["ink"],
                fontsize=12, fontproperties=MONO_B)
        if note:
            ax.text(xR - 2.4, y - 2.4, note, ha="right", va="top", color=C["sub"],
                    fontsize=8.5, fontproperties=MONO)

    # ---- AGENT -----------------------------------------------------------
    h = 9
    y = top - h
    rrect(xL, y, cw, h, C["agent"], C["rule"], 1.3)
    ax.text(xL + 2.4, y + h / 2 + 0.6, "AGENT", ha="left", va="center",
            color=C["ink"], fontsize=13, fontproperties=MONO_B)
    ax.text(xL + 2.4, y + h / 2 - 2.2,
            "LLM caller — selects a skill, fills typed params, reports the result",
            ha="left", va="center", color=C["sub"], fontsize=8.6, fontproperties=MONO)
    tier_label(y, h, "caller")
    y_agent_bot = y

    # ---- SKILLS ----------------------------------------------------------
    def chip_row(items, y0, face):
        n = len(items)
        cgx = 1.3
        cwd = (cw - (n - 1) * cgx) / n
        ch = 4.6
        for i, it in enumerate(items):
            x = xL + i * (cwd + cgx)
            rrect(x, y0 - ch, cwd, ch, face, C["rule"], 0.9, z=3, radius=0.6)
            ax.text(x + cwd / 2, y0 - ch / 2, it, ha="center", va="center",
                    color=C["chip_ink"], fontsize=6.7, fontproperties=MONO, zorder=4)
        return ch

    h = 32
    y = y_agent_bot - gap - h
    rrect(xL, y, cw, h, C["card"], C["rule"], 1.3)
    band_title(xL, y + h, "SKILLS", "20 · one run() each")
    inner = y + h - 7.6                       # clear the SKILLS title row
    ax.text(xL + 2.4, inner + 0.5, "primitive — call a backend directly",
            ha="left", va="bottom", color=C["sub"], fontsize=7.8, fontproperties=MONO)
    ch1 = chip_row(PRIMITIVES[:6], inner, C["prim"])
    chip_row(PRIMITIVES[6:], inner - ch1 - 1.4, C["prim"])
    yc = inner - 2 * ch1 - 1.4 - 4.6
    ax.text(xL + 2.4, yc + 0.4, "composite — orchestrate primitives in-process",
            ha="left", va="bottom", color=C["accent"], fontsize=7.8, fontproperties=MONO)
    chip_row(COMPOSITES, yc, C["comp"])
    tier_label(y, h, "skills")
    y_skills_bot = y

    # ---- ENGINE SPINE ----------------------------------------------------
    h = 9
    y = y_skills_bot - gap - h
    rrect(xL, y, cw, h, C["card"], C["rule"], 1.3)
    band_title(xL, y + h, "ENGINE SPINE", "shared library")
    ax.text(W / 2, y + 2.7, "   ·   ".join(SPINE), ha="center", va="center",
            color=C["ink"], fontsize=9, fontproperties=MONO)
    tier_label(y, h, "engine")
    y_spine_bot = y

    # ---- INTEGRITY GATE (accent) -----------------------------------------
    h = 8
    y = y_spine_bot - gap - h
    rrect(xL, y, cw, h, C["accent_bg"], C["accent"], 1.8)
    ax.text(W / 2, y + h / 2 + 1.1, "INTEGRITY GATE", ha="center", va="center",
            color=C["accent"], fontsize=12.5, fontproperties=MONO_B)
    ax.text(W / 2, y + h / 2 - 1.9,
            "every result stamped trustworthy / gated — nothing passes unchecked",
            ha="center", va="center", color=C["ink"], fontsize=8.2, fontproperties=MONO)
    tier_label(y, h, "gate")
    y_gate_bot = y

    # ---- BACKENDS --------------------------------------------------------
    h = 9
    y = y_gate_bot - gap - h
    rrect(xL, y, cw, h, C["card"], C["rule"], 1.3)
    band_title(xL, y + h, "BACKENDS", "the physics")
    n = len(BACKENDS)
    seg = cw / n
    for i, b in enumerate(BACKENDS):
        if i:
            ax.plot([xL + i * seg, xL + i * seg], [y + 1.2, y + 4.6],
                    color=C["rule"], lw=0.9, zorder=3)
        ax.text(xL + i * seg + seg / 2, y + 2.9, b, ha="center", va="center",
                color=C["ink"], fontsize=8.4, fontproperties=MONO, zorder=3)
    tier_label(y, h, "compute")
    y_back_bot = y

    # ---- flow arrows on the right ----------------------------------------
    xd, xu = xR + 1.3, xR + 3.3
    ytop, ybot = y_agent_bot + 9, y_back_bot
    ax.add_patch(FancyArrowPatch((xd, ytop), (xd, ybot), arrowstyle="-|>",
                 mutation_scale=11, color=C["sub"], lw=1.3, zorder=1))
    ax.add_patch(FancyArrowPatch((xu, ybot), (xu, ytop), arrowstyle="-|>",
                 mutation_scale=11, color=C["accent"], lw=1.3, zorder=1))
    mid = (ytop + ybot) / 2
    ax.text(xd - 0.7, mid, "request", ha="center", va="center", color=C["sub"],
            fontsize=7.4, rotation=90, fontproperties=MONO)
    ax.text(xu + 1.7, mid, "result JSON", ha="center", va="center",
            color=C["accent"], fontsize=7.4, rotation=90, fontproperties=MONO)

    fig.savefig(out_path, facecolor=C["bg"], bbox_inches="tight", pad_inches=0.25)
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="architecture.png", help="output image path")
    ap.add_argument("--dark", action="store_true", help="dark theme (default: light)")
    args = ap.parse_args()
    draw(args.out, args.dark)


if __name__ == "__main__":
    main()
