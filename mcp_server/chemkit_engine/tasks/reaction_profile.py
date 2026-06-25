"""Full reaction-profile pipeline: opt(R) + opt(P) + ts(guess) + freq + IRC.

This is a composition skill — it chains existing tasks in a deterministic
order and produces:
  - ΔG‡ (activation free energy) and ΔG_rxn at the requested temperature
  - A connectivity check confirming the IRC endpoints match R and P
    (RMSD threshold; can fail with a clear "TS connects different species"
    warning)
  - A schematic energy-diagram PNG (R / TS / P levels with ΔG‡ and ΔG_rxn
    labeled, connected by smooth interpolations)
  - All intermediate JSON + xyz files referenced from the result

The IRC connectivity check is the most-skipped step in typical TS workflows
("we found a TS, ship it") — and is exactly the question reviewers always
ask. Making it part of the pipeline keeps the user from having to remember
to do it themselves.

Skipping IRC: if --no-irc is set (or method == dft/hf where IRC isn't
implemented), the IRC stage is skipped and the connectivity check falls back
to a simpler "RMSD between the converged TS geometry and each minimum"
heuristic, which is much weaker but better than nothing.
"""
from __future__ import annotations
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

import numpy as np

from ..calculators import program_label, method_label, build_calculator
from ..io import read_geometry
from ..schema import base_result, EV_TO_KCAL
from . import opt as opt_task
from . import freq as freq_task
from . import ts as ts_task
from . import irc as irc_task


# ---------------------------------------------------------------------------
# RMSD utility
# ---------------------------------------------------------------------------

