---
description: logP (octanol/water partition coefficient) — When the user wants the predicted octanol/water partition coefficient of a NEUTRAL molecule (e.g. "logP", "log P", "partition coefficient", "lipophilicity", "octanol/water partition"). Single-point — does NOT optimize. For ionizable molecules use logD (pH-dependent, not supported here). For chemoinformatic estimates also consider RDKit's Crippen/XLogP.
---

# logP — Octanol/Water Partition Coefficient

Estimate logP from ΔG_solv(water) − ΔG_solv(octanol) at 298.15 K. Three single-point energies (gas, water, octanol) on the supplied geometry. Screening-grade (±1 log unit typical at semi-empirical level).

Sign convention: **positive logP → prefers octanol (lipophilic)**.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required) of the **neutral** molecule
- `--method {xtb,mopac,dft,hf}` (required — if missing, **AskUserQuestion**)
- Optional: `--mult N` (default 1 — for a closed-shell neutral)
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

DFT logP is slower but meaningfully better than semi-empirical for polar / H-bonding scaffolds. The ddCOSMO model in PySCF lacks cavitation / dispersion-repulsion terms (no SMD parameterization for octanol), so DFT logP here is still screening-grade — better than xtb/mopac but not a substitute for SMD-parameterized DFT in a dedicated package.

## Refuses
- `--charge ≠ 0` — logP is defined for the neutral species. If the molecule is ionizable at physiological pH, what the user wants is **logD**, which is pH-dependent and out of scope here. Mention this and offer to compute ΔG_solv in water (via `/solvation`) on the relevant ionization state instead.

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion. If user passed `--charge !=0` → stop, explain logD.
2. Run `chemkit logp --method <M> [--mult <M>] <XYZ>`.
3. Read the JSON. Copy to `<basename>_logp_<method>.json` in the cwd.
4. Report:
   - **logP** (the headline number)
   - ΔG_solv(water) and ΔG_solv(octanol), both in kcal/mol
   - ΔΔG (water − octanol)
   - Method, charge/multiplicity (charge is always 0)
   - Caveats: ±1 log unit; electronic ΔG_solv only; for chemoinformatic estimates use RDKit Crippen.
   - Flag any warnings in the JSON.

## Recommendation
For numerical comparison, RDKit's `Crippen.MolLogP` (group-contribution) is faster and often comparably accurate for drug-like molecules. The chemkit thermodynamic-cycle approach has the advantage of reflecting the actual electronic structure / conformation, which matters for unusual scaffolds where group-contribution models fail.

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- xtb-python / MOPAC missing → install via `conda install -c conda-forge xtb-python mopac`.

## Running this skill

This skill folder is self-contained. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python logp.py --help                 # full argument list
```

The script bundles everything it needs under `_engine/`; no external package
is required on the path.
