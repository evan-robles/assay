"""Per-(skill, model) pass-rate heatmap, read directly from the benchmark CSVs.

The matrix is NOT hardcoded: each cell is the mean of the model's per-case
`pass_rate` values in that skill's `benchmarks/fidelity/<suite>/summary.csv`
(real `argo:` rows only), so the figure can never drift from the data. Cells are
rounded UP to two decimals (ceiling) for display.

Rows are the 10 benchmarked models (fixed order); columns are the 13 benchmarked
skills (fixed order, matching the paper).

Usage:
    python data/heatmap-with-warnings.py            # show
    python data/heatmap-with-warnings.py --out heatmap-with-warnings.png
"""
import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

_FIDELITY = Path(__file__).resolve().parent.parent / "benchmarks" / "fidelity"

# Column label (as shown in the figure) -> suite folder. Order = x-axis order.
SKILLS = [
    ("single-point", "single-point-validation"),
    ("name-to-smiles", "name-to-smiles-validation"),
    ("build-from-smiles", "build-validation"),
    ("conformer-search", "conformer-search-validation"),
    ("conformational-analysis", "conformational-analysis-validation"),
    ("solvation", "solvation-validation"),
    ("vibrational-analysis", "vibrational-analysis-validation"),
    ("logp-partition", "logp-partition-validation"),
    ("electrostatics", "electrostatics-validation"),
    ("redox-potential", "redox-potential-validation"),
    ("pka-acidity", "pka-acidity-validation"),
    ("fukui-analysis", "fukui-reactivity-validation"),
    ("frontier-orbitals", "frontier-orbitals-validation"),
]

# Row order = y-axis order. Each entry is (display label shown on the axis,
# raw model id used to look the model up in the CSVs). Keep these separate: the
# CSV rows are keyed by the 'argo:' id, while the figure should show clean names.
MODELS = [
    ("Claude Haiku 4.5", "argo:claude-haiku-4.5"),
    ("Claude Opus 4.8", "argo:claude-opus-4.8"),
    ("Claude Sonnet 4.6", "argo:claude-sonnet-4.6"),
    ("Gemini 2.5 Flash", "argo:gemini-2.5-flash"),
    ("Gemini 2.5 Pro", "argo:gemini-2.5-pro"),
    ("GPT 4.1 Nano", "argo:gpt-4.1-nano"),
    ("GPT 4o", "argo:gpt-4o"),
    ("GPT 5.5", "argo:gpt-5.5"),
    ("GPT o3", "argo:o3"),
    ("GPT o4 Mini", "argo:o4-mini"),
]


def _ceil2(x: float) -> float:
    """Ceiling to two decimals (round-up), with an epsilon guard against float
    dust so an exact 0.98 stored as 0.98000001 is not bumped to 0.99."""
    return math.ceil(round(x, 9) * 100) / 100


def _skill_pass_rates(suite: str):
    """model -> mean per-case pass_rate for one suite's summary.csv."""
    p = _FIDELITY / suite / "summary.csv"
    by_model = defaultdict(list)
    if p.is_file():
        with p.open() as fh:
            for r in csv.DictReader(fh):
                m = (r.get("model") or "").strip()
                if not m.startswith("argo:"):
                    continue
                try:
                    by_model[m].append(float(r["pass_rate"]))
                except (TypeError, ValueError):
                    pass
    return {m: statistics.fmean(v) for m, v in by_model.items() if v}


def build_dataframe() -> pd.DataFrame:
    labels = [disp for disp, _id in MODELS]      # y-axis display names
    ids = [mid for _disp, mid in MODELS]         # raw 'argo:' lookup keys
    cols = {}
    for label, suite in SKILLS:
        means = _skill_pass_rates(suite)
        cols[label] = [
            (_ceil2(means[mid]) if mid in means else float("nan")) for mid in ids
        ]
    return pd.DataFrame(cols, index=labels)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None,
                    help="save to this path instead of showing interactively")
    args = ap.parse_args()

    data = build_dataframe()

    plt.figure(figsize=(14, 6))
    sns.heatmap(
        data,
        annot=True,
        fmt=".2f",
        cmap="YlGnBu",
        linewidths=0.5,
    )
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()

    if args.out:
        plt.savefig(args.out, dpi=300, bbox_inches="tight")
        print(f"wrote {args.out}")
    else:
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
