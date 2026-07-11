#!/usr/bin/env python3
"""Plot per-skill completion-token usage, one line per model.

X-axis  : skill (the 13 requested skills, in the requested order).
Y-axis  : mean completion tokens per run for that (skill, model).
Lines   : one per model (up to 10), tab10 colormap.
Error   : std of the per-case completion-token means across that skill's cases
          (the spread over the skill's molecules), drawn as error bars.

Data source: each skill's `benchmarks/fidelity/<suite>/summary.csv`, which carries
a `completion_tokens_mean` column per (case, model). Rows are the real per-case
rows (model starts with 'argo:' and completion_tokens_mean is numeric); the
CSV's interleaved metadata/latency rows are skipped. A skill whose summary.csv
lacks token columns (e.g. fukui was not token-instrumented) shows no point for
that skill — a gap in each line — rather than a fabricated value.

Usage:
    python benchmarks/plot_token_usage.py                       # -> data/token_usage.png
    python benchmarks/plot_token_usage.py --out fig.png --show
"""
from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_FIDELITY = _REPO / "benchmarks" / "fidelity"

# Requested skills, in requested x-axis order -> validation-suite folder.
# (display label, suite folder). "pending" skills are included; they simply show
# gaps where a model has no token data yet.
SKILLS: List[tuple[str, str]] = [
    ("Fukui",                  "fukui-reactivity-validation"),
    ("Frontier orbitals",      "frontier-orbitals-validation"),
    ("Name to SMILES",         "name-to-smiles-validation"),
    ("Build from SMILES",      "build-validation"),
    ("Conformer search",       "conformer-search-validation"),
    ("Conformational analysis","conformational-analysis-validation"),
    ("pKa acidity",            "pka-acidity-validation"),       # pending
    ("Single point",           "single-point-validation"),
    ("Solvation",              "solvation-validation"),
    ("logP partition",         "logp-partition-validation"),
    ("Redox potential",        "redox-potential-validation"),  # pending
    ("Electrostatics",         "electrostatics-validation"),
    ("Vibrational analysis",   "vibrational-analysis-validation"),
]

TOKEN_COL = "completion_tokens_mean"

# Research-grade categorical encoding for up to 10 model lines. Okabe–Ito is the
# de-facto colorblind-safe palette for scientific figures (8 colors; extended
# here with two extra distinct hues). Because 10 lines exceed what color alone
# can reliably separate — especially in grayscale print or for colorblind
# readers — each series ALSO gets a distinct marker shape and a solid/dashed
# linestyle (redundant encoding), so the lines stay distinguishable without color.
MODEL_COLORS = [
    "#000000",  # black
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#009E73",  # bluish green
    "#F0E442",  # yellow
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
    "#7F7F7F",  # grey (extension)
    "#1B9E77",  # teal (extension)
]
MODEL_MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*", "h", "<"]
MODEL_LINESTYLES = ["-", "-", "-", "-", "-", "--", "--", "--", "--", "--"]


def _real_rows(csv_path: Path) -> List[dict]:
    """Real per-case rows from a summary.csv: model starts with 'argo:' and the
    token column parses as a number. Skips the interleaved metadata/latency rows
    (model=None / non-numeric token cell)."""
    if not csv_path.is_file():
        return []
    rows: List[dict] = []
    with csv_path.open() as fh:
        reader = csv.DictReader(fh)
        if TOKEN_COL not in (reader.fieldnames or []):
            return []  # this suite's summary was not token-instrumented (e.g. fukui)
        for r in reader:
            model = (r.get("model") or "").strip()
            cell = (r.get(TOKEN_COL) or "").strip()
            if not model.startswith("argo:"):
                continue
            try:
                r["_tok"] = float(cell)
            except ValueError:
                continue
            rows.append(r)
    return rows


def _model_name(raw: str) -> str:
    """'argo:gpt-4o' -> 'gpt-4o'."""
    return raw.split(":", 1)[1] if ":" in raw else raw


def collect() -> tuple[Dict[str, Dict[str, tuple[float, float]]], List[str]]:
    """Return per-skill per-model (mean, std) of completion tokens, and the sorted
    list of all models seen.

    data[skill_label][model] = (mean_over_cases, std_over_cases)
    The mean is over that skill's cases' `completion_tokens_mean` values; std is
    the sample std across those cases (0.0 when a model has only one case).
    """
    data: Dict[str, Dict[str, tuple[float, float]]] = {}
    models: set[str] = set()
    for label, suite in SKILLS:
        rows = _real_rows(_FIDELITY / suite / "summary.csv")
        per_model: Dict[str, List[float]] = {}
        for r in rows:
            per_model.setdefault(_model_name(r["model"]), []).append(r["_tok"])
        data[label] = {}
        for model, vals in per_model.items():
            models.add(model)
            mean = statistics.fmean(vals)
            std = statistics.stdev(vals) if len(vals) > 1 else 0.0
            data[label][model] = (mean, std)
    return data, sorted(models)


def plot(out: Path, show: bool) -> None:
    data, models = collect()
    labels = [lbl for lbl, _ in SKILLS]
    x = list(range(len(labels)))

    if not models:
        raise SystemExit("No token data found in any summary.csv "
                         "(no rows with completion_tokens_mean).")

    fig, ax = plt.subplots(figsize=(13, 7))

    for i, model in enumerate(models):
        xs, ys, es = [], [], []
        for xi, lbl in enumerate(labels):
            cell = data[lbl].get(model)
            if cell is None:
                continue  # gap: this model has no token data for this skill
            mean, std = cell
            xs.append(xi)
            ys.append(mean)
            es.append(std)
        if not xs:
            continue
        # Redundant encoding (color + marker + linestyle) so the 10 lines stay
        # distinguishable in grayscale and for colorblind readers.
        ax.errorbar(
            xs, ys, yerr=es,
            label=model,
            color=MODEL_COLORS[i % len(MODEL_COLORS)],
            marker=MODEL_MARKERS[i % len(MODEL_MARKERS)],
            linestyle=MODEL_LINESTYLES[i % len(MODEL_LINESTYLES)],
            markersize=5, linewidth=1.8, capsize=3,
            elinewidth=0.9, alpha=0.95,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=40, ha="right")
    ax.set_xlabel("Skill")
    ax.set_ylabel("Completion tokens per run (mean ± std over cases)")
    ax.set_title("Per-skill completion-token usage by model")
    ax.grid(True, axis="y", linestyle=":", alpha=0.5)
    ax.legend(title="Model", ncol=2, fontsize=8, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    print(f"wrote {out}  ({len(models)} models, {len(labels)} skills)")
    # Report any skill with no token data at all (e.g. fukui) so gaps are explicit.
    empty = [lbl for lbl in labels if not data[lbl]]
    if empty:
        print("skills with NO token data (gaps in every line): " + ", ".join(empty))
    if show:
        matplotlib.use("TkAgg", force=True)  # best-effort interactive
        plt.show()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(_REPO / "data" / "token_usage.png"),
                    help="output image path (default: data/token_usage.png)")
    ap.add_argument("--show", action="store_true", help="also display interactively")
    args = ap.parse_args()
    plot(Path(args.out), args.show)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
