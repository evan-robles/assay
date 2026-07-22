#!/usr/bin/env python3
"""Combined two-panel figure: task accuracy (left) and token efficiency (right).

Both panels share the SAME model order — the order the models appear in the
benchmark summary.csv files (not re-sorted by value) — so each model sits at the
same x-position in both panels and can be read across. Because the order is
identical, the x-tick model labels are drawn once along the bottom of both panels.

    LEFT  — Per-case pass-rate (%): one jittered, semi-transparent dot per case
            with a median mark and IQR spine, on a full 0–100% axis with a dashed
            100% line. (Not a bar + SD whisker: pass-rate is bounded at 100% and
            these data pile at the ceiling, so a symmetric whisker would extend
            above the physical maximum — see plot_accuracy.py.)
    RIGHT — Pass rate (%) per 1,000 completion tokens (efficiency), navy bars.

House style (see _style.py). Writes PNG (300 dpi) + PDF.

Usage:
    python data/plot_combined.py                 # -> data/combined.png (+ .pdf)
    python data/plot_combined.py --out fig.png --show
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import (apply as _apply_style, savefig, collect_tokens,  # noqa: E402
                    short_model, PRIMARY, MUTED, INK)
from plot_accuracy import collect as collect_pass, draw_strip  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent


def _csv_order(models) -> list:
    """Models in benchmark-CSV order (alphabetical by full 'argo:' id, which is
    the order the summary.csv rows are written). Kept verbatim — no value sort."""
    return sorted(models)


def plot(out: Path, show: bool) -> None:
    _apply_style()

    # ── Data ──────────────────────────────────────────────────────────────────
    pr = collect_pass()                 # model -> [per-case pass_rate]
    tok = collect_tokens()              # model -> {completion, pass_rate, ...}

    # Shared model order = CSV order, intersected with models present in both.
    models = _csv_order([m for m in pr if m in tok])
    names = [short_model(m) for m in models]
    n = len(models)
    x = list(range(n))

    series = [pr[m] for m in models]    # per-model list of per-case pass-rates (dots)
    # Pass-rate PERCENTAGE POINTS per 1000 completion tokens (pass_rate is a 0-1
    # fraction, so ×100 gives points): e.g. gpt-4o = 98.2 / (270/1000).
    eff = [(tok[m]["pass_rate"] * 100.0) / (tok[m]["completion"] / 1000.0)
           for m in models]

    # ── Figure ────────────────────────────────────────────────────────────────
    fig, (axL, axR) = plt.subplots(
        1, 2, figsize=(1.15 * n + 4.0, 6.2))

    # LEFT: per-case dot strip with median + IQR (shared with plot_accuracy.py).
    # No bar / no symmetric SD whisker — nothing is drawn above the 100% ceiling.
    draw_strip(axL, series)
    axL.axhline(1.0, color=MUTED, linewidth=0.9, linestyle=(0, (4, 3)),
                alpha=0.7, zorder=1)
    axL.set_ylim(0.0, 1.03)             # full 0–100% axis
    axL.yaxis.set_major_formatter(lambda v, _p: f"{v*100:.0f}")
    axL.set_ylabel("Per-case pass-rate (%)")

    # RIGHT: efficiency.
    axR.bar(x, eff, color=PRIMARY, edgecolor="white", linewidth=0.6,
            width=0.70, zorder=2)
    axR.set_ylim(0, max(eff) * 1.06)
    axR.set_ylabel("Pass rate (%) per 1,000 completion tokens")

    # Shared x-axis: identical model order, so label both the same way (each panel
    # keeps its own labels for standalone legibility side by side).
    for ax in (axL, axR):
        ax.set_xlim(-0.7, n - 0.3)
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=30, ha="right")
        ax.grid(True, axis="y")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(axis="x", length=0)

    # Panel labels (A / B) — bold, upper-left of each panel, in figure convention.
    for ax, tag in ((axL, "A"), (axR, "B")):
        ax.text(-0.02, 1.04, tag, transform=ax.transAxes,
                fontsize=15, fontweight="bold", va="bottom", ha="right",
                color=INK)

    fig.subplots_adjust(bottom=0.20, top=0.97, left=0.07, right=0.99, wspace=0.18)
    written = savefig(fig, out)
    print("wrote " + ", ".join(str(p) for p in written) + f"  ({n} models)")
    if show:
        matplotlib.use("TkAgg", force=True)
        plt.show()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(_REPO / "data" / "combined.png"),
                    help="output image path (default: data/combined.png)")
    ap.add_argument("--show", action="store_true", help="also display interactively")
    args = ap.parse_args()
    plot(Path(args.out), args.show)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