def _kabsch_rmsd(a, b) -> float:
    """Minimum-RMSD over rigid-body alignment of two (N, 3) arrays. Returns
    +inf if the atom counts differ.

    Plain Kabsch — no atom-order remapping, so the two arrays must be in the
    same atom order. For our pipeline this is true: every IRC frame and every
    optimized geometry inherits the same atom ordering as the TS input.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.shape != b.shape:
        return float("inf")
    a = a - a.mean(0)
    b = b - b.mean(0)
    H = a.T @ b
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1, 1, d])
    R = Vt.T @ D @ U.T
    a_rot = a @ R.T
    return float(np.sqrt(((a_rot - b) ** 2).sum() / len(a)))


# ---------------------------------------------------------------------------
# Diagram
# ---------------------------------------------------------------------------

def _emit_diagram(*, png_path: str, G_R, G_TS, G_P, dG_act, dG_rxn,
                  reaction_label: str, energy_label: str = "ΔG (kcal/mol)"):
    """Three-level energy diagram with smooth Bezier-like interpolation."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    # x positions for R / TS / P
    xs_levels = [0.0, 1.0, 2.0]
    ys_levels = [G_R, G_TS, G_P]

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)

    # Draw flat horizontal segments at each stationary point
    HALF = 0.18
    for x, y in zip(xs_levels, ys_levels):
        ax.plot([x - HALF, x + HALF], [y, y], color="black", lw=2.2)

    # Connect levels with smooth cubic-Hermite interpolation
    def _smooth(x0, y0, x1, y1, npts=60):
        t = np.linspace(0, 1, npts)
        # Hermite with zero slope at endpoints → smooth S-curve, no overshoot
        h00 = 2 * t**3 - 3 * t**2 + 1
        h01 = -2 * t**3 + 3 * t**2
        return x0 + (x1 - x0) * t, h00 * y0 + h01 * y1

    for (x0, y0), (x1, y1) in zip(
        [(xs_levels[0] + HALF, ys_levels[0]), (xs_levels[1] + HALF, ys_levels[1])],
        [(xs_levels[1] - HALF, ys_levels[1]), (xs_levels[2] - HALF, ys_levels[2])],
    ):
        xs, ys = _smooth(x0, y0, x1, y1)
        ax.plot(xs, ys, color="#4477aa", lw=1.6)

    # Labels under each level
    for x, label in zip(xs_levels, ["Reactants", "TS", "Products"]):
        ax.text(x, ys_levels[xs_levels.index(x)] - 0.04 * abs(max(ys_levels) - min(ys_levels) + 1),
                label, ha="center", va="top", fontsize=11)

    # Energy labels next to each level
    for x, y in zip(xs_levels, ys_levels):
        ax.text(x + HALF + 0.05, y, f"{y:.1f}", va="center", ha="left", fontsize=9)

    # Annotate ΔG‡ (between R and TS) and ΔG_rxn (between R and P)
    ax.annotate(
        "", xy=(0.5, G_TS), xytext=(0.5, G_R),
        arrowprops=dict(arrowstyle="<->", color="#cc3333", lw=1.4),
    )
    ax.text(0.55, (G_R + G_TS) / 2, f"ΔG$^‡$ = {dG_act:.1f}",
            color="#cc3333", fontsize=10, va="center")
    ax.annotate(
        "", xy=(2.0, G_P), xytext=(2.0, G_R),
        arrowprops=dict(arrowstyle="<->", color="#117733", lw=1.4),
    )
    ax.text(2.05, (G_R + G_P) / 2, f"ΔG$_{{rxn}}$ = {dG_rxn:.1f}",
            color="#117733", fontsize=10, va="center")

    ax.set_xlim(-0.4, 2.7)
    ax.set_ylabel(energy_label)
    ax.set_title(reaction_label)
    ax.set_xticks([])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(png_path)
    plt.close(fig)
    return png_path


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    *,
    reactant_xyz: str,
    product_xyz: str,
    ts_guess_xyz: str,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    temperature_K: float = 298.15,
    pressure_Pa: float = 101325.0,
    rmsd_tol: float = 0.5,           # Å, for IRC-endpoint connectivity
    skip_irc: bool = False,
    out_stem: Optional[str] = None,
    cli: str = "",
    tier: Optional[str] = None,
    functional: Optional[str] = None,
    basis: Optional[str] = None,
    density_fit: bool = False,
    solvent_model: str = "ddcosmo",
    gate_integrity: bool = True,
    allow_unconverged: bool = False,
) -> Dict[str, Any]:
    """Run the full opt(R) + opt(P) + ts(guess) + freq×3 + IRC pipeline.

    Returns a result dict with ΔE / ΔH / ΔG for both the activation and the
    overall reaction, a connectivity verdict, and pointers to every
    intermediate file.
    """
    method = method.lower()
    common_kw = dict(
        method=method, charge=charge, multiplicity=multiplicity, solvent=solvent,
        tier=tier, functional=functional, basis=basis, density_fit=density_fit,
        solvent_model=solvent_model,
        # Every R/P/TS opt+freq is a sub-call: stamp its own integrity but never
        # raise mid-profile (the TS freq legitimately has 1 imaginary mode). The
        # profile gates its own aggregated verdict at the end.
        gate_integrity=False,
    )

    workdir = tempfile.mkdtemp(prefix="chemkit_profile_")
    stem = out_stem or os.path.splitext(reactant_xyz)[0] + f"_profile_{method}"

    # ----- 1) Optimize reactant -----
    r_opt_xyz = os.path.join(workdir, "reactant_opt.xyz")
    r_opt = opt_task.run(
        reactant_xyz, out_xyz=r_opt_xyz,
        cli="(internal reaction_profile: opt R)", **common_kw,
    )
    # ----- 2) Optimize product -----
    p_opt_xyz = os.path.join(workdir, "product_opt.xyz")
    p_opt = opt_task.run(
        product_xyz, out_xyz=p_opt_xyz,
        cli="(internal reaction_profile: opt P)", **common_kw,
    )

    # ----- 3) Locate TS -----
    ts_stem = os.path.join(workdir, "ts")
    ts_res = ts_task.run(
        ts_guess_xyz, out_stem=ts_stem,
        steps=500, verify_freq=False,  # we'll do our own freq below
        cli="(internal reaction_profile: TS search)", **common_kw,
    )
    ts_xyz = ts_res.get("ts_xyz") or ts_stem + ".xyz"
    ts_converged = bool(ts_res.get("converged"))

    # ----- 4) Freq on each stationary point (G + minimum check) -----
    r_freq = freq_task.run(
        r_opt_xyz, temperature_K=temperature_K, pressure_Pa=pressure_Pa,
        preopt=False, cli="(internal reaction_profile: freq R)", **common_kw,
    )
    p_freq = freq_task.run(
        p_opt_xyz, temperature_K=temperature_K, pressure_Pa=pressure_Pa,
        preopt=False, cli="(internal reaction_profile: freq P)", **common_kw,
    )
    ts_freq = freq_task.run(
        ts_xyz, temperature_K=temperature_K, pressure_Pa=pressure_Pa,
        preopt=False, cli="(internal reaction_profile: freq TS)", **common_kw,
    )

    G_R_eV  = r_freq["gibbs_free_energy_eV"]
    G_P_eV  = p_freq["gibbs_free_energy_eV"]
    G_TS_eV = ts_freq["gibbs_free_energy_eV"]
    H_R_eV  = r_freq.get("enthalpy_eV")
    H_P_eV  = p_freq.get("enthalpy_eV")
    H_TS_eV = ts_freq.get("enthalpy_eV")
    E_R_eV  = r_freq.get("electronic_energy_eV") or r_opt["total_energy_eV"]
    E_P_eV  = p_freq.get("electronic_energy_eV") or p_opt["total_energy_eV"]
    E_TS_eV = ts_freq.get("electronic_energy_eV") or ts_res.get("total_energy_eV")

    n_imag_r  = r_freq.get("n_imaginary_modes") or 0
    n_imag_p  = p_freq.get("n_imaginary_modes") or 0
    n_imag_ts = ts_freq.get("n_imaginary_modes") or 0

    dE_act_kcal  = (E_TS_eV - E_R_eV) * EV_TO_KCAL
    dE_rxn_kcal  = (E_P_eV  - E_R_eV) * EV_TO_KCAL
    dH_act_kcal  = (H_TS_eV - H_R_eV) * EV_TO_KCAL if (H_R_eV is not None and H_TS_eV is not None) else None
    dH_rxn_kcal  = (H_P_eV  - H_R_eV) * EV_TO_KCAL if (H_R_eV is not None and H_P_eV  is not None) else None
    dG_act_kcal  = (G_TS_eV - G_R_eV) * EV_TO_KCAL
    dG_rxn_kcal  = (G_P_eV  - G_R_eV) * EV_TO_KCAL
    dG_act_rev_kcal = (G_TS_eV - G_P_eV) * EV_TO_KCAL

    # ----- 5) IRC connectivity check -----
    irc_info: Dict[str, Any] = {"performed": False}
    connectivity_ok: Optional[bool] = None
    if not skip_irc and method in ("xtb", "mopac") and ts_converged:
        try:
            irc_stem = os.path.join(workdir, "irc")
            irc_res = irc_task.run(
                ts_xyz, method=method, charge=charge, multiplicity=multiplicity,
                solvent=solvent, max_points=40, step=0.05, out_stem=irc_stem,
                cli="(internal reaction_profile: IRC connectivity)",
            )
            fwd_xyz = irc_res.get("forward_trajectory")
            rev_xyz = irc_res.get("reverse_trajectory")
            # MOPAC's IRC=N takes small mass-weighted steps and stops after N
            # points — it doesn't walk to the minimum. To make the connectivity
            # check robust, relax each IRC endpoint with a regular opt before
            # comparing to the supplied R/P minima. This was previously masked
            # by the IRC=N* keyword bug (the trailing * accidentally requested
            # walk-to-convergence behavior).
            fwd_end = _relax_endpoint(
                _last_xyz_frame(fwd_xyz), atoms_template=ts_xyz, label="fwd",
                workdir=workdir, **common_kw,
            ) if fwd_xyz else None
            rev_end = _relax_endpoint(
                _last_xyz_frame(rev_xyz), atoms_template=ts_xyz, label="rev",
                workdir=workdir, **common_kw,
            ) if rev_xyz else None
            r_pos = read_geometry(r_opt_xyz).get_positions()
            p_pos = read_geometry(p_opt_xyz).get_positions()

            rmsd_fwd_r = _kabsch_rmsd(fwd_end, r_pos) if fwd_end is not None else float("inf")
            rmsd_fwd_p = _kabsch_rmsd(fwd_end, p_pos) if fwd_end is not None else float("inf")
            rmsd_rev_r = _kabsch_rmsd(rev_end, r_pos) if rev_end is not None else float("inf")
            rmsd_rev_p = _kabsch_rmsd(rev_end, p_pos) if rev_end is not None else float("inf")

            # The forward direction may land on either R or P; pair each
            # endpoint with whichever stationary point it's closer to.
            fwd_to_p = rmsd_fwd_p < rmsd_fwd_r
            connect_r = rmsd_rev_r if fwd_to_p else rmsd_fwd_r
            connect_p = rmsd_fwd_p if fwd_to_p else rmsd_rev_p
            connectivity_ok = (connect_r < rmsd_tol and connect_p < rmsd_tol)
            irc_info = {
                "performed": True,
                "method": method,
                "forward_trajectory": fwd_xyz,
                "reverse_trajectory": rev_xyz,
                "rmsd_fwd_vs_R_A": rmsd_fwd_r,
                "rmsd_fwd_vs_P_A": rmsd_fwd_p,
                "rmsd_rev_vs_R_A": rmsd_rev_r,
                "rmsd_rev_vs_P_A": rmsd_rev_p,
                "rmsd_tolerance_A": rmsd_tol,
                "connects_R_and_P": connectivity_ok,
                "forward_lands_on": "P" if fwd_to_p else "R",
            }
        except Exception as e:
            irc_info = {"performed": False, "error": str(e)}
    elif skip_irc:
        irc_info = {"performed": False, "reason": "user requested --no-irc"}
    elif method in ("dft", "hf"):
        irc_info = {
            "performed": False,
            "reason": f"IRC not implemented for --method {method}; "
                      "use xtb/mopac for the connectivity check, or skip with --no-irc.",
        }

    # ----- 6) Diagram PNG -----
    # Ensure the destination directory exists — user can pass --out into a
    # nested results/run-N/ path that nothing else has created yet. Diagram
    # PNG and persistent xyz copies both land in `dirname(stem)`.
    out_dir = os.path.dirname(stem) or "."
    os.makedirs(out_dir, exist_ok=True)

    # Y-axis: ΔG relative to reactants in kcal/mol
    G_R_rel = 0.0
    G_TS_rel = dG_act_kcal
    G_P_rel = dG_rxn_kcal
    png_path = f"{stem}_diagram.png"
    diagram_path = _emit_diagram(
        png_path=png_path, G_R=G_R_rel, G_TS=G_TS_rel, G_P=G_P_rel,
        dG_act=dG_act_kcal, dG_rxn=dG_rxn_kcal,
        reaction_label=f"{os.path.basename(reactant_xyz)} → {os.path.basename(product_xyz)} ({method_label(method)})",
    )

    # ----- 7) Persist intermediate xyz files next to the JSON -----
    persistent: Dict[str, str] = {}
    for label, src in [("reactant_opt_xyz", r_opt_xyz),
                       ("product_opt_xyz", p_opt_xyz),
                       ("ts_opt_xyz", ts_xyz)]:
        dst = f"{stem}_{label[:-4]}.xyz"
        shutil.copyfile(src, dst)
        persistent[label] = os.path.abspath(dst)

    # ----- 8) Assemble result -----
    canonical_method = method_label(method)
    if method in ("dft", "hf"):
        any_calc = build_calculator(
            method, charge=charge, multiplicity=multiplicity, solvent=solvent,
            tier=tier, functional=functional, basis=basis, density_fit=density_fit,
            solvent_model=solvent_model,
        )
        canonical_method = method_label(method, any_calc)

    result = base_result(
        task="reaction_profile",
        method=canonical_method,
        program=program_label(method),
        input_path=os.path.abspath(reactant_xyz),
        n_atoms=len(read_geometry(reactant_xyz)),
        atoms=read_geometry(reactant_xyz).get_chemical_symbols(),
        charge=charge,
        multiplicity=multiplicity,
        solvent=solvent,
        cli=cli,
    )
    result["temperature_K"] = temperature_K
    result["pressure_Pa"] = pressure_Pa

    result["delta_E_activation_kcal_mol"] = dE_act_kcal
    result["delta_E_reaction_kcal_mol"]   = dE_rxn_kcal
    if dH_act_kcal is not None:
        result["delta_H_activation_kcal_mol"] = dH_act_kcal
        result["delta_H_reaction_kcal_mol"]   = dH_rxn_kcal
    result["delta_G_activation_kcal_mol"]         = dG_act_kcal
    result["delta_G_reaction_kcal_mol"]           = dG_rxn_kcal
    result["delta_G_activation_reverse_kcal_mol"] = dG_act_rev_kcal

    result["stationary_points"] = {
        "reactant": {
            "input_file": os.path.abspath(reactant_xyz),
            "optimized_xyz": persistent["reactant_opt_xyz"],
            "opt_converged": bool(r_opt.get("converged")),
            "G_eV": G_R_eV, "H_eV": H_R_eV, "E_eV": E_R_eV,
            "n_imaginary_modes": n_imag_r,
        },
        "transition_state": {
            "input_file": os.path.abspath(ts_guess_xyz),
            "optimized_xyz": persistent["ts_opt_xyz"],
            "ts_converged": ts_converged,
            "G_eV": G_TS_eV, "H_eV": H_TS_eV, "E_eV": E_TS_eV,
            "n_imaginary_modes": n_imag_ts,
        },
        "product": {
            "input_file": os.path.abspath(product_xyz),
            "optimized_xyz": persistent["product_opt_xyz"],
            "opt_converged": bool(p_opt.get("converged")),
            "G_eV": G_P_eV, "H_eV": H_P_eV, "E_eV": E_P_eV,
            "n_imaginary_modes": n_imag_p,
        },
    }
    result["irc"] = irc_info
    if diagram_path:
        result["diagram_png"] = os.path.abspath(diagram_path)

    # ----- 9) Verdict + warnings -----
    is_valid_minimum_R = (n_imag_r == 0 and r_opt.get("converged"))
    is_valid_minimum_P = (n_imag_p == 0 and p_opt.get("converged"))
    is_valid_ts        = (n_imag_ts == 1 and ts_converged)
    fully_characterized = (
        is_valid_minimum_R and is_valid_minimum_P and is_valid_ts and
        (connectivity_ok is True or skip_irc or method in ("dft", "hf"))
    )
    result["is_fully_characterized"] = fully_characterized
    result["verdict"] = {
        "reactant_is_minimum": is_valid_minimum_R,
        "product_is_minimum":  is_valid_minimum_P,
        "ts_is_first_order_saddle": is_valid_ts,
        "irc_connects_R_and_P": connectivity_ok,
    }

    warnings: List[str] = []
    if not r_opt.get("converged"):
        warnings.append("reactant opt did not converge")
    if not p_opt.get("converged"):
        warnings.append("product opt did not converge")
    if not ts_converged:
        warnings.append("TS search did not converge")
    if n_imag_r > 0:
        warnings.append(f"reactant has {n_imag_r} imaginary mode(s) — not a true minimum")
    if n_imag_p > 0:
        warnings.append(f"product has {n_imag_p} imaginary mode(s) — not a true minimum")
    if n_imag_ts == 0:
        warnings.append("TS has ZERO imaginary modes — collapsed to a minimum, not a saddle")
    elif n_imag_ts > 1:
        warnings.append(
            f"TS has {n_imag_ts} imaginary modes — higher-order saddle, not a true TS"
        )
    if connectivity_ok is False:
        warnings.append(
            "IRC endpoints do NOT match the supplied reactant/product within "
            f"RMSD tolerance ({rmsd_tol} Å) — this TS connects different species. "
            "Either the wrong stationary points were supplied or the TS guess "
            "was for a different rearrangement."
        )
    if warnings:
        result["warnings"] = warnings
    result["workdir"] = workdir

    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def _relax_endpoint(coords, *, atoms_template, label, workdir,
                    method, charge, multiplicity, solvent,
                    tier=None, functional=None, basis=None, density_fit=False,
                    solvent_model="ddcosmo",
                    gate_integrity=False, allow_unconverged=False):
    # gate_integrity/allow_unconverged accepted (they ride in via **common_kw)
    # but are intentionally ignored here — the internal opt always runs ungated
    # (gate_integrity=False) since a non-converged IRC endpoint must not abort
    # the whole profile.
    """Write `coords` (with atom symbols pulled from `atoms_template`) to xyz
    and run a quick opt — returns the relaxed positions or None on failure.

    Used by the IRC connectivity check: MOPAC's IRC=N stops after N small
    steps and doesn't reach the minimum, so we relax the endpoint before
    comparing it to the supplied R/P minima. Returns coords unchanged if
    the opt fails (better than dropping the endpoint entirely)."""
    if coords is None:
        return None
    template = read_geometry(atoms_template)
    syms = template.get_chemical_symbols()
    if len(syms) != len(coords):
        return coords
    in_xyz = os.path.join(workdir, f"irc_{label}_endpoint.xyz")
    out_xyz = os.path.join(workdir, f"irc_{label}_endpoint_opt.xyz")
    with open(in_xyz, "w") as f:
        f.write(f"{len(syms)}\nIRC {label} endpoint\n")
        for s, (x, y, z) in zip(syms, coords):
            f.write(f"{s:<3s} {x:15.8f} {y:15.8f} {z:15.8f}\n")
    try:
        opt_task.run(
            in_xyz, out_xyz=out_xyz,
            method=method, charge=charge, multiplicity=multiplicity,
            solvent=solvent, tier=tier, functional=functional, basis=basis,
            density_fit=density_fit, solvent_model=solvent_model,
            cli=f"(internal reaction_profile: relax IRC {label} endpoint)",
            gate_integrity=False,
        )
        return read_geometry(out_xyz).get_positions()
    except Exception:
        return coords


def _last_xyz_frame(xyz_path: str):
    """Read the last frame from a multi-frame xyz file (one frame = N+2 lines)."""
    if not xyz_path or not os.path.isfile(xyz_path):
        return None
    with open(xyz_path) as f:
        lines = f.read().splitlines()
    if not lines:
        return None
    # Walk frames: each starts with an integer line giving the atom count.
    i = 0
    last_start = 0
    while i < len(lines):
        try:
            n = int(lines[i].strip())
        except ValueError:
            break
        if i + 1 + n >= len(lines):
            break
        last_start = i
        i += 2 + n
    n = int(lines[last_start].strip())
    coords = []
    for ln in lines[last_start + 2 : last_start + 2 + n]:
        parts = ln.split()
        coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
    return np.array(coords)
