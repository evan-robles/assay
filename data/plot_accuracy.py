#!/usr/bin/env python3
"""Plot per-model task accuracy — a per-case dot strip with a median line.

One column per model. Each dot is the pass-rate of ONE benchmark case, over every
case in every `benchmarks/fidelity/<suite>/summary.csv` that has a `pass_rate`
column (~111 cases per model, ~5 runs per case). Dots are horizontally jittered
and drawn semi-transparent, so the dense band at 100% shows the many cases solved
perfectly while the sparse lower tail shows the hard cases as distinct points. The
heavy horizontal mark is the per-model MEDIAN pass-rate; the thin vertical line
spans the interquartile range (Q1–Q3).

Why a dot strip and not a bar + SD whisker: pass-rate is bounded at 100%, and for
these near-ceiling data most per-case values sit exactly at 1.0 with a thin lower
tail. A symmetric mean ± SD whisker therefore extends ABOVE 100% — into a region
where no data can exist — which misrepresents the (skewed, bounded) distribution.
Plotting the per-case distribution directly is honest by construction: nothing is
drawn above the 100% physical maximum, and the reader sees the real shape. The
mean ± SD is still printed to stdout for the paper text/tables.

The y-axis spans the full 0–100% so the lowest cases appear in true context; a
dashed line marks the 100% ceiling. Columns are sorted most→least accurate by mean.
Horizontal jitter is SEEDED (deterministic), so the figure renders identically on
every run.

Usage:
    python data/plot_accuracy.py                 # -> data/accuracy.png
    python data/plot_accuracy.py --out fig.png --show
"""
from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Dict, List

import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import (apply as _apply_style, savefig, write_per_skill_csv,  # noqa: E402
                    PRIMARY, MUTED, INK)

_REPO = Path(__file__).resolve().parent.parent
_FIDELITY = _REPO / "benchmarks" / "fidelity"

JITTER_SEED = 0             # seed for deterministic horizontal dot jitter
JITTER_W = 0.18            # half-width of the horizontal jitter (axis units)


def collect() -> Dict[str, List[float]]:
    """model -> list of per-case pass_rate values, across every suite whose
    summary.csv exposes a pass_rate column (real rows only: model starts with
    'argo:')."""
    pr: Dict[str, List[float]] = {}
    for suite in sorted(_FIDELITY.iterdir()):
        csvp = suite / "summary.csv"
        if not csvp.is_file():
            continue
        with csvp.open() as fh:
            reader = csv.DictReader(fh)
            if "pass_rate" not in (reader.fieldnames or []):
                continue
            for r in reader:
                m = (r.get("model") or "").strip()
                if not m.startswith("argo:"):
                    continue
                v = (r.get("pass_rate") or "").strip()
                try:
                    pr.setdefault(m, []).append(float(v))
                except ValueError:
                    pass
    return pr


def _short(m: str) -> str:
    return m.split(":", 1)[1] if ":" in m else m


def draw_strip(ax, series, seed: int = JITTER_SEED) -> None:
    """Draw a per-case dot strip with median + IQR for each column.

    `series` is a list of per-column value lists (one list of per-case pass-rates
    per model), in the same left→right order as the x-tick labels. Column i is
    centred at integer x=i. Dots are seeded-jittered and semi-transparent so a
    ceiling pile-up reads as a dark band; the heavy horizontal mark is the column
    MEDIAN and a thin vertical line spans Q1–Q3. Draws no bar and no symmetric
    whisker, so nothing is rendered above the data's bound.
    """
    rng = np.random.default_rng(seed)
    for xi, vals in enumerate(series):
        if not vals:
            continue
        arr = np.asarray(vals, dtype=float)
        dx = rng.uniform(-JITTER_W, JITTER_W, size=arr.size)
        q1, med, q3 = np.percentile(arr, [25, 50, 75])
        # IQR spine (behind), then dots (semi-transparent), then median (on top).
        ax.vlines(xi, q1, q3, color=MUTED, linewidth=1.2, alpha=0.6, zorder=3)
        ax.scatter(xi + dx, arr, s=22, color=PRIMARY, alpha=0.35,
                   edgecolors="none", zorder=2)
        ax.hlines(med, xi - 0.28, xi + 0.28, color=INK, linewidth=2.2, zorder=4)


def plot(out: Path, show: bool) -> None:
    _apply_style()
    pr = collect()
    if not pr:
        raise SystemExit("No pass_rate data found in any summary.csv.")

    rows = []  # (name, mean, median, std, vals)
    for m, vals in pr.items():
        mean = statistics.fmean(vals)
        med = statistics.median(vals)
        std = statistics.stdev(vals) if len(vals) > 1 else 0.0
        rows.append((_short(m), mean, med, std, vals))
    rows.sort(key=lambda r: -r[1])  # most accurate first (left), by mean

    names = [r[0] for r in rows]
    means = [r[1] for r in rows]
    meds = [r[2] for r in rows]
    stds = [r[3] for r in rows]
    series = [r[4] for r in rows]      # per-model list of per-case pass-rates (dots)
    ncases = len(rows[0][4])

    n = len(rows)
    x = list(range(n))
    fig, ax = plt.subplots(figsize=(0.92 * n + 2.2, 6.2))

    # Per-case dot strip with median + IQR. No bar, no symmetric SD whisker: the
    # dots themselves show the (bounded, ceiling-piled) distribution, so nothing
    # is ever drawn above the 100% physical maximum.
    draw_strip(ax, series)

    # 100% reference line — the physical ceiling for a pass-rate.
    ax.axhline(1.0, color=MUTED, linewidth=0.9, linestyle=(0, (4, 3)),
               alpha=0.7, zorder=1)

    # Full 0–100% axis (a hair of headroom above 1.0 for the ceiling dots/line),
    # so the lowest cases appear in true context rather than a near-ceiling zoom.
    ax.set_ylim(0.0, 1.03)
    ax.set_xlim(-0.7, n - 0.3)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=30, ha="right")
    # Percent tick labels on the fraction axis.
    ax.yaxis.set_major_formatter(lambda v, _pos: f"{v*100:.0f}")
    ax.set_ylabel("Per-case pass-rate (%)")

    ax.grid(True, axis="y")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="x", length=0)   # no tick marks under rotated names

    fig.subplots_adjust(bottom=0.20, top=0.97, left=0.09, right=0.98)
    written = savefig(fig, out)
    print("wrote " + ", ".join(str(p) for p in written)
          + f"  ({n} models, {ncases} cases each)")
    print("ranking (most→least accurate; mean±SD across cases, median): "
          + ", ".join(f"{nm} {mu*100:.1f}%±{sd*100:.1f} (med {md*100:.1f})"
                      for nm, mu, md, sd, _ in rows))

    # Companion data dump: per-(skill, model) mean pass-rate ± SD across that
    # skill's cases (long format), written next to the figure.
    csv_out = Path(out).with_suffix("").with_name(
        Path(out).stem + "_per_skill").with_suffix(".csv")
    write_per_skill_csv(csv_out)
    print(f"wrote {csv_out}")

    if show:
        matplotlib.use("TkAgg", force=True)
        plt.show()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(_REPO / "data" / "accuracy.png"),
                    help="output image path (default: data/accuracy.png)")
    ap.add_argument("--show", action="store_true", help="also display interactively")
    args = ap.parse_args()
    plot(Path(args.out), args.show)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
