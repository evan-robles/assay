"""`chemkit` command-line interface."""
from __future__ import annotations
import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .io import write_result, cli_invocation


def _add_chem_options(p, *, with_input: bool = True, with_solvent: bool = True):
    """Shared CLI options. Set `with_solvent=False` for tasks where the
    solvent is fixed by the task itself (e.g. logp pins water + octanol)."""
    if with_input:
        p.add_argument("input", help="Path to input geometry (.xyz, .sdf, .pdb).")
    p.add_argument("--method", choices=["xtb", "mopac", "dft", "hf"], required=True)
    p.add_argument("--charge", type=int, default=0)
    p.add_argument("--mult", "--multiplicity", dest="multiplicity",
                   type=int, default=1, help="Spin multiplicity 2S+1 (default 1).")
    if with_solvent:
        p.add_argument("--solvent", default=None,
                       help="Implicit solvent (e.g. water, methanol, dmso). Gas phase if omitted.")
    # PySCF-only knobs; silently ignored for xtb/mopac.
    p.add_argument("--tier", choices=["fast", "standard", "accurate"], default=None,
                   help="DFT tier preset (fast=r2SCAN/def2-SVP, standard=wB97X-V/def2-TZVP, "
                        "accurate=wB97M-V/def2-QZVPP). Ignored unless --method dft.")
    p.add_argument("--functional", default=None,
                   help="DFT functional override, libxc name (e.g. b3lyp, pbe0, wb97x_v, "
                        "wb97m_v, wb97x-d3bj). Ignored unless --method dft.")
    p.add_argument("--basis", default=None,
                   help="Basis-set override for DFT/HF (e.g. def2-tzvp, cc-pvtz). "
                        "Ignored unless --method dft or --method hf.")
    p.add_argument("--out", default=None,
                   help="Output JSON path. Default: <input-stem>_<task>_<method>.json")


