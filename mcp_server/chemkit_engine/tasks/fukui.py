"""Condensed Fukui functions + dual descriptor (atom-resolved reactivity).

Approach: three single-point partial-charge calculations on the SAME geometry,
differing only in electron count.

  N electrons:    q_N        (neutral reference, mult M)
  N-1 electrons:  q_{N-1}    (cation, charge += 1, mult typically M+1)
  N+1 electrons:  q_{N+1}    (anion,  charge -= 1, mult typically M+1)

Condensed Fukui per atom k (Yang/Mortier, finite-difference):
  f+_k = q_k(N) − q_k(N+1)     electrophilic   (attacked by nucleophiles)
  f-_k = q_k(N-1) − q_k(N)     nucleophilic    (attacked by electrophiles)
  f0_k = ½(f+_k + f-_k)         radical attack
  dual_k = f+_k − f-_k          Morell dual descriptor
                                 (positive → electrophilic site;
                                  negative → nucleophilic site)

Composes directly with chemkit.tasks.electrostatics, which already produces
Mulliken partial charges via either backend.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional

from . import electrostatics
from ..io import read_geometry
from ..schema import base_result, element_warnings


def run(
    input_path: str,
    *,
    method: str,
    charge: int = 0,
    multiplicity: int = 1,
    solvent: Optional[str] = None,
    cation_mult: Optional[int] = None,
    anion_mult: Optional[int] = None,
    plot: bool = True,
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
    """Three partial-charge SPs (N, N+1, N-1) → condensed Fukui + dual descriptor."""
    atoms = read_geometry(input_path)
    symbols = atoms.get_chemical_symbols()

    # When the user doesn't specify cation/anion mult, derive it from parent:
    # changing N by ±1 must flip spin parity. The natural default is the
    # lower of the two parity-flipped options, i.e. M+1 for a singlet parent
    # (singlet → doublet) and M-1 for any higher-spin parent (triplet → doublet,
    # quartet → triplet, …). Users with non-default spin states (e.g. O2 triplet
    # whose cation/anion are doublet OR quartet) should override explicitly.
    cation_mult_was_default = cation_mult is None
    anion_mult_was_default = anion_mult is None
    if cation_mult is None:
        cation_mult = multiplicity + 1 if multiplicity == 1 else multiplicity - 1
    if anion_mult is None:
        anion_mult = multiplicity + 1 if multiplicity == 1 else multiplicity - 1
    # Spin-parity sanity check — any user-supplied combination must keep parity right.
    if (cation_mult - multiplicity) % 2 != 1:
        raise ValueError(
            f"cation_mult ({cation_mult}) and parent multiplicity ({multiplicity}) "
            "have the same spin parity — impossible after removing one electron. "
            "Spin parity must flip; use multiplicity ± 1."
        )
    if (anion_mult - multiplicity) % 2 != 1:
        raise ValueError(
            f"anion_mult ({anion_mult}) and parent multiplicity ({multiplicity}) "
            "have the same spin parity — impossible after adding one electron. "
            "Spin parity must flip; use multiplicity ± 1."
        )

    # Sub-calls stamp their own integrity but never raise; fukui gates its own
    # result (the Σf± charge-conservation check proxies for a failed N±1 state).
    es_kwargs = dict(tier=tier, functional=functional, basis=basis,
                     density_fit=density_fit, solvent_model=solvent_model,
                     gate_integrity=False)
    neutral = electrostatics.run(
        input_path, method=method, charge=charge,
        multiplicity=multiplicity, solvent=solvent, cli=cli, **es_kwargs,
    )
    cation = electrostatics.run(
        input_path, method=method, charge=charge + 1,
        multiplicity=cation_mult, solvent=solvent, cli=cli, **es_kwargs,
    )
    anion = electrostatics.run(
        input_path, method=method, charge=charge - 1,
        multiplicity=anion_mult, solvent=solvent, cli=cli, **es_kwargs,
    )

    q_N  = neutral.get("partial_charges")
    q_Nm = cation.get("partial_charges")
    q_Np = anion.get("partial_charges")
    if not q_N or not q_Nm or not q_Np:
        raise RuntimeError(
            "fukui: at least one electrostatics call returned no partial charges. "
            f"neutral={bool(q_N)} cation={bool(q_Nm)} anion={bool(q_Np)}"
        )
    if not (len(q_N) == len(q_Nm) == len(q_Np) == len(symbols)):
        raise RuntimeError(
            "fukui: partial-charge arrays have inconsistent length: "
            f"neutral={len(q_N)} cation={len(q_Nm)} anion={len(q_Np)} "
            f"atoms={len(symbols)}"
        )

    f_plus  = [q_N[k]  - q_Np[k] for k in range(len(symbols))]
    f_minus = [q_Nm[k] - q_N[k]  for k in range(len(symbols))]
    f_zero  = [0.5 * (f_plus[k] + f_minus[k]) for k in range(len(symbols))]
    dual    = [f_plus[k] - f_minus[k] for k in range(len(symbols))]

    k_eplus = max(range(len(symbols)), key=lambda k: f_plus[k])
    k_eminus = max(range(len(symbols)), key=lambda k: f_minus[k])

    result = base_result(
        task="fukui",
        method=neutral["method"], program=method,
        input_path=os.path.abspath(input_path),
        n_atoms=len(atoms), atoms=symbols,
        charge=charge, multiplicity=multiplicity, solvent=solvent, cli=cli,
    )
    result["partial_charges_scheme"] = neutral.get("partial_charges_scheme")
    result["charges_neutral"] = q_N
    result["charges_cation"]  = q_Nm
    result["charges_anion"]   = q_Np
    result["cation_charge"] = charge + 1
    result["cation_multiplicity"] = cation_mult
    result["anion_charge"] = charge - 1
    result["anion_multiplicity"] = anion_mult
    result["fukui_plus"]  = f_plus
    result["fukui_minus"] = f_minus
    result["fukui_zero"]  = f_zero
    result["dual_descriptor"] = dual
    result["most_electrophilic"] = {
        "atom_index_0based": k_eplus,
        "atom_label_1based": k_eplus + 1,
        "symbol": symbols[k_eplus],
        "f_plus": f_plus[k_eplus],
    }
    result["most_nucleophilic"] = {
        "atom_index_0based": k_eminus,
        "atom_label_1based": k_eminus + 1,
        "symbol": symbols[k_eminus],
        "f_minus": f_minus[k_eminus],
    }

    warns = _validate(f_plus, f_minus, symbols)
    warns += element_warnings(symbols, method)
    if multiplicity > 1 and (cation_mult_was_default or anion_mult_was_default):
        warns.append(
            f"Parent multiplicity={multiplicity}; cation_mult/anion_mult defaulted "
            f"to {multiplicity - 1} (the lower parity-flipped option). For systems "
            "where the high-spin N±1 state is the ground state (e.g. O2 anion is "
            "doublet vs cation quartet), override --cation-mult / --anion-mult."
        )
    if warns:
        result["warnings"] = warns

    if plot and out_stem:
        png_path = f"{out_stem}.png"
        _write_plot(png_path, symbols, f_plus, f_minus, dual,
                    method=neutral["method"], input_path=input_path)
        if os.path.isfile(png_path):
            result["plot_png"] = os.path.abspath(png_path)

    from ..integrity import finalize
    return finalize(result, gate_integrity=gate_integrity,
                    allow_unconverged=allow_unconverged)


def _validate(f_plus: List[float], f_minus: List[float],
              symbols: List[str]) -> List[str]:
    """Sanity-check Fukui sums (charge conservation) and look for SCF issues."""
    sum_fp = sum(f_plus)
    sum_fm = sum(f_minus)
    warns: List[str] = []
    # Sum of condensed f+ (resp. f-) across atoms should be ≈ 1 by charge conservation.
    for name, total in (("f+", sum_fp), ("f-", sum_fm)):
        if abs(total - 1.0) > 0.05:
            warns.append(
                f"Σ {name}_k = {total:.3f} (expected ≈ 1.0). Drift > 0.05 usually "
                "means an SCF in one of the three states didn't converge cleanly, "
                "or the charges scheme isn't comparable across the trio. Treat "
                "atom-resolved values with care."
            )
    return warns


def _write_plot(
    path: str,
    symbols: List[str],
    f_plus: List[float],
    f_minus: List[float],
    dual: List[float],
    *,
    method: str,
    input_path: Optional[str] = None,
) -> None:
    """Grouped bar chart of f+/f-/dual per atom. No-op if matplotlib missing."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    n = len(symbols)
    if n == 0:
        return

    import numpy as np
    x = np.arange(n)
    width = 0.27

    fig, ax = plt.subplots(figsize=(max(7.0, 0.6 * n + 2), 4.5))
    ax.bar(x - width, f_plus,  width, label="f+ (electrophilic)", color="#d04848")
    ax.bar(x,         f_minus, width, label="f- (nucleophilic)",  color="#2e6fdf")
    ax.bar(x + width, dual,    width, label="dual (f+ − f-)",     color="#888888")

    xticklabels = [f"{sym}{k + 1}" for k, sym in enumerate(symbols)]
    ax.set_xticks(x)
    ax.set_xticklabels(xticklabels, rotation=0 if n <= 20 else 60,
                       ha="center" if n <= 20 else "right", fontsize=9)
    ax.set_ylabel("Fukui index")
    ax.axhline(0.0, color="black", linewidth=0.6, alpha=0.7)
    ax.grid(True, axis="y", alpha=0.3)

    method_label = method
    mol_name = (os.path.splitext(os.path.basename(input_path))[0]
                if input_path else "")
    title = f"Condensed Fukui functions — {method_label}"
    ax.set_title(f"{mol_name}\n{title}" if mol_name else title, fontsize=10)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
