#!/usr/bin/env python3
"""ASSAY wordmark/logo generator — variants that stand out on the navy title bar.

Produces transparent-background PNGs sized for the poster's top-left logo box.
The poster bar is navy #275e97; the current logo is navy-on-navy and vanishes,
so these use high-contrast fills (white / accent) and an optional badge panel.

Variants:
  white   — white wordmark + accent molecular mark, transparent (drop on navy)
  badge   — white rounded panel with navy wordmark (max pop, a distinct unit)
  amber   — white wordmark with an amber accent ring on the mark

Usage:
    python tools/assay_logo.py --variant white --out assay_logo_white.png
"""
from __future__ import annotations

import argparse
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch
from matplotlib.font_manager import FontProperties


NAVY   = "#20456e"
NAVY2  = "#275e97"
WHITE  = "#ffffff"
AMBER  = "#f2a03d"
TEAL   = "#5fd0a8"
INKMOL = "#123"


def hexagon_mark(ax, cx, cy, r, node_col, bond_col, lw=2.2, dot=0.055):
    """A tiny benzene-ring glyph as the logo mark."""
    pts = [(cx + r * math.cos(math.radians(60 * i - 90)),
            cy + r * math.sin(math.radians(60 * i - 90))) for i in range(6)]
    for i in range(6):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 6]
        ax.plot([x1, x2], [y1, y2], color=bond_col, lw=lw,
                solid_capstyle="round", zorder=3)
    # inner aromatic circle
    ax.add_patch(Circle((cx, cy), r * 0.42, fill=False, ec=bond_col,
                        lw=lw * 0.75, zorder=3))
    for x, y in pts:
        ax.add_patch(Circle((x, y), dot, facecolor=node_col, edgecolor="none",
                            zorder=4))


def draw(out_path: str, variant: str) -> None:
    HEAVY = FontProperties(family="DejaVu Sans", weight="bold")
    MONO = FontProperties(family="DejaVu Sans Mono")

    fig, ax = plt.subplots(figsize=(7.2, 2.6), dpi=300)
    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 36)
    ax.set_aspect("equal")
    ax.axis("off")

    if variant == "badge":
        # white rounded panel; navy wordmark — reads as a distinct logo unit
        ax.add_patch(FancyBboxPatch((1, 2), 98, 32,
                     boxstyle="round,pad=0,rounding_size=3.2",
                     facecolor=WHITE, edgecolor="none", zorder=1))
        word_col, sub_col, mark_node, mark_bond = NAVY2, "#5a6b86", AMBER, NAVY2
        mx = 13
    elif variant == "amber":
        word_col, sub_col, mark_node, mark_bond = WHITE, "#cfe0f2", AMBER, WHITE
        mx = 13
    else:  # white
        word_col, sub_col, mark_node, mark_bond = WHITE, "#cfe0f2", TEAL, WHITE
        mx = 13

    # molecular mark on the left
    hexagon_mark(ax, mx, 19, 8.2, mark_node, mark_bond, lw=2.6, dot=1.1)
    if variant == "amber":
        ax.add_patch(Circle((mx, 19), 11.2, fill=False, ec=AMBER, lw=2.0,
                            alpha=0.9, zorder=2))

    # wordmark
    ax.text(27, 22, "ASSAY", ha="left", va="center", color=word_col,
            fontsize=40, fontproperties=HEAVY, zorder=5)
    ax.text(27.6, 8.5,
            "AGENTIC SIMULATION SUITE",
            ha="left", va="center", color=sub_col, fontsize=8.4,
            fontproperties=MONO, zorder=5)

    fig.savefig(out_path, transparent=(variant != "badge"),
                facecolor="none" if variant != "badge" else WHITE,
                bbox_inches="tight", pad_inches=0.06)
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", default="white",
                    choices=["white", "badge", "amber"])
    ap.add_argument("--out", default="assay_logo.png")
    args = ap.parse_args()
    draw(args.out, args.variant)


if __name__ == "__main__":
    main()
