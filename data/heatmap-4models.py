"""Per-(skill, model) pass-rate heatmap for the FOUR headline models only.

A focused variant of ``heatmap-with-warnings.py``: same data source, same
computation, and the SAME colour scheme (seaborn ``YlGnBu``), but restricted to
the four models featured in the paper — Claude Haiku 4.5, Claude Opus 4.8,
GPT-5.5, and o3. Each cell = the mean of the model's per-case ``pass_rate`` in
that skill's ``benchmarks/fidelity/<suite>/summary.csv`` (real ``argo:`` rows
only), rounded UP to two decimals. Model row labels drop the ``argo:`` prefix;
output is written as both PNG and vector PDF.

The matrix is NOT hardcoded; it is read from the CSVs so it can never drift.

Usage:
    python data/heatmap-4models.py                       # show
    python data/heatmap-4models.py --out heatmap-4models # writes .png AND .pdf
"""
import argparse
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# data/_style.py is a sibling; make it importable when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import savefig, short_model  # noqa: E402

_FIDELITY = Path(__file__).resolve().parent.parent / "benchmarks" / "fidelity"

# Column label (as shown in the figure) -> suite folder. Order = x-axis order.
# Identical to heatmap-with-warnings.py so the two figures stay comparable.
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

# The FOUR headline models (row order = y-axis order). CSV ids under `argo:`.
MODELS = [
    "argo:claude-haiku-4.5",
    "argo:claude-opus-4.8",
    "argo:gpt-5.5",
    "argo:o3",
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
    cols = {}
    for label, suite in SKILLS:
        means = _skill_pass_rates(suite)
        cols[label] = [
            (_ceil2(means[m]) if m in means else float("nan")) for m in MODELS
        ]
    # Rows labeled with the clean model name (drop the 'argo:' prefix).
    return pd.DataFrame(cols, index=[short_model(m) for m in MODELS])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None,
                    help="save to this path (writes both .png and .pdf) instead "
                         "of showing interactively")
    args = ap.parse_args()

    data = build_dataframe()

    # Four rows only -> a shorter figure than the 10-model version.
    fig = plt.figure(figsize=(14, 3.2))
    # Same colour scheme as heatmap-with-warnings.py (seaborn YlGnBu).
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
        for p in savefig(fig, args.out):
            print(f"wrote {p}")
    else:
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
