#!/usr/bin/env python3
"""Scaffold fidelity validation suites for the 9 remaining chemkit skills.

Creates benchmarks/fidelity/<skill>-validation/<molecule>/<molecule>_<skill>.spec.json
for each skill (8 molecules each), with the verified report_value_field, correct
input shape (single xyz / string / multi-input `inputs` list), and per-molecule
charge/multiplicity. It does NOT fetch geometries — run fetch_geometries.py after
(single-molecule + monomer xyz auto-fetch from PubChem; derived geometries —
A- forms, dimer complexes, TS guesses, IRC TS geometries — are flagged there for
manual input).

Idempotent: skips a spec that already exists (so re-running won't clobber edits).

Usage:
    # Env: anl_env
    python benchmarks/scaffold_suites.py            # write all
    python benchmarks/scaffold_suites.py --dry-run  # list what would be written
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_FID = _REPO / "benchmarks" / "fidelity"
_RULES = ["calculation-reporting-standards", "research-standards"]


def _xyz_path(skill_dir: str, mol: str, fname: str | None = None) -> str:
    fname = fname or f"{mol}.xyz"
    return f"benchmarks/fidelity/{skill_dir}/{mol}/{fname}"


def _rxn_spec(slug: str, species: str) -> str:
    """Build a reaction-energy species spec with the molecule's xyz path placed
    under the case folder. `species` is `[COEF*]NAME.xyz[,charge=][,mult=]`;
    e.g. '3*h2.xyz' -> '3*benchmarks/.../reaction-energy-validation/<slug>/h2.xyz',
    'o2.xyz,mult=3' -> '.../<slug>/o2.xyz,mult=3'."""
    s = species
    prefix = ""
    if "*" in s.split(",", 1)[0]:
        prefix, s = s.split("*", 1)
        prefix += "*"
    path_part, sep, suffix = s.partition(",")
    rel = _xyz_path("reaction-energy-validation", slug, path_part)
    return f"{prefix}{rel}{sep}{suffix}"


def base(name, skill, prompt, *, xyz=None, inputs=None, input_kind=None,
         intended=None, intended_flags=None, rvf="__MISSING__", value_tol=None,
         expect=None, extra=None):
    """Assemble a spec dict in the canonical key order."""
    d = {"name": name, "skill": skill}
    if xyz is not None:
        d["xyz"] = xyz
    if input_kind is not None:
        d["input_kind"] = input_kind
    if inputs is not None:
        d["inputs"] = inputs
    if expect is not None:
        d["expect"] = expect
    d["prompt"] = prompt
    d["intended_flags"] = intended_flags if intended_flags is not None else []
    d["intended"] = intended if intended is not None else {}
    if rvf != "__MISSING__":
        d["report_value_field"] = rvf
    if value_tol is not None:
        d["value_tol"] = value_tol
    if extra:
        d.update(extra)
    d["rules"] = _RULES
    return d


SPECS: list[tuple[str, str, dict]] = []  # (skill_dir, molecule, spec_dict)


def add(skill_dir, mol, spec):
    SPECS.append((skill_dir, mol, spec))


# --------------------------------------------------------------------------- #
# 1. redox-potential — 1 xyz + --ox-charge/--red-charge ; redox_potential_V_vs_SHE
# --------------------------------------------------------------------------- #
# (name, ox_charge, red_charge, ox_mult, red_mult, solvent, note)
REDOX = [
    ("benzoquinone", 0, -1, 1, 2, "water", "1e- reduction; canonical organic redox"),
    ("tempo", 0, 1, 2, 1, "water", "stable radical oxidation (doublet->singlet)"),
    ("ferrocene", 0, 1, 1, 2, "water", "organometallic reference couple (Fe sandwich)"),
    ("anthraquinone", 0, -1, 1, 2, "water", "extended quinone reduction"),
    ("nitrobenzene", 0, -1, 1, 2, "water", "aromatic nitro radical-anion"),
    ("duroquinone", 0, -1, 1, 2, "water", "methylated quinone (substituent shift)"),
    ("naphthoquinone", 0, -1, 1, 2, "water", "fused-ring quinone"),
    ("tetracyanoethylene", 0, -1, 1, 2, "water", "strong organic acceptor (edge case)"),
]
for mol, oxq, rdq, oxm, rdm, solv, note in REDOX:
    flags = ["--method", "xtb", "--ox-charge", str(oxq), "--red-charge", str(rdq),
             "--ox-mult", str(oxm), "--red-mult", str(rdm), "--solvent", solv,
             "--ref", "SHE"]
    add("redox-potential-validation", mol, base(
        f"{mol}_redox_potential", "redox-potential",
        f"Run the redox-potential task on {mol} using the GFN2-xTB method in "
        f"implicit {solv} solvent, referenced to SHE, for the {oxq}->{rdq} redox "
        f"couple (oxidized charge {oxq} mult {oxm}, reduced charge {rdq} mult "
        f"{rdm}). Report the standard redox potential in volts vs SHE as the "
        f"headline value (the task also returns delta_E_redox in eV/kcal — do NOT "
        f"report those as the headline value), and state the method, reference "
        f"electrode, and charges used. Use the chemkit tools; do not guess the number.",
        xyz=_xyz_path("redox-potential-validation", mol),
        intended_flags=flags,
        intended={"method": "xtb", "charge": oxq, "multiplicity": oxm,
                  "solvent": solv},
        rvf="redox_potential_V_vs_SHE", value_tol=0.1))


# --------------------------------------------------------------------------- #
# 2. conformational-analysis — 1 xyz ; no top-level scalar -> rvf null
# --------------------------------------------------------------------------- #
CONF = [
    ("butane", "anti/gauche C-C-C-C torsion"),
    ("ethane", "simplest 3-fold rotor"),
    ("hydrogen-peroxide", "O-O torsion, cis+trans barriers"),
    ("12-dichloroethane", "Cl-C-C-Cl anti/gauche"),
    ("propane", "central C-C rotation"),
    ("biphenyl", "aryl-aryl twist"),
    ("ethylene-glycol", "coupled rotors + internal H-bond"),
    ("12-difluoroethane", "gauche effect (gauche < anti) edge case"),
]
for mol, note in CONF:
    add("conformational-analysis-validation", mol, base(
        f"{mol}_conformational_analysis", "conformational-analysis",
        f"Run the conformational-analysis task on {mol} using the GFN2-xTB method "
        f"in the gas phase (auto-detect rotatable bonds). Report the rotation "
        f"barrier(s) found (per scanned dihedral) and state the method used. Use "
        f"the chemkit tools; do not guess the number.",
        xyz=_xyz_path("conformational-analysis-validation", mol),
        intended_flags=["--method", "xtb"],
        intended={"method": "xtb", "charge": 0, "multiplicity": 1, "solvent": None},
        rvf=None))


# --------------------------------------------------------------------------- #
# 3. transition-state — 1 TS-guess xyz (ALL FLAGGED) ; total_energy_eV
# --------------------------------------------------------------------------- #
# Reaction family shared with IRC + reaction-profile. mopac default per SKILL.
TS_FAMILY = [
    ("ammonia-inversion", 0, 1, "NH3 umbrella inversion (planar guess)"),
    ("ethane-rotation", 0, 1, "C2H6 eclipsed torsional TS"),
    ("hcn-isomerization", 0, 1, "HCN<->HNC bent TS"),
    ("methanol-torsion", 0, 1, "CH3OH O-H rotation TS"),
    ("hydrogen-peroxide-torsion", 0, 1, "HOOH cis torsional saddle"),
    ("formamide-rotation", 0, 1, "HCONH2 C-N rotation TS"),
    ("formic-acid-ze", 0, 1, "HCOOH syn/anti (Z/E) TS"),
    ("sn2-chloride", -1, 1, "[Cl-CH3-Cl]- Walden inversion (charged edge case)"),
]
for mol, chg, mult, note in TS_FAMILY:
    cflags = ["--method", "mopac"] + (["--charge", str(chg)] if chg else [])
    add("transition-state-validation", mol, base(
        f"{mol}_transition_state", "transition-state",
        f"Run the transition-state task on the {mol} TS guess geometry using the "
        f"PM7 (mopac) method in the gas phase"
        + (f" with charge {chg}" if chg else "")
        + ". Report the total energy in eV as the headline value, and state "
        f"whether the converged structure is a valid TS (exactly one imaginary "
        f"mode) and the imaginary frequency. State the method"
        + (" and charge" if chg else "") + " used. Use the chemkit tools; do not "
        f"guess the number.",
        xyz=_xyz_path("transition-state-validation", mol),
        intended_flags=cflags,
        intended={"method": "mopac", "charge": chg, "multiplicity": mult,
                  "solvent": None},
        rvf="total_energy_eV", value_tol=0.05))


# --------------------------------------------------------------------------- #
# 4. intrinsic-reaction-coordinate — 1 TS xyz (ALL FLAGGED) ; rvf null
# --------------------------------------------------------------------------- #
for mol, chg, mult, note in TS_FAMILY:
    cflags = ["--method", "mopac"] + (["--charge", str(chg)] if chg else [])
    add("intrinsic-reaction-coordinate-validation", mol, base(
        f"{mol}_irc", "intrinsic-reaction-coordinate",
        f"Run the intrinsic-reaction-coordinate task on the {mol} transition-state "
        f"geometry using the PM7 (mopac) method in the gas phase"
        + (f" with charge {chg}" if chg else "")
        + ". Report the forward and reverse endpoint energies, the energy drops "
        f"in each direction, and whether the two endpoints are distinct. State "
        f"the method" + (" and charge" if chg else "") + " used. Use the chemkit "
        f"tools; do not guess the number.",
        xyz=_xyz_path("intrinsic-reaction-coordinate-validation", mol),
        intended_flags=cflags,
        intended={"method": "mopac", "charge": chg, "multiplicity": mult,
                  "solvent": None},
        rvf=None))


# --------------------------------------------------------------------------- #
# 5. name-to-smiles — name string, no xyz ; expect smiles (+1 refusal)
# --------------------------------------------------------------------------- #
NAME2SMI = [
    ("caffeine", "caffeine", "structure"),
    ("l-alanine", "L-alanine", "structure"),
    ("aspirin", "aspirin", "structure"),
    ("2-bromo-3-methylbutane", "2-bromo-3-methylbutane", "structure"),
    ("acetic-acid", "acetic acid", "structure"),
    ("d-glucose", "D-glucose", "structure"),
    ("e-2-butene", "(E)-2-butene", "structure"),
    ("not-a-real-molecule", "florbgnax-9-ium", "refusal"),  # unresolvable bait
]
for slug, query, kind in NAME2SMI:
    if kind == "refusal":
        prompt = (f"Resolve the molecule name '{query}' to a SMILES string using "
                  f"the name-to-smiles tool. Report the resolved SMILES and the "
                  f"source it came from. Use the chemkit tool only; do not invent "
                  f"a SMILES.")
        add("name-to-smiles-validation", slug, base(
            f"{slug}_name_to_smiles", "name-to-smiles",
            prompt, input_kind="string", expect="refusal",
            extra={"input": query},
            intended={"method": ""}))
    else:
        prompt = (f"Resolve the molecule name '{query}' to a SMILES string using "
                  f"the name-to-smiles tool. Report the resolved SMILES (the "
                  f"headline) and the source it was resolved from. Use the chemkit "
                  f"tool only; do not guess the SMILES.")
        add("name-to-smiles-validation", slug, base(
            f"{slug}_name_to_smiles", "name-to-smiles",
            prompt, input_kind="string", expect="smiles",
            extra={"input": query},
            intended={"method": ""}))


# --------------------------------------------------------------------------- #
# 6. binding-energy — complex.xyz (positional) + --monomer x2 ; binding_energy_eV
#    Complex FLAGGED (derived); monomers fetchable.
# --------------------------------------------------------------------------- #
# (slug, complex_label, [monomer_slugs], note)
BINDING = [
    ("water-dimer", "water dimer", ["water", "water"], "H-bond anchor"),
    ("formic-acid-dimer", "formic acid dimer", ["formic-acid", "formic-acid"], "double H-bond"),
    ("ammonia-dimer", "ammonia dimer", ["ammonia", "ammonia"], "weak H-bond"),
    ("hf-dimer", "hydrogen fluoride dimer", ["hydrogen-fluoride", "hydrogen-fluoride"], "strong simple H-bond"),
    ("methane-dimer", "methane dimer", ["methane", "methane"], "pure dispersion"),
    ("ammonia-borane", "ammonia borane", ["ammonia", "borane"], "dative bond"),
    ("water-ammonia", "water-ammonia complex", ["water", "ammonia"], "mixed H-bond donor/acceptor"),
    ("benzene-dimer", "benzene dimer (T-shaped)", ["benzene", "benzene"], "pi-stacking edge case"),
]
for slug, clabel, monos, note in BINDING:
    cx = _xyz_path("binding-energy-validation", slug, "complex.xyz")
    inputs = [{"flag": "--monomer",
               "xyz": _xyz_path("binding-energy-validation", slug, f"monomer_{i+1}_{m}.xyz")}
              for i, m in enumerate(monos)]
    add("binding-energy-validation", slug, base(
        f"{slug}_binding_energy", "binding-energy",
        f"Run the binding-energy task on the {clabel} using the GFN2-xTB method in "
        f"the gas phase. The complex geometry is the positional input; pass each "
        f"monomer with --monomer. Report the binding (interaction) energy in eV as "
        f"the headline value (negative = bound; the task also returns kcal/mol and "
        f"hartree — report eV), and state the method used. Use the chemkit tools; "
        f"do not guess the number.",
        xyz=cx, inputs=inputs,
        intended_flags=["--method", "xtb"],
        intended={"method": "xtb", "charge": 0, "multiplicity": 1, "solvent": None},
        rvf="binding_energy_eV", value_tol=0.01))


# --------------------------------------------------------------------------- #
# 7. reaction-energy — repeated --reactant/--product specs, NO positional ;
#    delta_E_kcal_mol. input_kind="none".
# --------------------------------------------------------------------------- #
# (slug, reaction_str, [(flag, species_spec)], note)
RXN = [
    ("haber", "N2 + 3 H2 -> 2 NH3",
     [("--reactant", "n2.xyz"), ("--reactant", "3*h2.xyz"), ("--product", "2*nh3.xyz")],
     "stoichiometric coefficients"),
    ("water-formation", "2 H2 + O2 -> 2 H2O",
     [("--reactant", "2*h2.xyz"), ("--reactant", "o2.xyz,mult=3"), ("--product", "2*h2o.xyz")],
     "O2 triplet"),
    ("methane-combustion", "CH4 + 2 O2 -> CO2 + 2 H2O",
     [("--reactant", "ch4.xyz"), ("--reactant", "2*o2.xyz,mult=3"),
      ("--product", "co2.xyz"), ("--product", "2*h2o.xyz")], "multi-species, two triplets"),
    ("butane-isomerization", "n-butane -> isobutane",
     [("--reactant", "butane.xyz"), ("--product", "isobutane.xyz")], "near-thermoneutral"),
    ("hcn-isomerization", "HCN -> HNC",
     [("--reactant", "hcn.xyz"), ("--product", "hnc.xyz")], "simplest 1->1"),
    ("ammonia-decomposition", "2 NH3 -> N2 + 3 H2",
     [("--reactant", "2*nh3.xyz"), ("--product", "n2.xyz"), ("--product", "3*h2.xyz")],
     "reverse of Haber (sign check)"),
    ("ethylene-hydrogenation", "C2H4 + H2 -> C2H6",
     [("--reactant", "ethylene.xyz"), ("--reactant", "h2.xyz"), ("--product", "ethane.xyz")],
     "addition, exothermic"),
    ("acid-base-proton-transfer", "CH3COOH + NH3 -> CH3COO- + NH4+",
     [("--reactant", "acetic-acid.xyz"), ("--reactant", "ammonia.xyz"),
      ("--product", "acetate.xyz,charge=-1"), ("--product", "ammonium.xyz,charge=1")],
     "mixed charges edge case"),
]
for slug, rxn, specs_list, note in RXN:
    inputs = [{"flag": f, "spec": _rxn_spec(slug, s)} for f, s in specs_list]
    add("reaction-energy-validation", slug, base(
        f"{slug}_reaction_energy", "reaction-energy",
        f"Run the reaction-energy task for the reaction {rxn} using the GFN2-xTB "
        f"method in the gas phase, in single-point mode. Each species is passed via "
        f"--reactant/--product with its stoichiometric coefficient (and charge/mult "
        f"where needed). Report the reaction energy delta_E in kcal/mol as the "
        f"headline value, and state the method used. Use the chemkit tools; do not "
        f"guess the number.",
        input_kind="none", inputs=inputs,
        intended_flags=["--method", "xtb"],
        intended={"method": "xtb", "charge": 0, "multiplicity": 1, "solvent": None},
        rvf="delta_E_kcal_mol", value_tol=0.1))


# --------------------------------------------------------------------------- #
# 8. pka-acidity — --ha + --a-minus, NO positional ; pKa. A- FLAGGED.
# --------------------------------------------------------------------------- #
# (slug, acid_label, ha_charge, note)
PKA = [
    ("acetic-acid", "acetic acid", 0, "pKa 4.76 reference acid"),
    ("formic-acid", "formic acid", 0, "pKa 3.75"),
    ("phenol", "phenol", 0, "pKa 9.99 resonance anion"),
    ("methanol", "methanol", 0, "pKa ~15.5 weak acid"),
    ("methylammonium", "methylammonium (cationic acid)", 1, "cationic acid, pKaH ~10.6"),
    ("benzoic-acid", "benzoic acid", 0, "pKa 4.20 aromatic carboxylic"),
    ("hydrogen-cyanide", "hydrogen cyanide", 0, "pKa 9.2"),
    ("trifluoroacetic-acid", "trifluoroacetic acid", 0, "strong acid pKa 0.23 edge case"),
]
for slug, label, haq, note in PKA:
    ha = _xyz_path("pka-acidity-validation", slug, "ha.xyz")
    am = _xyz_path("pka-acidity-validation", slug, "a_minus.xyz")
    inputs = [{"flag": "--ha", "xyz": ha}, {"flag": "--a-minus", "xyz": am}]
    flags = ["--method", "xtb", "--mode", "absolute"]
    if haq:
        flags += ["--ha-charge", str(haq)]
    add("pka-acidity-validation", slug, base(
        f"{slug}_pka_acidity", "pka-acidity",
        f"Run the pka-acidity task for {label} using the GFN2-xTB method in "
        f"implicit water (absolute mode). Pass the protonated form with --ha and "
        f"the deprotonated form with --a-minus"
        + (f" (HA charge {haq})." if haq else ".")
        + " Report the aqueous pKa as the headline value, and state the method, "
        f"mode, and charges used. Note xtb absolute pKa is screening-grade. Use "
        f"the chemkit tools; do not guess the number.",
        input_kind="none", inputs=inputs,
        intended_flags=flags,
        intended={"method": "xtb", "charge": haq, "multiplicity": 1,
                  "solvent": "water"},
        rvf="pKa", value_tol=2.0))


# --------------------------------------------------------------------------- #
# 9. reaction-profile — --reactant + --product + --ts-guess, NO positional ;
#    delta_G_activation_kcal_mol. ts-guess FLAGGED; R/P fetchable-ish.
# --------------------------------------------------------------------------- #
PROFILE = [
    ("ammonia-inversion", 0, 1, "symmetric NH3 inversion"),
    ("ethane-rotation", 0, 1, "trivial torsional barrier"),
    ("hcn-isomerization", 0, 1, "HCN->HNC distinct minima"),
    ("methanol-torsion", 0, 1, "O-H rotation profile"),
    ("hydrogen-peroxide-torsion", 0, 1, "HOOH cis-barrier profile"),
    ("formamide-rotation", 0, 1, "C-N rotation barrier"),
    ("formic-acid-ze", 0, 1, "syn/anti isomerization"),
    ("sn2-chloride", -1, 1, "charged bimolecular SN2 (edge case)"),
]
for slug, chg, mult, note in PROFILE:
    r = _xyz_path("reaction-profile-validation", slug, "reactant.xyz")
    p = _xyz_path("reaction-profile-validation", slug, "product.xyz")
    ts = _xyz_path("reaction-profile-validation", slug, "ts_guess.xyz")
    inputs = [{"flag": "--reactant", "xyz": r}, {"flag": "--product", "xyz": p},
              {"flag": "--ts-guess", "xyz": ts}]
    flags = ["--method", "mopac"] + (["--charge", str(chg)] if chg else [])
    add("reaction-profile-validation", slug, base(
        f"{slug}_reaction_profile", "reaction-profile",
        f"Run the reaction-profile task for the {slug} reaction using the PM7 "
        f"(mopac) method in the gas phase"
        + (f" with charge {chg}" if chg else "")
        + ". Pass the reactant, product, and TS guess (shared atom ordering) with "
        f"--reactant/--product/--ts-guess. Report the activation free energy "
        f"delta_G_activation in kcal/mol as the headline value, plus the reaction "
        f"free energy and the IRC connectivity verdict. State the method"
        + (" and charge" if chg else "") + " used. Use the chemkit tools; do not "
        f"guess the number.",
        input_kind="none", inputs=inputs,
        intended_flags=flags,
        intended={"method": "mopac", "charge": chg, "multiplicity": mult,
                  "solvent": None},
        rvf="delta_G_activation_kcal_mol", value_tol=1.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    written, skipped = 0, 0
    for skill_dir, mol, spec in SPECS:
        folder = _FID / skill_dir / mol
        fname = f"{mol}_{skill_dir.replace('-validation','').replace('-','_')}.spec.json"
        # match existing naming: <molecule>_<skill_with_underscores>.spec.json
        target = folder / fname
        if target.exists():
            skipped += 1
            continue
        if args.dry_run:
            print(f"would write {target.relative_to(_REPO)}")
            written += 1
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(spec, indent=2) + "\n")
        written += 1
    print(f"\n{'(dry-run) ' if args.dry_run else ''}wrote {written}, skipped {skipped} existing")


if __name__ == "__main__":
    raise SystemExit(main())
