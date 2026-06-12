# Example: Vibrational frequencies of water (GFN2-xTB)

## Goal
Compute the three vibrational fundamentals of water (bend, symmetric stretch,
asymmetric stretch) and compare to the experimental gas-phase IR frequencies.

## Calculation run

- **Skill:** `vibrational-analysis`
- **Method:** GFN2-xTB (semi-empirical), program `xtb`
- **Basis / functional:** not applicable (semi-empirical)
- **Charge / multiplicity:** 0 / 1
- **Solvent:** none (gas phase)
- **Symmetry number:** σ = 2 (C₂ᵥ water), supplied via `--symmetry 2`
- **Pre-optimization:** on (default) before the Hessian

```bash
# Env: anl_env
python skills/vibrational-analysis/scripts/vibrational-analysis.py \
    --method xtb --symmetry 2 water.xyz --out water_freq.json
```

Input structure: [water.xyz](water.xyz)

## Result (this calculation)

- Imaginary modes: **0** (a true minimum)
- Zero-point energy: **12.63 kcal/mol**
- Harmonic frequencies (cm⁻¹): **1539.5** (bend), **3642.8** (symmetric
  stretch), **3651.1** (asymmetric stretch)

## Literature comparison (experimental)

| Mode | GFN2-xTB (cm⁻¹) | Experiment (cm⁻¹) |
|---|---|---|
| Bend (ν₂) | 1539.5 | 1594.7 |
| Symmetric stretch (ν₁) | 3642.8 | 3657.1 |
| Asymmetric stretch (ν₃) | 3651.1 | 3755.9 |

The experimental gas-phase fundamentals of H₂O are ν₂ = 1594.7, ν₁ = 3657.1,
ν₃ = 3755.9 cm⁻¹. GFN2-xTB reproduces the stretches within ~15–105 cm⁻¹ and
the bend within ~55 cm⁻¹. Note the experimental values are anharmonic
fundamentals while the computed values are harmonic frequencies, so a modest
systematic offset is expected and should not be read as error in the run.

## Reference

- T. Shimanouchi. *Tables of Molecular Vibrational Frequencies, Consolidated
  Volume I*; NSRDS-NBS 39; National Bureau of Standards, **1972** —
  experimental vibrational fundamentals of H₂O.
  https://doi.org/10.6028/NBS.NSRDS.39
  *(Citation details — exact page for H₂O — not web-verified in this session;
  confirm the primary measurement source if needed. [CITATION UNVERIFIED])*

## 3D Structures

- Input: [water.xyz](water.xyz)

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
