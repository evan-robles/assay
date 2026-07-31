#!/usr/bin/env python3
"""ASSAY architecture figure for the poster — INTERFACE / SKILLS / ENGINE with
integrity GATE badges on the right rail. Poster-blue palette, white ground.

Layout matches the poster's ARCHITECTURE slot: three layer cards, dot-chips for
the 20 skills (primitive vs composite), a spine line + backend chips in the
engine, and a red 'GATE' badge aligned to each layer (interface / skills /
engine).

Usage:
    # Env: any env with matplotlib
    python tools/architecture_poster.py [--out architecture.png]
"""
from __future__ import annotations

import argparse
from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle
from matplotlib.font_manager import FontProperties


# --- content (real repo architecture) --------------------------------------
PRIMITIVES: List[str] = [
    "single-point", "geometry-opt", "vib-analysis", "transition-state",
    "irc", "conf-scan", "electrostatics", "frontier-orbitals",
    "viz-orbitals", "conformer-search", "build-smiles", "name-to-smiles",
]
COMPOSITES: List[str] = [
    "redox", "reaction-profile", "reaction-energy", "pka",
    "logp", "solvation", "binding", "fukui",
]
BACKENDS: List[str] = ["xTB", "MOPAC", "OpenBabel", "ASE", "PySCF", "Sella"]

# --- palette: sampled from the poster --------------------------------------
BG      = "#ffffff"
NAVY    = "#275e97"   # poster title-bar navy (layer titles)
BLUE    = "#3880c4"   # poster heading blue (borders)
DEEP    = "#26338b"   # poster heatmap deep navy
BLUE_L  = "#eaf1f9"   # layer fill
INK     = "#1f2430"
SUB     = "#5f6675"
RULE    = "#c9ccd2"
TEAL    = "#2f7fb0"   # primitive dot (mid poster blue)
COMPd   = "#26338b"   # composite dot (deep navy)
GATEC   = "#c0392b"   # gate accent (red, as in the pasted image)
GATE_BG = "#fbecea"
CHIP    = "#f4f7fb"


