"""I/O helpers: read molecular geometry; write structured JSON result files."""
from __future__ import annotations
import json
import os
import sys
from typing import Any, Dict

from ase.io import read as ase_read


def read_geometry(path: str):
    """Read xyz/sdf/pdb (anything ASE recognizes) and return an Atoms object."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Geometry file not found: {path}")
    atoms = ase_read(path)
    return atoms


def write_result(result: Dict[str, Any], out_path: str) -> str:
    """Write result dict to JSON; create parent dir if missing. Returns abs path."""
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=_default_json)
    return out_path


def _default_json(o):
    try:
        import numpy as np
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
    except ImportError:
        pass
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"Not JSON-serializable: {type(o).__name__}")


def cli_invocation() -> str:
    """Reconstruct the command line that produced this run (for reproducibility)."""
    return " ".join(sys.argv)


_EV_TO_KCAL = 23.060541945329334


def write_summary(result: Dict[str, Any], json_path: str) -> str:
    """Write a fixed-width human-readable summary of `result` next to the JSON.
    Returns the summary path. The JSON remains the canonical record; this is a
    `cat`-able companion for humans skimming results."""
    summary_path = os.path.splitext(os.path.abspath(json_path))[0] + "_summary.txt"
    task = result.get("task", "")
    lines = []

    def hdr(s):
        lines.append("=" * 78)
        lines.append(s)
        lines.append("=" * 78)

    def kv(label, value, width=22):
        lines.append(f"  {label:<{width}}: {value}")

    hdr(f"{result.get('method', '?')}  —  {task}")
    kv("input", result.get("input_file", "?"))
    if result.get("solvent"):
        kv("solvent", result["solvent"])
    if result.get("charge") is not None or result.get("multiplicity") is not None:
        kv("charge / mult", f"{result.get('charge', 0)} / {result.get('multiplicity', 1)}")
    kv("n_atoms", result.get("n_atoms", "?"))
    lines.append("")

    if task == "single_point_energy":
        e_eV = result.get("electronic_energy_eV")
        if e_eV is not None:
            kv("E_elec  (eV)", f"{e_eV:>16.6f}")
            kv("E_elec  (kcal/mol)", f"{e_eV * _EV_TO_KCAL:>16.4f}")
        if result.get("heat_of_formation_kcal_mol") is not None:
            kv("HoF (kcal/mol)", f"{result['heat_of_formation_kcal_mol']:>16.4f}")

    elif task == "geometry_optimization":
        kv("converged", result.get("converged"))
        kv("opt_steps", result.get("n_optimization_steps"))
        e_eV = result.get("final_energy_eV")
        if e_eV is not None:
            kv("E_final (eV)", f"{e_eV:>16.6f}")
            kv("E_final (kcal/mol)", f"{e_eV * _EV_TO_KCAL:>16.4f}")
        if result.get("heat_of_formation_kcal_mol") is not None:
            kv("HoF (kcal/mol)", f"{result['heat_of_formation_kcal_mol']:>16.4f}")
        if result.get("optimized_xyz"):
            kv("optimized xyz", result["optimized_xyz"])

    elif task == "vibrational_thermochemistry":
        kv("T (K)", result.get("temperature_K"))
        kv("P (Pa)", result.get("pressure_Pa"))
        kv("symmetry (σ)", result.get("symmetry_number"))
        kv("geometry", result.get("geometry"))
        lines.append("")
        e_eV = result.get("electronic_energy_eV")
        zpe_eV = result.get("zpe_eV")
        h_eV = result.get("enthalpy_eV")
        s_eVK = result.get("entropy_eV_per_K")
        g_eV = result.get("gibbs_free_energy_eV")
        lines.append(f"  {'Quantity':<22}  {'eV':>16}  {'kcal/mol':>16}")
        lines.append(f"  {'-'*22}  {'-'*16}  {'-'*16}")
        if e_eV is not None:
            lines.append(f"  {'E_elec':<22}  {e_eV:>16.6f}  {e_eV*_EV_TO_KCAL:>16.4f}")
        if zpe_eV is not None:
            lines.append(f"  {'ZPE':<22}  {zpe_eV:>16.6f}  {zpe_eV*_EV_TO_KCAL:>16.4f}")
        if h_eV is not None:
            lines.append(f"  {'H(T)':<22}  {h_eV:>16.6f}  {h_eV*_EV_TO_KCAL:>16.4f}")
        if g_eV is not None:
            lines.append(f"  {'G(T)':<22}  {g_eV:>16.6f}  {g_eV*_EV_TO_KCAL:>16.4f}")
        if s_eVK is not None:
            lines.append(f"  {'S(T)':<22}  {s_eVK*1000:>16.4f}  "
                         f"{s_eVK*_EV_TO_KCAL*1000:>16.4f}    "
                         "(meV/K | cal/mol/K)")
        lines.append("")
        kv("n_real_vib_modes", result.get("n_real_vib_modes"))
        kv("n_imaginary_modes", result.get("n_imaginary_modes"))
        freqs = result.get("vibrational_frequencies_cm-1") or []
        real = sorted(f for f in freqs if f > 0)
        if real:
            top = ", ".join(f"{f:.1f}" for f in real[-5:])
            low = ", ".join(f"{f:.1f}" for f in real[:5])
            kv("lowest 5 (cm⁻¹)", low)
            kv("highest 5 (cm⁻¹)", top)
        if result.get("n_imaginary_modes", 0) > 0:
            lines.append("  WARNING: imaginary modes present — saddle point, not minimum.")

    elif task == "conformational_analysis":
        kv("n_dihedrals_scanned", result.get("n_dihedrals_scanned"))
        lines.append("")
        for dh in result.get("dihedrals", []):
            atoms = dh.get("atoms_1based") or dh.get("atoms") or []
            atom_str = "–".join(str(a) for a in atoms)
            lines.append(f"  dihedral atoms (1-based): {atom_str}")
            if "barrier_kcal_mol" in dh:
                lines.append(f"    barrier         : {dh['barrier_kcal_mol']:>9.3f} kcal/mol")
            if "min_angle_deg" in dh:
                lines.append(f"    min angle       : {dh['min_angle_deg']:>9.1f}°")
            if "max_angle_deg" in dh:
                lines.append(f"    max angle       : {dh['max_angle_deg']:>9.1f}°")
            lines.append(f"    converged       : {dh.get('n_converged', '?')} / {dh.get('n_points', '?')}")
            if dh.get("plot_png"):
                lines.append(f"    plot            : {dh['plot_png']}")
            if dh.get("table_out"):
                lines.append(f"    table           : {dh['table_out']}")
            if dh.get("trajectory_xyz"):
                lines.append(f"    trajectory      : {dh['trajectory_xyz']}")
            lines.append("")

    elif task == "conformational_search":
        kv("n_conformers_found", result.get("n_conformers_found"))
        kv("n_conformers_kept", result.get("n_conformers_kept"))
        rels = result.get("conformer_relative_energies_kcal_mol") or []
        if rels:
            shown = ", ".join(f"{e:.2f}" for e in rels[:10])
            kv("ΔE (kcal/mol, first 10)", shown)
        if result.get("preoptimization"):
            lines.append(f"  note: {result['preoptimization']}")
        if result.get("conformers_xyz"):
            kv("conformers xyz", result["conformers_xyz"])

    elif task == "binding_energy":
        if "binding_energy_kcal_mol" in result:
            kv("ΔE_bind (kcal/mol)", f"{result['binding_energy_kcal_mol']:>10.4f}")
        if "complex_energy_eV" in result:
            kv("E_complex (eV)", f"{result['complex_energy_eV']:>16.6f}")

    elif task == "redox_potential":
        for key in ("E_redox_V_vs_SHE", "E_redox_V_vs_Ag_AgCl", "E_redox_V_vs_Fc"):
            if key in result:
                kv(key, f"{result[key]:>10.4f}")

    else:
        kv("(no formatter)", task)

    lines.append("")
    lines.append(f"json: {os.path.abspath(json_path)}")
    if result.get("cli_invocation"):
        lines.append(f"cli : {result['cli_invocation']}")

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return summary_path
