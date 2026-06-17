# Example: Acetic acid pKa (GFN2-xTB)

## Goal
Estimate the aqueous pKa of acetic acid using the `pka-acidity` skill, comparing the absolute-mode and reference-mode workflows.

## Calculation run
- **Skill:** pka-acidity
- **Method + program:** GFN2-xTB (xtb)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** HA charge 0 / singlet (A⁻ charge −1)
- **Solvent:** water

Two runs were performed: (a) absolute mode; (b) reference mode using formic acid (user-supplied experimental pKa 3.75) as the reference acid.

```bash
# Env: anl_env
python skills/pka-acidity/scripts/pka-acidity.py --ha acetic_acid.xyz --a-minus acetate.xyz --method xtb --ha-charge 0 --mode reference --ref-ha formic_acid_reference.xyz --ref-a-minus formate_reference.xyz --pka-ref 3.75 --out acetic_acid_pka_reference_mode.json
```

Generated files: [acetic_acid.xyz](acetic_acid.xyz), [acetate.xyz](acetate.xyz), [formic_acid_reference.xyz](formic_acid_reference.xyz), [formate_reference.xyz](formate_reference.xyz), [acetic_acid_pka_reference_mode.json](acetic_acid_pka_reference_mode.json), [acetic_acid_pka_absolute_mode.json](acetic_acid_pka_absolute_mode.json)

## Result (this calculation)

| Quantity | Value |
|---|---|
| pKa (absolute mode) | −114.7 (nonsensical) |
| pKa (reference mode, ref = formic acid) | 10.58 |

## Literature comparison

| Quantity | Computed | Literature (experimental) | Verdict |
|---|---|---|---|
| pKa (acetic acid) | −114.7 (abs) / 10.58 (ref) | 4.76 | Reference mode far better; still ~6 units high |

Absolute mode gives a physically meaningless value (−114.7) — expected, since absolute pKa from semi-empirical free energies plus a literature G(H⁺,aq) carries enormous systematic error (the skill itself warns against absolute mode). Reference mode (against formic acid, exp pKa 3.75) is far better at 10.58 but still ~6 units too high — GFN2-xTB pKa is screening-grade even in reference mode for this pair. Both are reported honestly; higher-level methods are recommended for quantitative pKa. Note: `--pka-ref 3.75` (formic acid) is a user-supplied experimental reference value — an allowed input, not auto-reported experimental data.

## References
- Haynes, W. M., Ed. *CRC Handbook of Chemistry and Physics*, 95th ed.; CRC Press: Boca Raton, FL, **2014**; Dissociation Constants of Organic Acids and Bases. (Acetic acid pKa 4.76; formic acid pKa 3.75.)

## 3D Structures
- [acetic_acid.xyz](acetic_acid.xyz)
- [acetate.xyz](acetate.xyz)
- [formic_acid_reference.xyz](formic_acid_reference.xyz)
- [formate_reference.xyz](formate_reference.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
