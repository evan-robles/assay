#!/usr/bin/env python3
"""Plot token efficiency — accuracy per token — one bar per model.

A single screening-grade efficiency number per model:

    efficiency = mean pass-rate / (mean tokens per run / 1000)
               = pass-rate points per 1000 tokens

aggregated over every case in every token-instrumented
`benchmarks/fidelity/<suite>/summary.csv`. Higher = more correctness per token
spent (cheap AND accurate). A model that is accurate but verbose, or terse but
inaccurate, both score low — the ratio rewards being cheap and right at once.

Default cost basis is COMPLETION tokens (what the model generates / controls);
`--metric total` uses prompt+completion instead, which can reorder models (a
terse model that re-sends context over many turns is efficient by completion but
not by total).

This is a screening heuristic, not a billing model: it treats one pass-rate
point and one token as directly tradeable, which they are not in general. Read it
as "who wastes the fewest tokens per unit of quality," not as a dollar figure.

Usage:
    python data/plot_efficiency.py                    # -> data/efficiency.png
    python data/plot_efficiency.py --metric total
    python data/plot_efficiency.py --out fig.png --show
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Reuse the vetted aggregation from the shared style module (reads every
# token-instrumented summary.csv and means pass-rate / completion / total per
# model over all its cases).
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import (apply as _apply_style, savefig, collect_tokens as collect,  # noqa: E402
                    write_per_skill_tokens_csv, short_model as _short,
                    PRIMARY, ACCENT, ACCENT_DK, MUTED, INK)

_REPO = Path(__file__).resolve().parent.parent

BAR_COLOR = PRIMARY         # muted steel blue (house style)
BAR_COLOR_LOW = ACCENT      # desaturated brick red for the accuracy outlier
LOW_PASS = 0.90             # models below this are flagged (quality outlier)


def plot(out: Path, show: bool, metric: str) -> None:
    _apply_style()
    data = collect()
    if not data:
        raise SystemExit("No token data found in any summary.csv.")

    scale = 1000.0 if metric == "completion" else 10000.0
    unit = "1k completion" if metric == "completion" else "10k total"

    rows = []
    for m in data:
        d = data[m]
        tokens = d[metric]
        # Pass-rate PERCENTAGE POINTS per 1000 completion tokens (pass_rate is a
        # 0-1 fraction, so ×100 gives points): e.g. gpt-4o = 98.2 / (270/1000).
        eff = (d["pass_rate"] * 100.0) / (tokens / scale)
        rows.append((_short(m), eff, d["pass_rate"], tokens))
    rows.sort(key=lambda r: -r[1])  # descending -> most efficient on the left

    names = [r[0] for r in rows]
    effs = [r[1] for r in rows]
    passes = [r[2] for r in rows]
    toks = [r[3] for r in rows]

    # Colour: one solid primary navy for every bar — no per-bar accent.
    n = len(rows)
    x = list(range(n))
    fig, ax = plt.subplots(figsize=(0.92 * n + 2.2, 6.2))
    bars = ax.bar(x, effs, color=PRIMARY, edgecolor="white", linewidth=0.6,
                  width=0.70, zorder=2)

    ymax = max(effs)
    ax.set_ylim(0, ymax * 1.06)
    ax.set_xlim(-0.7, n - 0.3)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=30, ha="right")
    ax.set_ylabel("Pass rate (%) per 1,000 completion tokens")

    ax.grid(True, axis="y")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="x", length=0)   # no tick marks under rotated names

    fig.subplots_adjust(bottom=0.18, top=0.97, left=0.10, right=0.98)
    written = savefig(fig, out)
    print("wrote " + ", ".join(str(p) for p in written)
          + f"  (metric={metric})")
    print("ranking (most→least efficient): "
          + ", ".join(f"{nm} {e:.1f}" for nm, e, *_ in reversed(rows)))

    # Companion data dump: per-(skill, model) mean completion tokens ± SD and
    # mean pass-rate ± SD across that skill's cases (long format).
    csv_out = Path(out).with_suffix("").with_name(
        Path(out).stem + "_per_skill").with_suffix(".csv")
    write_per_skill_tokens_csv(csv_out)
    print(f"wrote {csv_out}")

    if show:
        matplotlib.use("TkAgg", force=True)
        plt.show()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(_REPO / "data" / "efficiency.png"),
                    help="output image path (default: data/efficiency.png)")
    ap.add_argument("--metric", default="completion",
                    choices=["completion", "total"],
                    help="token basis for the ratio (default: completion)")
    ap.add_argument("--show", action="store_true", help="also display interactively")
    args = ap.parse_args()
    plot(Path(args.out), args.show, args.metric)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
