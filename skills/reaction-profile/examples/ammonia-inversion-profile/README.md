# Example: Ammonia inversion reaction profile (PM7)

## Goal
Build the full reaction profile (reactant, TS, product, and energy diagram) for NH₃ umbrella inversion using the `reaction-profile` skill.

## Calculation run
- **Skill:** reaction-profile
- **Method + program:** PM7 (MOPAC); full pipeline — opt(reactant) + opt(product) + TS search + freq on all three + diagram
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** charge 0 / multiplicity 1
- **Solvent:** gas phase

Reactant = pyramidal NH₃; product = inverted (mirror) NH₃; TS guess = planar D₃ₕ.

```bash
# Env: anl_env
python skills/reaction-profile/scripts/reaction-profile.py --reactant nh3_reactant.xyz --product nh3_product.xyz --ts-guess nh3_ts_guess.xyz --method mopac --out nh3_profile.json
```

Generated files: [nh3_reactant.xyz](nh3_reactant.xyz), [nh3_product.xyz](nh3_product.xyz), [nh3_ts_guess.xyz](nh3_ts_guess.xyz), [nh3_profile.json](nh3_profile.json), [nh3_profile_diagram.png](nh3_profile_diagram.png), [profile_nh3_reactant_opt.xyz](profile_nh3_reactant_opt.xyz), [profile_nh3_product_opt.xyz](profile_nh3_product_opt.xyz), [profile_nh3_ts_opt.xyz](profile_nh3_ts_opt.xyz)

## Result (this calculation)

| Quantity | Value (kcal/mol) |
|---|---|
| ΔE‡ (activation) | 3.79 |
| ΔH‡ (activation) | 3.79 |
| ΔG‡ (activation) | 4.22 |
| ΔG‡ (reverse) | 4.22 |
| ΔE reaction | 0.0 |
| ΔG reaction | 0.0 |

## Literature comparison

| Quantity | Computed | Literature (experimental) | Verdict |
|---|---|---|---|
| NH₃ inversion barrier | 3.79–4.22 kcal/mol | ≈ 5.8 kcal/mol | underestimated |
| ΔG reaction | 0.0 kcal/mol | 0 (symmetric) | correct |

The full reaction-profile pipeline succeeds end-to-end (opt + TS + freq + diagram). ΔG reaction = 0 is correct (symmetric inversion: identical reactant/product). The activation barrier (3.79–4.22 kcal/mol) underestimates the experimental ~5.8 kcal/mol, consistent with the standalone TS example — an honest PM7 limitation.

## References
- J. D. Swalen, J. A. Ibers. "Potential function for the inversion of ammonia." J. Chem. Phys. 1962, 36, 1914 (experimental NH₃ inversion barrier). https://doi.org/10.1063/1.1701290 *(value/DOI not web-verified in this session. [CITATION UNVERIFIED])*

## 3D Structures
- [nh3_reactant.xyz](nh3_reactant.xyz)
- [nh3_product.xyz](nh3_product.xyz)
- [profile_nh3_ts_opt.xyz](profile_nh3_ts_opt.xyz)
- Energy diagram (image): [nh3_profile_diagram.png](nh3_profile_diagram.png)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
