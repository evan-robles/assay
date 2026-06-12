---
description: pKa Estimation — When the user wants the aqueous pKa of an acid HA (e.g. "pKa", "what's the pKa of this", "acid dissociation constant", "how acidic is this group", "estimate pKa", "deprotonation thermodynamics"). Thermodynamic-cycle method: HA(aq) → A⁻(aq) + H⁺(aq) with full opt+freq on each species in implicit solvent. Two modes: absolute (literature G(H⁺,aq), high systematic error) and reference (against a known acid, cancels errors — strongly recommended).
---

# pKa Estimation

Compute an aqueous pKa from the thermodynamic cycle
`HA(aq) ⇌ A⁻(aq) + H⁺(aq)`. Two modes:

- **absolute** (default) — uses a literature G(H⁺,aq) reference. Easy to set up
  but carries a large method-dependent systematic error (xtb absolute pKa can
  be off by 50+ units; DFT/standard tier typically ±2–3 units; HF worse).
- **reference** — anchors the computed cycle against a known reference acid:
  `pKa(HA) = pKa(Ref) + ΔG_iso / (RT ln10)` where `ΔG_iso` is the free-energy
  change for the *isodesmic* exchange `HA + Ref⁻ → A⁻ + HRef`. Most systematic
  errors cancel (basis set incompleteness, solvation-model bias, etc.).
  **Strongly recommended** whenever you have a chemically similar reference.

## Arguments
`$ARGUMENTS` should include:
- `--ha <path>` (required) — xyz of the protonated form HA
- `--a-minus <path>` (required) — xyz of the deprotonated form A⁻
- `--method {xtb,mopac,dft,hf}` (required — if missing, **AskUserQuestion**)
- `--mode {absolute,reference}` (default `absolute`)
- `--solvent <name>` (default `water` — the absolute G(H⁺) ref only applies to water)
- `--ha-charge N` (default 0; A⁻ charge is automatically HA charge − 1)
- `--ha-mult N`, `--a-minus-mult N` (defaults 1)
- `--hplus-reference {tissandier_1998,kelly_2006}` (default `tissandier_1998`,
  −270.28 kcal/mol; Kelly gives −265.9, shifts every pKa by ~1.4 units)
- `--temperature K` (default 298.15), `--pressure Pa` (default 101325)
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`
- Reference-mode extras (all required when `--mode reference`):
  - `--ref-ha <path>`, `--ref-a-minus <path>` — known acid + its conjugate base
  - `--pka-ref FLOAT` — the **experimental** pKa of the reference acid
  - `--ref-ha-charge N`, `--ref-ha-mult N`, `--ref-a-minus-mult N`

## Examples
```bash
# Absolute pKa of acetic acid at DFT/standard in water
chemkit pka --method dft --tier standard --solvent water \
  --ha acetic_acid.xyz --a-minus acetate.xyz

# Reference mode: predict propionic acid's pKa using acetic acid as anchor
chemkit pka --method dft --tier standard --solvent water --mode reference \
  --ha propionic.xyz --a-minus propionate.xyz \
  --ref-ha acetic.xyz --ref-a-minus acetate.xyz --pka-ref 4.76

# Cheap screen at xtb (large systematic error — use only for relative ranking
# within a series, never for absolute numbers)
chemkit pka --method xtb --solvent water \
  --ha amine_ha.xyz --a-minus amine_a.xyz
```

## Input preparation
**You must supply both HA and A⁻ xyz files yourself** — chemkit does not
automatically build the deprotonated form. Two reliable ways:
- From SMILES via `/build_from_smiles`: build `acetic.xyz` from `CC(=O)O` and
  `acetate.xyz` from `CC(=O)[O-]`, optionally with `--opt xtb` for a sensible
  starting geometry.
- Hand-edit an .xyz to delete the acidic proton, then run `/geometry_optimize`.

## Steps
1. Parse args. If `--ha` or `--a-minus` missing → stop and ask. If method
   missing → AskUserQuestion. If `--mode reference` but `--ref-ha` /
   `--ref-a-minus` / `--pka-ref` missing → stop and ask.
2. Run `chemkit pka [flags...]`. **Heads-up**: at DFT this is 2× full opt+freq
   for absolute mode, 4× for reference mode. Plan for ~10–60 min per species
   at `--tier standard` on a 15-atom molecule.
3. Read the JSON. Copy to a sensible filename in cwd.
4. Report:
   - **pKa** (the headline number)
   - Mode used, solvent, temperature
   - For absolute mode: G(HA), G(A⁻), G(H⁺,aq), standard-state correction,
     ΔG_dissociation
   - For reference mode: ΔG_isodesmic, the reference acid + its exp pKa
   - Sign reminder: lower pKa → stronger acid
   - Surface every warning (especially imaginary modes on any species)
   - Estimate of expected error:
     - xtb absolute: ±10 units or worse (not really meaningful — flag it)
     - xtb reference (same functional group): ±2 units
     - DFT absolute (standard tier): ±3 units typical
     - DFT reference (chemically similar anchor): ±1 unit, often better

## Refuses / Warns
- HA and A⁻ charges differing by anything other than +1 → hard error (cycle
  is malformed).
- Non-aqueous solvent in absolute mode → warning (the G(H⁺) reference is
  parametrized for water).
- Reference mode without all three reference args → hard error.

## Notes
- The largest systematic error in absolute pKa is the choice of G(H⁺,aq).
  Tissandier 1998 (−270.28 kcal/mol) and Kelly 2006 (−265.9 kcal/mol)
  differ by 4.4 kcal/mol → 3.2 pKa units. Reference mode side-steps this
  entirely.
- The standard-state correction (+1.89 kcal/mol = RT ln 24.46 at 298 K) is
  applied automatically in absolute mode (one extra mole on the RHS going
  from 1 atm gas to 1 M aqueous).
- For polyprotic acids, each pKa needs its own run (HA → A⁻ → A²⁻ etc.) with
  the appropriate charges.
- For very strong acids/bases (pKa < 0 or > 14), the calculation works but
  the experimental measurement uncertainty is also large; treat predictions
  as order-of-magnitude.

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- Backend missing → `conda install -c conda-forge xtb-python mopac` or `pip install pyscf`.
- "Too few vibration modes" → known issue on flexible molecules with very
  low-frequency torsions; chemkit raises soft modes to a 50 cm⁻¹ floor
  (quasi-RRHO) so this shouldn't happen, but if it does, optimize tighter
  first and re-run.

## Running this skill

This skill is a single self-contained script. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python pka.py --help                 # full argument list
```

The chemistry engine is inlined into `pka.py`; no other files are required.
