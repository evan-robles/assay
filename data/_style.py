"""Shared research-grade matplotlib style for the ASSAY benchmark figures.

Import and call `apply()` once at the top of a plotting script to get a
consistent "clean journal" look across every figure: Helvetica sans, a restrained
slate palette with one desaturated terracotta accent, thin de-emphasized
spines/ticks, a subtle grid drawn behind the data, and publication-ready output.

    from _style import apply, savefig, sequential, ACCENT
    apply()
    ...
    colors = sequential(n)          # dark->light shades for a ranked bar chart
    savefig(fig, out)               # writes <out>.png (300 dpi) AND <out>.pdf

Design choices (why, not just what):
  * Helvetica is the canonical journal sans; falls back to Arial / Nimbus Sans /
    DejaVu so the scripts still run on machines without it.
  * mathtext (not text.usetex) — true LaTeX is slower and brittle when labels
    contain '%' or '_'; mathtext gives clean math with none of that fragility.
  * For a RANKED bar chart, `sequential(n)` shades the bars dark→light in the
    primary slate hue (position already encodes rank; the shade reinforces it),
    and any flagged outlier gets the terracotta ACCENT. Research figures earn
    attention with a considered low-chroma scheme, not a rainbow.
  * `collect_tokens()` / `short_model()` centralise the summary.csv aggregation
    so every figure reads the benchmark data the same way.
  * Grid behind bars, hairline spines, ticks only where they inform — the data,
    not the frame, should carry the ink.
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Dict, List
import matplotlib as mpl
import matplotlib.pyplot as plt

_FIDELITY = Path(__file__).resolve().parent.parent / "benchmarks" / "fidelity"

COMPLETION_COL = "completion_tokens_mean"
TOTAL_COL = "total_tokens_mean"
PASS_COL = "pass_rate"


def _num(row: dict, key: str):
    try:
        return float((row.get(key) or "").strip())
    except (ValueError, TypeError):
        return None


def short_model(m: str) -> str:
    """'argo:gpt-4o' -> 'gpt-4o'."""
    return m.split(":", 1)[1] if ":" in m else m


def collect_tokens() -> Dict[str, Dict[str, float]]:
    """model -> {completion, total, pass_rate, n_cases}, meaned over every case in
    every TOKEN-INSTRUMENTED suite summary.csv (real 'argo:' rows only). Shared by
    the token-efficiency figure."""
    comp: Dict[str, List[float]] = {}
    tot: Dict[str, List[float]] = {}
    pas: Dict[str, List[float]] = {}
    for suite in sorted(_FIDELITY.iterdir()):
        csvp = suite / "summary.csv"
        if not csvp.is_file():
            continue
        with csvp.open() as fh:
            reader = csv.DictReader(fh)
            if COMPLETION_COL not in (reader.fieldnames or []):
                continue  # suite not token-instrumented (e.g. fukui) — skip
            for r in reader:
                m = (r.get("model") or "").strip()
                if not m.startswith("argo:"):
                    continue
                c, t, p = _num(r, COMPLETION_COL), _num(r, TOTAL_COL), _num(r, PASS_COL)
                if c is not None:
                    comp.setdefault(m, []).append(c)
                if t is not None:
                    tot.setdefault(m, []).append(t)
                if p is not None:
                    pas.setdefault(m, []).append(p)
    data: Dict[str, Dict[str, float]] = {}
    for m in comp:
        data[m] = {
            "completion": statistics.fmean(comp[m]),
            "total": statistics.fmean(tot.get(m, [float("nan")])),
            "pass_rate": statistics.fmean(pas.get(m, [float("nan")])),
            "n_cases": float(len(comp[m])),
        }
    return data


def _skill_label(suite_dir_name: str) -> str:
    """'single-point-validation' -> 'single-point'; strip the trailing
    '-validation'/'-reactivity-validation' so column names read as the skill."""
    for suf in ("-reactivity-validation", "-validation"):
        if suite_dir_name.endswith(suf):
            return suite_dir_name[: -len(suf)]
    return suite_dir_name


def per_skill_accuracy():
    """Per (skill, model) pass-rate statistics from every suite summary.csv that
    exposes a pass_rate column.

    Returns (skills, per) where:
      * skills = ordered list of skill labels (suite folder order)
      * per[skill][model] = {"mean", "sd", "n_cases", "n_runs"}
    The cell mean is the mean of the skill's per-case pass_rates for that model;
    sd is the sample standard deviation ACROSS those cases (spread over the
    skill's molecules/tasks), or 0.0 when the skill has a single case. n_runs is
    the total scored runs (sum of n_runs) behind the cell.
    """
    skills: List[str] = []
    per: Dict[str, Dict[str, Dict[str, float]]] = {}
    for suite in sorted(_FIDELITY.iterdir()):
        csvp = suite / "summary.csv"
        if not csvp.is_file():
            continue
        with csvp.open() as fh:
            reader = csv.DictReader(fh)
            if PASS_COL not in (reader.fieldnames or []):
                continue
            by_model: Dict[str, List[float]] = {}
            runs_by_model: Dict[str, int] = {}
            for r in reader:
                m = (r.get("model") or "").strip()
                if not m.startswith("argo:"):
                    continue
                p = _num(r, PASS_COL)
                nr = _num(r, "n_runs")
                if p is not None:
                    by_model.setdefault(m, []).append(p)
                if nr is not None:
                    runs_by_model[m] = runs_by_model.get(m, 0) + int(nr)
            if not by_model:
                continue
        label = _skill_label(suite.name)
        skills.append(label)
        per[label] = {}
        for m, vals in by_model.items():
            per[label][m] = {
                "mean": statistics.fmean(vals),
                "sd": statistics.stdev(vals) if len(vals) > 1 else 0.0,
                "n_cases": float(len(vals)),
                "n_runs": float(runs_by_model.get(m, 0)),
            }
    return skills, per


def write_per_skill_csv(out_path, skills=None, per=None) -> Path:
    """Write a long-format per-(skill, model) accuracy CSV:
       skill, model, mean_pass_rate, sd_across_cases, n_cases, n_runs
    Long format (one row per cell) keeps mean AND sd machine-readable without
    cramming two numbers into one wide cell. Returns the path written."""
    if skills is None or per is None:
        skills, per = per_skill_accuracy()
    out_path = Path(out_path)
    models = sorted({m for s in skills for m in per[s]})
    with out_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["skill", "model", "mean_pass_rate",
                    "sd_across_cases", "n_cases", "n_runs"])
        for s in skills:
            for m in models:
                cell = per[s].get(m)
                if cell is None:
                    continue
                w.writerow([s, short_model(m),
                            f"{cell['mean']:.4f}", f"{cell['sd']:.4f}",
                            int(cell["n_cases"]), int(cell["n_runs"])])
    return out_path


def per_skill_tokens():
    """Per (skill, model): mean completion tokens ± SD across cases AND mean
    pass-rate ± SD across cases, from every TOKEN-INSTRUMENTED suite summary.csv.

    Returns (skills, per) where per[skill][model] = {"tok_mean","tok_sd",
    "pass_mean","pass_sd","n_cases"}. SD is the sample std across the skill's
    cases (0.0 for a single case). Only suites carrying completion_tokens_mean
    are included (e.g. fukui has no token columns and is skipped)."""
    skills: List[str] = []
    per: Dict[str, Dict[str, Dict[str, float]]] = {}
    for suite in sorted(_FIDELITY.iterdir()):
        csvp = suite / "summary.csv"
        if not csvp.is_file():
            continue
        with csvp.open() as fh:
            reader = csv.DictReader(fh)
            if COMPLETION_COL not in (reader.fieldnames or []):
                continue
            toks: Dict[str, List[float]] = {}
            pas: Dict[str, List[float]] = {}
            for r in reader:
                m = (r.get("model") or "").strip()
                if not m.startswith("argo:"):
                    continue
                c, p = _num(r, COMPLETION_COL), _num(r, PASS_COL)
                if c is not None:
                    toks.setdefault(m, []).append(c)
                if p is not None:
                    pas.setdefault(m, []).append(p)
            if not toks:
                continue
        label = _skill_label(suite.name)
        skills.append(label)
        per[label] = {}
        for m, tv in toks.items():
            pv = pas.get(m, [])
            per[label][m] = {
                "tok_mean": statistics.fmean(tv),
                "tok_sd": statistics.stdev(tv) if len(tv) > 1 else 0.0,
                "pass_mean": statistics.fmean(pv) if pv else float("nan"),
                "pass_sd": statistics.stdev(pv) if len(pv) > 1 else 0.0,
                "n_cases": float(len(tv)),
            }
    return skills, per


def write_per_skill_tokens_csv(out_path, skills=None, per=None) -> Path:
    """Write a long-format per-(skill, model) tokens+accuracy CSV:
       skill, model, mean_completion_tokens, sd_completion_tokens,
       mean_pass_rate, sd_pass_rate, n_cases
    Returns the path written."""
    if skills is None or per is None:
        skills, per = per_skill_tokens()
    out_path = Path(out_path)
    models = sorted({m for s in skills for m in per[s]})
    with out_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["skill", "model", "mean_completion_tokens",
                    "sd_completion_tokens", "mean_pass_rate", "sd_pass_rate",
                    "n_cases"])
        for s in skills:
            for m in models:
                cell = per[s].get(m)
                if cell is None:
                    continue
                w.writerow([s, short_model(m),
                            f"{cell['tok_mean']:.1f}", f"{cell['tok_sd']:.1f}",
                            f"{cell['pass_mean']:.4f}", f"{cell['pass_sd']:.4f}",
                            int(cell["n_cases"])])
    return out_path

# ── Palette ──────────────────────────────────────────────────────────────────
# A restrained, publication-typical scheme: one deep muted primary for the normal
# series, a desaturated warm accent for a flagged/outlier bar, and neutral inks
# for structure. These read as "research-grade" precisely because they are low-
# chroma and consistent — the data, not the colour, carries the signal.
PRIMARY = "#2B5A87"      # dark muted navy-blue (series default)
PRIMARY_DK = "#1C3E5E"   # darker navy (emphasis)
ACCENT = "#A24B38"       # deep muted brick (outlier flag)
ACCENT_DK = "#7E3928"    # darker brick (outlier emphasis / edge)
INK = "#17232E"          # near-black navy-slate for text / strong marks
MUTED = "#68717A"        # secondary text
FAINT = "#CDD3D8"        # grid / hairline spines (cool light grey)


def sequential(n: int, dark: str = PRIMARY_DK, light: str = "#AFC4D8"):
    """n colours shading dark→light in the primary hue, for a RANKED bar chart
    (position already encodes rank; the shade reinforces it without adding a new
    variable). Returns hex strings, darkest first."""
    import matplotlib.colors as mcolors
    c0 = mcolors.to_rgb(dark)
    c1 = mcolors.to_rgb(light)
    if n <= 1:
        return [dark]
    out = []
    for i in range(n):
        t = i / (n - 1)
        out.append(mcolors.to_hex(tuple(a + (b - a) * t for a, b in zip(c0, c1))))
    return out


# Okabe-Ito colourblind-safe set, for any figure that needs many categories.
# (Yellow #F0E442 is intentionally omitted here — it is illegible as a marker on
# white; add it back only for large filled areas, not points/bars on white.)
PALETTE = [
    "#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7",
    "#56B4E9", "#000000", "#7F7F7F", "#1B9E77", "#994F00",
]

_SANS = ["Helvetica", "Arial", "Nimbus Sans", "TeX Gyre Heros", "DejaVu Sans"]


def apply() -> None:
    """Install the house rcParams. Idempotent; call once per script."""
    mpl.rcParams.update({
        # Fonts
        "font.family": "sans-serif",
        "font.sans-serif": _SANS,
        "mathtext.fontset": "custom",
        "mathtext.rm": _SANS[0],
        "mathtext.it": f"{_SANS[0]}:italic",
        "mathtext.bf": f"{_SANS[0]}:bold",
        "axes.unicode_minus": True,      # true minus glyph, not hyphen

        # Sizes (hierarchy: title > label > tick > small)
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.titleweight": "semibold",
        "axes.labelsize": 12,
        "xtick.labelsize": 10.5,
        "ytick.labelsize": 10.5,
        "legend.fontsize": 9.5,

        # Colour of text / structural elements
        "text.color": INK,
        "axes.labelcolor": INK,
        "axes.titlecolor": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.edgecolor": FAINT,

        # Spines: hairline, only where useful (scripts hide top/right themselves)
        "axes.linewidth": 0.8,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.major.size": 3.5,
        "ytick.major.size": 3.5,
        "xtick.direction": "out",
        "ytick.direction": "out",

        # Grid: subtle, behind the data
        "axes.grid": False,              # scripts opt-in per axis
        "grid.color": FAINT,
        "grid.linewidth": 0.6,
        "grid.alpha": 0.35,
        "axes.axisbelow": True,

        # Figure / output
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "figure.dpi": 120,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,              # embed TrueType (editable text in vector)
        "ps.fonttype": 42,
        "legend.frameon": False,
    })


def savefig(fig, out) -> list[Path]:
    """Save a figure as BOTH a 300-dpi PNG and a vector PDF sibling.

    `out` may end in .png/.pdf or have no suffix; both siblings are written. The
    PDF is the publication master (scales without pixelation, text stays
    selectable); the PNG is the quick-look raster. Returns the paths written.
    """
    out = Path(out)
    stem = out.with_suffix("")
    written = []
    for ext in (".png", ".pdf"):
        p = stem.with_suffix(ext)
        fig.savefig(p)
        written.append(p)
    return written
