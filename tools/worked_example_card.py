#!/usr/bin/env python3
"""Worked-example figure for the ASSAY poster — a JSON-output terminal card.

One real agent/CLI run (benzene frontier orbitals via xTB) shown the way the
tool actually prints it: shell commands, then the result JSON with syntax
highlighting and inline `//` comments annotating the chemistry. Every value is
transcribed verbatim from the real run.

Usage:
    # Env: any env with matplotlib
    python tools/worked_example_card.py [--out worked_example.png]
"""
from __future__ import annotations

import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle
from matplotlib.font_manager import FontProperties


# --- terminal syntax palette (dark navy ground, poster-compatible) ---------
WIN     = "#0d1a2b"   # window ground
BAR     = "#122238"   # title bar
BORDER  = "#25406b"   # subtle window border
TITLE   = "#8fa0bd"   # title-bar text (dim)
PROMPT  = "#e0913a"   # $ shell prompt (orange)
CMD     = "#e6ecf5"   # command text
KEY     = "#7fd6a0"   # JSON keys (green)
STR     = "#7fd6a0"   # JSON string values (green)
NUM     = "#e6a55c"   # numbers / true (amber)
PUNC    = "#b9c4d6"   # braces, colons, commas
COMMENT = "#6f7f9b"   # // comments (muted)
DIM     = "#8fa0bd"