def _add_common(p):
    """Back-compat shim — existing subparsers continue to use this."""
    _add_chem_options(p)


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
                        default=None,
                        help="Override molecular geometry (monatomic/linear/nonlinear). "
                             "If omitted, auto-detected from the input atoms.")
    p_freq.add_argument("--symmetry", type=int, default=None,
                        help="Rotational symmetry number σ. If omitted, defaults to "
                             "1 with a warning — look up σ for your point group "
                             "(H2O σ=2, NH3 σ=3, CH4/benzene σ=12) to avoid "
                             "overestimating rotational entropy by R·ln σ.")
    p_freq.add_argument(
        "--no-preopt", dest="preopt", action="store_false", default=True,
        help="Skip the automatic pre-optimization step. By default freq always "
             "optimizes the input geometry first so the Hessian is taken at a "
             "true stationary point.",
    )
    p_freq.add_argument(
        "--preopt-fmax", type=float, default=0.001,
        help="Force convergence (eV/Å) for the pre-opt step (default 0.01, "
             "tighter than `opt`'s 0.05 because residual forces propagate into "
             "near-zero imaginary modes).",
    )
    p_freq.add_argument(
        "--auto-confsearch", dest="auto_confsearch", action="store_true",
        default=False,
        help="Run an Open Babel conformer search (with PM7 postopt) before the "
             "freq step and take the lowest-energy minimum as the input geometry. "
             "Useful for flexible molecules where the user-supplied geometry "
             "may not be the global minimum; otherwise soft-mode saddles show "
             "up as spurious imaginary modes.",
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

    p_conf = sub.add_parser("confsearch", help="Conformer search via Open Babel (confab).")
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

    p_front = sub.add_parser(
        "frontier",
        help="Frontier molecular orbital energies + HOMO-LUMO gap (no opt).",
    )
    _add_common(p_front)
    p_front.add_argument(
        "--nfrontier", type=int, default=3,
        help="Number of occupied & virtual orbitals on each side of the gap "
             "to report (default 3).",
    )

    p_elst = sub.add_parser(
        "electrostatics",
        help="Dipole + atomic partial charges (single-point, no opt).",
    )
    _add_common(p_elst)

    p_solv = sub.add_parser(
        "solvation",
        help="ΔG_solv = E(solvated) − E(gas) at fixed geometry (electronic only).",
    )
    _add_common(p_solv)

    p_logp = sub.add_parser(
        "logp",
        help="logP from ΔG_solv(water) − ΔG_solv(octanol). Neutral species only.",
    )
    _add_chem_options(p_logp, with_solvent=False)

    p_prof = sub.add_parser(
        "profile",
        help="Reaction profile: opt(R) + opt(P) + TS search + freq×3 + IRC "
             "connectivity check + ΔE/ΔH/ΔG diagram PNG.",
    )
    p_prof.add_argument("--reactant", required=True, help="Reactant xyz.")
    p_prof.add_argument("--product", required=True, help="Product xyz.")
    p_prof.add_argument("--ts-guess", dest="ts_guess", required=True,
                        help="TS guess xyz (often from /conformational_analysis).")
    p_prof.add_argument(
        "--method", choices=["xtb", "mopac", "dft", "hf"], required=True,
        help="Same method is used for every species in the cycle.",
    )
    p_prof.add_argument("--charge", type=int, default=0)
    p_prof.add_argument("--mult", "--multiplicity", dest="multiplicity",
                        type=int, default=1)
    p_prof.add_argument("--solvent", default=None)
    p_prof.add_argument("--temperature", type=float, default=298.15)
    p_prof.add_argument("--pressure", type=float, default=101325.0)
    p_prof.add_argument(
        "--rmsd-tol", type=float, default=0.5,
        help="Å threshold for IRC-endpoint connectivity check (default 0.5).",
    )
    p_prof.add_argument(
        "--no-irc", dest="skip_irc", action="store_true", default=False,
        help="Skip the IRC connectivity check (only the RMSD-based check is "
             "available for dft/hf anyway, so this is a noop there).",
    )
    p_prof.add_argument("--tier", choices=["fast", "standard", "accurate"], default=None)
    p_prof.add_argument("--functional", default=None)
    p_prof.add_argument("--basis", default=None)
    p_prof.add_argument("--out", default=None)

    p_pka = sub.add_parser(
        "pka",
        help="pKa via thermodynamic cycle HA(aq) → A⁻(aq) + H⁺(aq). Requires "
             "BOTH the protonated and deprotonated xyz files.",
    )
    p_pka.add_argument("--ha", required=True, help="xyz of the protonated form (HA).")
    p_pka.add_argument("--a-minus", dest="a_minus", required=True,
                       help="xyz of the deprotonated form (A⁻).")
    p_pka.add_argument(
        "--method", choices=["xtb", "mopac", "dft", "hf"], required=True,
        help="Same method is applied to every species in the cycle.",
    )
    p_pka.add_argument(
        "--mode", choices=["absolute", "reference"], default="absolute",
        help="absolute: uses literature G(H+,aq). reference: uses a known acid "
             "(--ref-ha, --ref-a-minus, --pka-ref). Reference is far more accurate.",
    )
    p_pka.add_argument(
        "--solvent", default="water",
        help="Implicit solvent (default 'water' — required for the absolute "
             "G(H+) reference to apply).",
    )
    p_pka.add_argument("--ha-charge", type=int, default=0,
                       help="Charge of HA (default 0). A⁻ charge is HA charge − 1.")
    p_pka.add_argument("--ha-mult", type=int, default=1, help="HA multiplicity (default 1).")
    p_pka.add_argument("--a-minus-mult", type=int, default=1, help="A⁻ multiplicity (default 1).")
    p_pka.add_argument("--temperature", type=float, default=298.15)
    p_pka.add_argument("--pressure", type=float, default=101325.0)
    p_pka.add_argument(
        "--hplus-reference", default="tissandier_1998",
        choices=["tissandier_1998", "kelly_2006"],
        help="Source for G(H+,aq). Tissandier −270.28 kcal/mol (default); "
             "Kelly −265.9 kcal/mol shifts every pKa by ~1.4 units.",
    )
    # Reference-mode args
    p_pka.add_argument("--ref-ha", default=None, help="Reference acid HA xyz (reference mode).")
    p_pka.add_argument("--ref-a-minus", default=None, help="Reference base A⁻ xyz (reference mode).")
    p_pka.add_argument("--pka-ref", type=float, default=None,
                       help="Known experimental pKa of the reference acid (reference mode).")
    p_pka.add_argument("--ref-ha-charge", type=int, default=0)
    p_pka.add_argument("--ref-ha-mult", type=int, default=1)
    p_pka.add_argument("--ref-a-minus-mult", type=int, default=1)
    p_pka.add_argument("--tier", choices=["fast", "standard", "accurate"], default=None)
    p_pka.add_argument("--functional", default=None)
    p_pka.add_argument("--basis", default=None)
    p_pka.add_argument("--out", default=None)

    p_build = sub.add_parser(
        "build",
        help="Build a 3D xyz from a SMILES string OR a molecule name (Open Babel "
             "--gen3d; names are resolved online via PubChem/OPSIN/NIST).",
    )
    p_build.add_argument(
        "smiles",
        help="SMILES string (e.g. 'CCO') or a plain molecule name (e.g. "
             "'ethanol'). A name is resolved to SMILES online and the source "
             "is reported.",
    )
    p_build.add_argument(
        "--out-xyz", default=None,
        help="Destination .xyz path. Default: <input-sanitized>.xyz in cwd.",
    )
    p_build.add_argument(
        "--name", default=None,
        help="Title comment for the xyz (default: the SMILES string).",
    )
    p_build.add_argument(
        "--opt", dest="opt_method", choices=["xtb", "mopac", "dft", "hf"],
        default=None,
        help="Optional QM refinement step after the obabel build. Calls "
             "`chemkit opt` internally; the QM-relaxed xyz becomes the canonical "
             "output.",
    )
    p_build.add_argument(
        "--solvent", default=None,
        help="Implicit solvent for the optional QM step (ignored without --opt).",
    )
    p_build.add_argument(
        "--charge", type=int, default=None,
        help="Net charge forwarded to the QM step (default 0).",
    )
    p_build.add_argument(
        "--mult", "--multiplicity", dest="multiplicity", type=int, default=None,
        help="Spin multiplicity forwarded to the QM step (default 1).",
    )
    p_build.add_argument("--tier", choices=["fast", "standard", "accurate"], default=None)
    p_build.add_argument("--functional", default=None)
    p_build.add_argument("--basis", default=None)
    p_build.add_argument("--out", default=None, help="Result JSON path.")

    p_fukui = sub.add_parser(
        "fukui",
        help="Condensed Fukui functions + dual descriptor (atom-resolved reactivity).",
    )
    _add_common(p_fukui)
    p_fukui.add_argument(
        "--cation-mult", type=int, default=None,
        help="Multiplicity of the N-1 (cation) state. If omitted, derived from "
             "--mult: singlet parent → doublet (M+1), higher-spin parent → M-1. "
             "Override for systems where the high-spin N-1 is the ground state.",
    )
    p_fukui.add_argument(
        "--anion-mult", type=int, default=None,
        help="Multiplicity of the N+1 (anion) state. If omitted, derived from "
             "--mult: singlet parent → doublet (M+1), higher-spin parent → M-1.",
    )
    p_fukui.add_argument(
        "--no-plot", dest="plot", action="store_false", default=True,
        help="Skip the PNG bar chart of f+/f-/dual per atom.",
    )

    p_ts = sub.add_parser(
        "ts", help="Transition-state search (locate a first-order saddle).",
    )
    _add_common(p_ts)
    p_ts.add_argument(
        "--steps", type=int, default=500,
        help="Max optimizer iterations (default 500).",
    )
    p_ts.add_argument(
        "--verify-freq", dest="verify_freq", action="store_true", default=True,
        help="Run a frequency calculation on the converged TS to verify it has "
             "exactly one imaginary mode (the reaction-coordinate direction). "
             "Default on.",
    )
    p_ts.add_argument(
        "--no-verify-freq", dest="verify_freq", action="store_false",
        help="Skip the post-TS frequency verification.",
    )

    p_irc = sub.add_parser(
        "irc", help="Intrinsic reaction coordinate (walk down from a TS).",
    )
    _add_common(p_irc)
    p_irc.add_argument(
        "--max-points", type=int, default=40,
        help="Max IRC points per direction (default 40).",
    )
    p_irc.add_argument(
        "--step", type=float, default=0.05,
        help="Mass-weighted step size in amu^1/2 * bohr (default 0.05). xtb path only.",
    )

    p_rxn = sub.add_parser(
        "rxn-energy",
        help="Reaction energy ΔE / ΔH / ΔG for reactants → products.",
    )
    # rxn-energy has no single 'input' file. Species come from repeated
    # --reactant / --product flags. Method/solvent/PySCF knobs still apply.
    _add_chem_options(p_rxn, with_input=False)
    p_rxn.add_argument(
        "--reactant", action="append", default=None, required=True,
        help="Species spec '[COEF*]PATH[,charge=Q][,mult=M]'. Repeat per reactant.",
    )
    p_rxn.add_argument(
        "--product", action="append", default=None, required=True,
        help="Species spec '[COEF*]PATH[,charge=Q][,mult=M]'. Repeat per product.",
    )
    p_rxn.add_argument(
        "--mode", choices=["sp", "opt", "freq"], default="sp",
        help="sp: single-point on each input xyz (default). opt: optimize then SP. "
             "freq: full opt+freq → reports ΔE, ΔH, ΔG.",
    )
    p_rxn.add_argument("--temperature", type=float, default=298.15)
    p_rxn.add_argument("--pressure", type=float, default=101325.0)

    p_scan = sub.add_parser(
        "scan", help="Relaxed dihedral scan (torsional energy profile).",
    )
    _add_common(p_scan)
    p_scan.add_argument(
        "--dihedral", default=None,
        help="Comma-separated 1-based atom indices i,j,k,l defining the dihedral "
             "to scan (matches the C1, C2, ... labels in plots and filenames). "
             "If omitted, auto-detects all non-ring rotatable C–C bonds "
             "(including methyl rotors) and scans each.",
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

    # PySCF-only knobs threaded into every task.run(...) call below.
    # Tasks that don't use them ignore them; tasks that use dft/hf consume them.
    pyscf_kwargs = dict(tier=args.tier, functional=args.functional, basis=args.basis)

    if args.task == "sp":
        from .tasks import sp
        result = sp.run(args.input, method=args.method, charge=args.charge,
                        multiplicity=args.multiplicity, solvent=args.solvent, cli=cli,
                        **pyscf_kwargs)
    elif args.task == "opt":
        from .tasks import opt
        result = opt.run(args.input, method=args.method, charge=args.charge,
                         multiplicity=args.multiplicity, solvent=args.solvent,
                         fmax=args.fmax, steps=args.steps, out_xyz=args.xyz_out,
                         cli=cli, **pyscf_kwargs)
    elif args.task == "freq":
        from .tasks import freq
        result = freq.run(args.input, method=args.method, charge=args.charge,
                          multiplicity=args.multiplicity, solvent=args.solvent,
                          temperature_K=args.temperature, pressure_Pa=args.pressure,
                          geometry=args.geometry, symmetrynumber=args.symmetry,
                          preopt=args.preopt, preopt_fmax=args.preopt_fmax,
                          auto_confsearch=args.auto_confsearch,
                          cli=cli, **pyscf_kwargs)
    elif args.task == "binding":
        from .tasks import binding
        result = binding.run(args.input, args.monomer, method=args.method,
                             charge=args.charge, multiplicity=args.multiplicity,
                             solvent=args.solvent,
                             monomer_charges=args.monomer_charge,
                             monomer_multiplicities=args.monomer_mult, cli=cli,
                             **pyscf_kwargs)
    elif args.task == "redox":
        from .tasks import redox
        result = redox.run(args.input, method=args.method,
                           oxidized_charge=args.ox_charge,
                           reduced_charge=args.red_charge,
                           oxidized_multiplicity=args.ox_mult,
                           reduced_multiplicity=args.red_mult,
                           solvent=args.solvent, reference=args.ref,
                           n_electrons=args.n_electrons, cli=cli,
                           **pyscf_kwargs)
    elif args.task == "confsearch":
        from .tasks import confsearch
        result = confsearch.run(
            args.input, method=args.method, solvent=args.solvent,
            n_max_conformers=args.max_conformers,
            postopt=args.postopt,
            postopt_rmsd=args.postopt_rmsd,
            postopt_ewin=args.postopt_ewin,
            charge=args.charge, multiplicity=args.multiplicity,
            cli=cli, **pyscf_kwargs,
        )
    elif args.task == "frontier":
        from .tasks import frontier
        result = frontier.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            nfrontier=args.nfrontier, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "electrostatics":
        from .tasks import electrostatics
        result = electrostatics.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent, cli=cli,
            **pyscf_kwargs,
        )
    elif args.task == "solvation":
        if not args.solvent:
            parser.error("solvation requires --solvent (e.g. --solvent water)")
        from .tasks import solvation
        result = solvation.run(
            args.input, method=args.method, solvent=args.solvent,
            charge=args.charge, multiplicity=args.multiplicity, cli=cli,
            **pyscf_kwargs,
        )
    elif args.task == "logp":
        from .tasks import logp
        result = logp.run(
            args.input, method=args.method,
            charge=args.charge, multiplicity=args.multiplicity, cli=cli,
            **pyscf_kwargs,
        )
    elif args.task == "fukui":
        from .tasks import fukui
        out_path_pre = args.out or _default_out(args.input, args.task, args.method)
        out_stem = os.path.splitext(out_path_pre)[0]
        result = fukui.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            cation_mult=args.cation_mult, anion_mult=args.anion_mult,
            plot=args.plot, out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "ts":
        from .tasks import ts
        out_path_pre = args.out or _default_out(args.input, args.task, args.method)
        out_stem = os.path.splitext(out_path_pre)[0]
        result = ts.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            steps=args.steps, verify_freq=args.verify_freq,
            out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "irc":
        from .tasks import irc
        out_path_pre = args.out or _default_out(args.input, args.task, args.method)
        out_stem = os.path.splitext(out_path_pre)[0]
        result = irc.run(
            args.input, method=args.method, charge=args.charge,
            multiplicity=args.multiplicity, solvent=args.solvent,
            max_points=args.max_points, step=args.step,
            out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "rxn-energy":
        from .tasks import reaction_energy
        result = reaction_energy.run(
            reactants=args.reactant, products=args.product,
            method=args.method, mode=args.mode, solvent=args.solvent,
            temperature_K=args.temperature, pressure_Pa=args.pressure,
            cli=cli, **pyscf_kwargs,
        )
    elif args.task == "profile":
        from .tasks import reaction_profile as profile_task
        out_path_pre = (
            args.out
            or _default_out(args.reactant, args.task, args.method)
        )
        out_stem = os.path.splitext(out_path_pre)[0]
        result = profile_task.run(
            reactant_xyz=args.reactant, product_xyz=args.product,
            ts_guess_xyz=args.ts_guess, method=args.method,
            charge=args.charge, multiplicity=args.multiplicity,
            solvent=args.solvent,
            temperature_K=args.temperature, pressure_Pa=args.pressure,
            rmsd_tol=args.rmsd_tol, skip_irc=args.skip_irc,
            out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    elif args.task == "pka":
        from .tasks import pka as pka_task
        result = pka_task.run(
            ha_xyz=args.ha, a_minus_xyz=args.a_minus,
            method=args.method, mode=args.mode, solvent=args.solvent,
            ha_charge=args.ha_charge, ha_multiplicity=args.ha_mult,
            a_minus_multiplicity=args.a_minus_mult,
            temperature_K=args.temperature, pressure_Pa=args.pressure,
            hplus_reference=args.hplus_reference,
            ref_ha_xyz=args.ref_ha, ref_a_minus_xyz=args.ref_a_minus,
            ref_pka=args.pka_ref,
            ref_ha_charge=args.ref_ha_charge,
            ref_ha_multiplicity=args.ref_ha_mult,
            ref_a_minus_multiplicity=args.ref_a_minus_mult,
            cli=cli, **pyscf_kwargs,
        )
    elif args.task == "build":
        import re
        from .tasks import build as build_task
        if args.out_xyz:
            out_xyz = args.out_xyz
        else:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", args.smiles)[:60] or "molecule"
            out_xyz = os.path.abspath(f"{safe}.xyz")
        result = build_task.run(
            molecule=args.smiles, out_xyz=out_xyz, name=args.name,
            opt_method=args.opt_method, opt_solvent=args.solvent,
            opt_charge=args.charge, opt_multiplicity=args.multiplicity,
            tier=args.tier, functional=args.functional, basis=args.basis,
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
            out_stem=out_stem, cli=cli, **pyscf_kwargs,
        )
    else:
        parser.error(f"Unknown task {args.task!r}")
        return 2

    # Tasks without a single `input` xyz need bespoke default-output paths.
    if args.task == "rxn-energy":
        from .tasks.reaction_energy import _parse_species_spec
        first_path, _, _, _ = _parse_species_spec(args.reactant[0])
        out_path = args.out or _default_out(first_path, args.task, args.method)
    elif args.task == "pka":
        out_path = args.out or _default_out(args.ha, args.task, args.method)
    elif args.task == "profile":
        out_path = args.out or _default_out(args.reactant, args.task, args.method)
    elif args.task == "build":
        # build's input is a SMILES string and its --opt is optional, so the
        # naming convention is simpler: drop next to the xyz it wrote.
        if args.out:
            out_path = args.out
        else:
            stem = os.path.splitext(result["xyz_path"])[0]
            out_path = os.path.abspath(f"{stem}_build.json")
    else:
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
    if args.task == "fukui" and result.get("plot_png"):
        print(f"# plot_png: {result['plot_png']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
