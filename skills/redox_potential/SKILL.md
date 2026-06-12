---
description: Redox Potential — When the user wants to estimate a one- or multi-electron oxidation/reduction potential vs. SHE, Ag/AgCl, or Fc⁺/Fc (e.g. "redox potential", "E1/2", "oxidation potential", "reduction potential", "E vs SHE", "electrochemistry", "compute E0"). For redox-active species only — neutral closed-shell hydrocarbons have no meaningful aqueous redox potential.
---

# Redox Potential

Estimate a one- or n-electron redox potential against SHE, Ag/AgCl, or Fc⁺/Fc.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required — same geometry used for both oxidation states)
- `--method {xtb,mopac,dft,hf}` (required)
- `--ox-charge N` and `--red-charge N` (required, e.g. 0 and −1 for a 1-electron reduction)
- Optional: `--ox-mult` (default 1), `--red-mult` (default 2),
  `--solvent` (strongly recommended), `--ref {SHE,Ag/AgCl,Fc+/Fc}` (default SHE),
  `--n-electrons N` (default 1)
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

DFT redox potentials are typically ±0.1–0.2 V vs experiment when computed with a range-separated hybrid + implicit solvent — significantly better than semi-empirical (±0.3–0.5 V). Anions auto-promote to diffuse basis (def2-tzvp → def2-tzvpd).

## Steps
1. Parse args. Stop and ask if any of `xyz`, method, `--ox-charge`, `--red-charge` missing.
2. Run `chemkit redox --method <M> --ox-charge <Qo> --red-charge <Qr> --solvent <S> --ref <R> <XYZ>`.
3. Read JSON, copy to `<basename>_redox_<method>.json`.
4. Report:
   - **E° vs reference** (in V)
   - ΔE_redox (eV, kcal/mol)
   - Energies of oxidized and reduced states
   - **Warn explicitly**: semi-empirical methods give ±0.3–0.5 V at best; the calculation uses the same geometry for both states (no reorganization energy); solvation correction is implicit-only.

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Recommendation
For publication-grade values: optimize each oxidation state, run `freq` on each for ΔG, and ideally cross-check with a higher-level method. This skill is for screening, not final answers.

## Running this skill

This skill folder is self-contained. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python redox_potential.py --help                 # full argument list
```

The script bundles everything it needs under `_engine/`; no external package
is required on the path.