def draw(out_path: str) -> None:
    MONO   = FontProperties(family="DejaVu Sans Mono")
    MONO_B = FontProperties(family="DejaVu Sans Mono", weight="bold")
    SANS_B = FontProperties(family="DejaVu Sans", weight="bold")

    fig, ax = plt.subplots(figsize=(12, 8.2), dpi=220)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    W, H = 100.0, 68.0
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    # section heading (poster style) above the window
    ax.text(1.5, H - 0.6, "WORKED EXAMPLE", ha="left", va="top", color="#275e97",
            fontsize=12, fontproperties=SANS_B)

    # ---- terminal window --------------------------------------------------
    wx, wy, ww, wh = 1.5, 1.5, 97, H - 6.5
    ax.add_patch(FancyBboxPatch((wx, wy), ww, wh,
                 boxstyle="round,pad=0,rounding_size=0.9",
                 facecolor=WIN, edgecolor=BORDER, lw=1.5, zorder=2))
    bar_h = 3.6
    ax.add_patch(FancyBboxPatch((wx, wy + wh - bar_h), ww, bar_h,
                 boxstyle="round,pad=0,rounding_size=0.9",
                 facecolor=BAR, edgecolor="none", zorder=3))
    ax.add_patch(Rectangle((wx, wy + wh - bar_h), ww, bar_h - 0.9,
                 facecolor=BAR, edgecolor="none", zorder=3))
    for i, c in enumerate(["#ff5f56", "#ffbd2e", "#27c93f"]):
        ax.add_patch(Circle((wx + 2.4 + i * 1.9, wy + wh - bar_h / 2), 0.45,
                            facecolor=c, edgecolor="none", zorder=4))
    ax.text(wx + 8.2, wy + wh - bar_h / 2, "ASSAY — visualize orbitals",
            ha="left", va="center", color=TITLE, fontsize=8.6,
            fontproperties=MONO, zorder=4)
    # NOTE: values below are transcribed verbatim from the real run
    # (GFN2-xTB single point on the Open-Babel geometry, orbitals//obabel).

    # ---- monospace char grid ---------------------------------------------
    x = wx + 3.0
    y = wy + wh - bar_h - 3.0
    fs = 9.4
    LH = 3.05

    fig.canvas.draw()
    rnd = fig.canvas.get_renderer()
    probe = ax.text(0, 0, "M" * 50, fontsize=fs, fontproperties=MONO, alpha=0)
    bb = probe.get_window_extent(renderer=rnd)
    inv = ax.transData.inverted()
    (x0, _), (x1, _) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y0)])
    CHW = (x1 - x0) / 50.0
    probe.remove()

    def put(col, yy, txt, colr, bold=False):
        ax.text(x + col * CHW, yy, txt, ha="left", va="center", color=colr,
                fontsize=fs, fontproperties=MONO_B if bold else MONO, zorder=5)

    row = y

    def nl(gap=LH):
        nonlocal row
        row -= gap

    def kv(key, val, val_col, comment=None, comment_col=30, last=False):
        """Render a `"key": value,` JSON line with optional // comment."""
        put(2, row, f'"{key}"', KEY)
        c = 2 + len(key) + 2
        put(c, row, ": ", PUNC)
        put(c + 2, row, val, val_col)
        tail = "" if last else ","
        put(c + 2 + len(val), row, tail, PUNC)
        if comment:
            put(comment_col, row, comment, COMMENT)
        nl()

    CC = 34   # column where inline // comments start
    TOOL = "#67b7ff"   # tool names in the trace (bright blue)

    # ---- interactive agent session (the real REPL transcript) ------------
    put(0, row, "assay>", CMD, True)
    put(7, row, "visualize the orbitals of benzene (.cube) using xTB", KEY, True)
    nl()
    put(2, row, "assay working…  chaining skills:", COMMENT)
    nl()
    for tool in ("skill_help", "build-from-smiles", "visualize-orbitals"):
        put(4, row, "→ ran", COMMENT)
        put(10, row, f"assay:{tool}", TOOL, True)
        nl()
    nl(0.7)
    put(2, row, "orbital cube files generated. full report ↓", COMMENT)
    nl(LH + 1.1)

    # ---- result JSON ------------------------------------------------------
    put(0, row, "{", PUNC)
    nl()
    # task / method
    put(2, row, '"task"', KEY); put(8, row, ": ", PUNC)
    put(10, row, '"visualize_orbitals"', STR); put(30, row, ",  ", PUNC)
    put(33, row, '"method"', KEY); put(41, row, ": ", PUNC)
    put(43, row, '"GFN2-xTB"', STR); put(53, row, ",", PUNC)
    nl()
    # charge / mult / solvent
    put(2, row, '"charge"', KEY); put(10, row, ": ", PUNC)
    put(12, row, "0", NUM); put(13, row, ",  ", PUNC)
    put(16, row, '"multiplicity"', KEY); put(30, row, ": ", PUNC)
    put(32, row, "1", NUM); put(33, row, ",  ", PUNC)
    put(36, row, '"solvent"', KEY); put(45, row, ": ", PUNC)
    put(47, row, '"gas phase"', STR); put(58, row, ",", PUNC)
    nl()
    kv("geometry", '"SMILES → Open Babel, 12 atoms"', STR)
    kv("homo_eV", "-10.9097", NUM, comment="// benzene π  (HOMO, MO 15)",
       comment_col=CC)
    kv("lumo_eV", "-6.0851", NUM, comment="// benzene π* (LUMO, MO 16)",
       comment_col=CC)
    kv("homo_lumo_gap_eV", "4.8246", NUM,
       comment="// Koopmans gap, 30 MOs", comment_col=CC)
    # integrity — laid out segment by segment on the char grid
    c = 2
    put(c, row, '"integrity"', KEY);            c += 11
    put(c, row, ": ", PUNC);                    c += 2
    put(c, row, "{ ", PUNC);                    c += 2
    put(c, row, '"status"', KEY);               c += 8
    put(c, row, ": ", PUNC);                    c += 2
    put(c, row, '"ok"', STR);                   c += 4
    put(c, row, ", ", PUNC);                    c += 2
    put(c, row, '"trustworthy"', KEY);          c += 13
    put(c, row, ": ", PUNC);                    c += 2
    put(c, row, "true", NUM);                   c += 4
    put(c, row, " },", PUNC)
    nl()
    kv("cli_invocation",
       '"orbitals --method xtb --cubes homo,lumo benzene.xyz"', STR, last=True)
    put(2, row, "… full method header + per-orbital table + 4 .cube files written",
        COMMENT)
    nl()
    put(0, row, "}", PUNC)

    fig.savefig(out_path, facecolor="#ffffff", bbox_inches="tight", pad_inches=0.12)
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="worked_example.png", help="output path")
    args = ap.parse_args()
    draw(args.out)


if __name__ == "__main__":
    main()
