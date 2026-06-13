# Example: Ammonia inversion transition state (PM7)

## Goal
Locate and validate the transition state for NH₃ umbrella inversion using the `transition-state` skill, and extract the inversion barrier.

## Calculation run
- **Skill:** transition-state
- **Method + program:** PM7 (MOPAC) — uses MOPAC's native saddle optimizer, which reliably holds the saddle (a generic xtb/PM7 minimizer can fall back to a minimum, so MOPAC's saddle optimizer was chosen)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** charge 0 / multiplicity 1
- **Solvent:** gas phase

TS guess = planar D₃ₕ NH₃.

```bash
# Env: anl_env
python skills/transition-state/scripts/transition-state.py --method mopac nh3_planar_ts_guess.xyz --out nh3_ts.json
```

Generated files: [nh3_planar_ts_guess.xyz](nh3_planar_ts_guess.xyz), [nh3_ts_optimized.xyz](nh3_ts_optimized.xyz), [nh3_ts.json](nh3_ts.json), [nh3_minimum_freq.json](nh3_minimum_freq.json)

## Result (this calculation)

| Quantity | Value |
|---|---|
| Imaginary modes | 1 (−854.7 cm⁻¹, umbrella inversion) |
| is_valid_ts | True |
| TS heat of formation | −0.465 kcal/mol |
| Pyramidal minimum heat of formation | −4.261 kcal/mol |
| Inversion barrier | 3.80 kcal/mol |

## Literature comparison

| Quantity | Computed | Literature (experimental) | Verdict |
|---|---|---|---|
| NH₃ inversion barrier | 3.80 kcal/mol | ≈ 5.8 kcal/mol | ~2 kcal/mol low |

The TS is correctly located and validated (single imaginary mode = the inversion coordinate). PM7 underestimates the barrier (3.80 vs 5.8 kcal/mol, ~2 kcal/mol low) — honest and reasonable for a semi-empirical method. Note also: xtb/Sella on this system returned a minimum (0 imaginary modes); MOPAC's native saddle optimizer succeeded — a good illustration of choosing the right TS method.

## References
- J. D. Swalen, J. A. Ibers. "Potential function for the inversion of ammonia." J. Chem. Phys. 1962, 36, 1914 (experimental NH₃ inversion barrier). https://doi.org/10.1063/1.1701290 *(value/DOI not web-verified in this session. [CITATION UNVERIFIED])*

## 3D Structures
- [nh3_planar_ts_guess.xyz](nh3_planar_ts_guess.xyz)
- [nh3_ts_optimized.xyz](nh3_ts_optimized.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
