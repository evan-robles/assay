"""`chemkit` command-line interface."""
from __future__ import annotations
import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .io import write_result, cli_invocation


def _add_common(p):
    p.add_argument("input", help="Path to input geometry (.xyz, .sdf, .pdb).")
    p.add_argument("--method", choices=["xtb", "mopac"], required=True)
    p.add_argument("--charge", type=int, default=0)
    p.add_argument("--mult", "--multiplicity", dest="multiplicity",
                   type=int, default=1, help="Spin multiplicity 2S+1 (default 1).")
    p.add_argument("--solvent", default=None,
                   help="Implicit solvent (e.g. water, methanol, dmso). Gas phase if omitted.")
    p.add_argument("--out", default=None,
                   help="Output JSON path. Default: <input-stem>_<task>_<method>.json")


def _default_out(input_path: str, task: str, method: str) -> str:
    stem = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.abspath(f"{stem}_{task}_{method}.json")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chemkit",
        description="ASE-based computational chemistry suite (xtb, MOPAC).",
    )
    parser.add_argument("--version", action="version", version=f"chemkit {__version__}")
    sub = parser.add_subparsers(dest="task", required=True)

    p_sp = sub.add_parser("sp", help="Single-point energy.")
    _add_common(p_sp)

    p_opt = sub.add_parser("opt", help="Geometry optimization.")
    _add_common(p_opt)
    p_opt.add_argument("--fmax", type=float, default=0.05,
                       help="Force convergence threshold in eV/Å (default 0.05).")
    p_opt.add_argument("--steps", type=int, default=500)
    p_opt.add_argument("--xyz-out", default=None,
                       help="Optimized geometry destination. Default: <stem>_<method>_opt.xyz")

    p_freq = sub.add_parser("freq", help="Opt-freq: optimize then vibrational analysis + thermochemistry.")
    _add_common(p_freq)
    p_freq.add_argument("--temperature", type=float, default=298.15)
    p_freq.add_argument("--pressure", type=float, default=101325.0)
    p_freq.add_argument("--geometry", choices=["monatomic", "linear", "nonlinear"],
                        default="nonlinear")
    p_freq.add_argument("--symmetry", type=int, default=1,
                        help="Rotational symmetry number σ (default 1).")
    p_freq.add_argument(
        "--no-preopt", dest="preopt", action="store_false", default=True,
        help="Skip the automatic pre-optimization step. By default freq always "
             "optimizes the input geometry first so the Hessian is taken at a "
             "true stationary point.",
    )
    p_freq.add_argument(
        "--preopt-fmax", type=float, default=0.01,
        help="Force convergence (eV/Å) for the pre-opt step (default 0.01, "
             "tighter than `opt`'s 0.05 because residual forces propagate into "
             "near-zero imaginary modes).",
    )

    p_bind = sub.add_parser("binding", help="Binding/interaction energy.")
    _add_common(p_bind)
    p_bind.add_argument("--monomer", action="append", required=True,
                        help="Path to a monomer geometry. Repeat for each fragment.")
    p_bind.add_argument("--monomer-charge", action="append", type=int, default=None)
    p_bind.add_argument("--monomer-mult", action="append", type=int, default=None)

    p_redox = sub.add_parser("redox", help="Redox potential vs SHE / Ag-AgCl / Fc+-Fc.")
    _add_common(p_redox)
    p_redox.add_argument("--ox-charge", type=int, required=True)
    p_redox.add_argument("--red-charge", type=int, required=True)
    p_redox.add_argument("--ox-mult", type=int, default=1)
    p_redox.add_argument("--red-mult", type=int, default=2)
    p_redox.add_argument("--ref", choices=["SHE", "Ag/AgCl", "Fc+/Fc"], default="SHE")
    p_redox.add_argument("--n-electrons", type=int, default=1)

    p_conf = sub.add_parser("confsearch", help="Conformer search via CREST.")
    _add_common(p_conf)
    p_conf.add_argument("--max-conformers", type=int, default=20)
    p_conf.add_argument(
        "--postopt", choices=["none", "mopac"], default="mopac",
        help=(
            "Re-optimize CREST conformers with another method to recover "
            "shallow minima that GFN2-xTB smooths over. 'mopac' uses PM7 "
            "(default). Pass 'none' to skip."
        ),
    )
    p_conf.add_argument(
        "--postopt-rmsd", type=float, default=0.25,
        help="RMSD threshold (Å) for deduping post-optimized conformers (default 0.25).",
    )
    p_conf.add_argument(
        "--postopt-ewin", type=float, default=6.0,
        help="Energy window (kcal/mol) to keep after post-optimization (default 6.0).",
    )

    p_scan = sub.add_parser(
        "scan", help="Relaxed dihedral scan (torsional energy profile).",
    )
    _add_common(p_scan)
    p_scan.add_argument(
        "--dihedral", default=None,
        help="Comma-separated 1-based atom indices i,j,k,l defining the dihedral "
             "to scan (matches the C1, C2, ... labels in plots and filenames). "
             "If omitted, auto-detects all non-methyl, non-ring rotatable bonds "
             "and scans each.",
    )
    p_scan.add_argument(
        "--steps", type=int, default=24,
        help="Number of points around 360° (default 24 = 15° resolution).",
    )
    p_scan.add_argument(
        "--fmax", type=float, default=0.05,
        help="Per-step force convergence (eV/Å, default 0.05).",
    )
    p_scan.add_argument(
        "--opt-steps", type=int, default=200,
        help="Max optimizer iterations per scan point (default 200).",
    )

    args = parser.parse_args(argv)
    cli = cli_invocation()

    if args.task == "sp":
        from .tasks import sp
        result = sp.run(args.input, method=args.method, charge=args.charge,
                        multiplicity=args.multiplicity, solvent=args.solvent, cli=cli)
    elif args.task == "opt":
        from .tasks import opt
        result = opt.run(args.input, method=args.method, charge=args.charge,
                         multiplicity=args.multiplicity, solvent=args.solvent,
                         fmax=args.fmax, steps=args.steps, out_xyz=args.xyz_out,
                         cli=cli)
    elif args.task == "freq":
        from .tasks import freq
        result = freq.run(args.input, method=args.method, charge=args.charge,
                          multiplicity=args.multiplicity, solvent=args.solvent,
                          temperature_K=args.temperature, pressure_Pa=args.pressure,
                          geometry=args.geometry, symmetrynumber=args.symmetry,
                          preopt=args.preopt, preopt_fmax=args.preopt_fmax,
                          cli=cli)
    elif args.task == "binding":
        from .tasks import binding
        result = binding.run(args.input, args.monomer, method=args.method,
                             charge=args.charge, multiplicity=args.multiplicity,
                             solvent=args.solvent,
                             monomer_charges=args.monomer_charge,
                             monomer_multiplicities=args.monomer_mult, cli=cli)
    elif args.task == "redox":
        from .tasks import redox
        result = redox.run(args.input, method=args.method,
                           oxidized_charge=args.ox_charge,
                           reduced_charge=args.red_charge,
                           oxidized_multiplicity=args.ox_mult,
                           reduced_multiplicity=args.red_mult,
                           solvent=args.solvent, reference=args.ref,
                           n_electrons=args.n_electrons, cli=cli)
    elif args.task == "confsearch":
        from .tasks import confsearch
        result = confsearch.run(
            args.input, method=args.method, solvent=args.solvent,
            n_max_conformers=args.max_conformers,
            postopt=args.postopt,
            postopt_rmsd=args.postopt_rmsd,
            postopt_ewin=args.postopt_ewin,
            charge=args.charge, multiplicity=args.multiplicity,
            cli=cli,
        )
    elif args.task == "scan":
        from .tasks import scan
        dihedral_tuple = None
        if args.dihedral:
            parts = [p.strip() for p in args.dihedral.split(",")]
            if len(parts) != 4:
                parser.error("--dihedral must be 4 comma-separated atom indices")
            try:
                one_based = tuple(int(p) for p in parts)
            except ValueError:
                parser.error("--dihedral atom indices must be integers")
            if any(k < 1 for k in one_based):
                parser.error("--dihedral atom indices are 1-based (must be >= 1)")
            dihedral_tuple = tuple(k - 1 for k in one_based)
        # Compute the JSON path early so scan.run can place its auxiliary
        # files (xyz / png / out) with a matching stem.
        out_path_pre = args.out or _default_out(args.input, args.task, args.method)
        out_stem = os.path.splitext(out_path_pre)[0]
        result = scan.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            dihedral=dihedral_tuple, n_steps=args.steps,
            fmax=args.fmax, opt_steps=args.opt_steps,
            out_stem=out_stem, cli=cli,
        )
    else:
        parser.error(f"Unknown task {args.task!r}")
        return 2

    out_path = args.out or _default_out(args.input, args.task, args.method)
    write_result(result, out_path)

    # For confsearch, also write the full conformer ensemble as an XYZ next
    # to the JSON so downstream tools have it without digging into tmp.
    if args.task == "confsearch":
        import shutil
        stem = os.path.splitext(out_path)[0]
        ensemble_dst = f"{stem}_conformers.xyz"
        ensemble_src = None
        post = result.get("postopt")
        if post and post.get("ensemble_xyz") and os.path.isfile(post["ensemble_xyz"]):
            ensemble_src = post["ensemble_xyz"]
        elif result.get("all_conformers_xyz") and os.path.isfile(result["all_conformers_xyz"]):
            ensemble_src = result["all_conformers_xyz"]
        if ensemble_src:
            shutil.copyfile(ensemble_src, ensemble_dst)
            result["conformers_xyz"] = os.path.abspath(ensemble_dst)
            # Rewrite the JSON so it records the persistent xyz path.
            write_result(result, out_path)

    print(json.dumps(result, indent=2, default=str))
    print(f"\n# result written to: {out_path}", file=sys.stderr)
    if args.task == "confsearch" and result.get("conformers_xyz"):
        print(f"# conformers xyz written to: {result['conformers_xyz']}", file=sys.stderr)
    if args.task == "scan":
        for d in result.get("dihedrals", []):
            for k in ("trajectory_xyz", "plot_png"):
                if d.get(k):
                    print(f"# {k}: {d[k]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