def draw(out_path: str) -> None:
    SANS_B = FontProperties(family="DejaVu Sans", weight="bold")
    SANS   = FontProperties(family="DejaVu Sans")
    MONO   = FontProperties(family="DejaVu Sans Mono")
    MONO_B = FontProperties(family="DejaVu Sans Mono", weight="bold")

    fig, ax = plt.subplots(figsize=(11, 9.6), dpi=220)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    W, H = 100.0, 96.0
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    xL, xR = 2, 88          # right gutter holds the GATE rail
    cw = xR - xL
    RAD = 0.6               # crisp near-square corners

    # Measure how wide a string renders (in data units) at a given font size,
    # so we can guarantee it fits inside its box and never clips/overflows.
    fig.canvas.draw()
    _rnd = fig.canvas.get_renderer()
    _inv = ax.transData.inverted()

    def text_width(s, fs, fp):
        t = ax.text(0, 0, s, fontsize=fs, fontproperties=fp, alpha=0)
        bb = t.get_window_extent(renderer=_rnd)
        (x0, _), (x1, _) = _inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y0)])
        t.remove()
        return x1 - x0

    def fit_text(x, y, s, max_w, fs, fp, color, ha="left", va="center", z=4):
        """Draw text, shrinking the font until it fits within max_w (data units)."""
        size = fs
        while size > 5.0 and text_width(s, size, fp) > max_w:
            size -= 0.3
        ax.text(x, y, s, ha=ha, va=va, color=color, fontsize=size,
                fontproperties=fp, zorder=z)

    def rrect(x, y, w, h, face, edge, lw=1.2, z=2, rad=RAD):
        if rad <= 0.01:
            ax.add_patch(Rectangle((x, y), w, h, facecolor=face, edgecolor=edge,
                                   lw=lw, zorder=z, joinstyle="miter"))
        else:
            ax.add_patch(FancyBboxPatch((x, y), w, h,
                         boxstyle=f"round,pad=0,rounding_size={rad}",
                         facecolor=face, edgecolor=edge, lw=lw, zorder=z))

    def gate_badge(ymid):
        gx = xR + 1.6
        rrect(gx, ymid - 2.4, 8.4, 4.8, GATE_BG, GATEC, 1.6, z=5, rad=0.0)
        ax.text(gx + 4.2, ymid, "GATE", ha="center", va="center", color=GATEC,
                fontsize=9.5, fontproperties=SANS_B, zorder=6)

    # ============ INTERFACE ==============================================
    h = 12
    y = H - 2 - h
    rrect(xL, y, cw, h, BLUE_L, BLUE, 2.0)
    ax.text(xL + 3.5, y + h - 3.5, "INTERFACE", ha="left", va="top", color=NAVY,
            fontsize=17, fontproperties=SANS_B)
    ax.text(xL + 3.5, y + 3.6, "MCP server · agent selects a tool, fills typed params",
            ha="left", va="center", color=SUB, fontsize=10.5, fontproperties=SANS)
    gate_badge(y + h / 2)

    # ============ SKILLS =================================================
    h = 46
    y = y - 4 - h
    rrect(xL, y, cw, h, "#ffffff", BLUE, 2.0)
    ax.text(xL + 3.5, y + h - 3.5, "SKILLS", ha="left", va="top", color=NAVY,
            fontsize=17, fontproperties=SANS_B)
    ax.text(xR - 3.5, y + h - 4.0, "20 tools", ha="right", va="top", color=SUB,
            fontsize=10.5, fontproperties=SANS)

    def chip_grid(items, y_top, dot):
        ncol = 4
        cgx, ch, cgy = 2.0, 4.6, 1.6
        cwd = (cw - 7 - (ncol - 1) * cgx) / ncol
        for i, it in enumerate(items):
            r, c = divmod(i, ncol)
            x = xL + 3.5 + c * (cwd + cgx)
            yy = y_top - r * (ch + cgy)
            rrect(x, yy - ch, cwd, ch, CHIP, RULE, 1.0, z=3, rad=0.0)
            ax.add_patch(Circle((x + 2.4, yy - ch / 2), 0.85, facecolor=dot,
                                edgecolor="none", zorder=4))
            label_x = x + 4.4
            avail = (x + cwd) - label_x - 1.4   # right padding inside the chip
            fit_text(label_x, yy - ch / 2, it, avail, 8.6, MONO, INK)

    ax.text(xL + 3.5, y + h - 8.4, "primitive — call a backend directly",
            ha="left", va="bottom", color=BLUE, fontsize=9.5, fontproperties=SANS_B)
    chip_grid(PRIMITIVES, y + h - 9.6, TEAL)          # 3 rows
    ax.text(xL + 3.5, y + 13.4, "composite — orchestrate primitives in-process",
            ha="left", va="bottom", color=DEEP, fontsize=9.5, fontproperties=SANS_B)
    chip_grid(COMPOSITES, y + 12.2, COMPd)            # 2 rows
    gate_badge(y + h / 2)

    # ============ ENGINE =================================================
    h = 22
    y = y - 4 - h
    rrect(xL, y, cw, h, BLUE_L, BLUE, 2.0)
    ax.text(xL + 3.5, y + h - 3.5, "ENGINE", ha="left", va="top", color=NAVY,
            fontsize=17, fontproperties=SANS_B)
    ax.text(xR - 3.5, y + h - 4.0, "shared Python engine", ha="right", va="top",
            color=SUB, fontsize=10.5, fontproperties=SANS)
    fit_text(xL + 3.5, y + h - 9.5,
             "calculators · schema · integrity · geometry I/O · name resolution",
             cw - 7, 10, MONO, INK)
    # backend chips
    n = len(BACKENDS)
    bgx = 1.8
    bwd = (cw - 7 - (n - 1) * bgx) / n
    for i, b in enumerate(BACKENDS):
        x = xL + 3.5 + i * (bwd + bgx)
        rrect(x, y + 2.4, bwd, 5.2, "#ffffff", BLUE, 1.3, z=3, rad=0.0)
        fit_text(x + bwd / 2, y + 2.4 + 2.6, b, bwd - 2.0, 9.4, SANS_B, NAVY,
                 ha="center")
    ax.text(xL + 3.5, y + 0.6, "backends", ha="left", va="bottom", color=SUB,
            fontsize=8.0, fontproperties=SANS)
    gate_badge(y + h / 2)

    fig.savefig(out_path, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="architecture.png", help="output path")
    args = ap.parse_args()
    draw(args.out)


if __name__ == "__main__":
    main()
