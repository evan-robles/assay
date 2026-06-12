# Example: Ethanol octanol-water logP (GFN2-xTB)

## Goal
Estimate the octanol-water partition coefficient (logP) of ethanol using implicit-solvent free energies from a semi-empirical method.

## Calculation run
- **Skill:** logp-partition
- **Method + program:** GFN2-xTB (xtb)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** 0 / 1
- **Solvent:** water + octanol (fixed by the skill)

```bash
# Env: anl_env
python skills/logp-partition/scripts/logp-partition.py --method xtb ethanol.xyz --out ethanol_logp.json
```

Generated files: [ethanol.xyz](ethanol.xyz), [ethanol_logp.json](ethanol_logp.json)

## Result (this calculation)

| Quantity | Value |
|---|---|
| logP | +1.10 |
| dG_solv (water) | -3.06 kcal/mol |
| dG_solv (octanol) | -4.55 kcal/mol |
| ddG | 1.50 kcal/mol |

## Literature comparison

| Source | logP | Type |
|---|---|---|
| This calculation (GFN2-xTB) | +1.10 | computed |
| Hansch/Leo/Hoekman | -0.31 | experimental |

**Verdict:** POOR agreement — GFN2-xTB predicts +1.10 vs experimental -0.31 (wrong sign). Honest to report: implicit-solvent logP from a semi-empirical method is screening-grade and small polar molecules like ethanol are a hard case. Report this as a known limitation, not a success.

## References
- C. Hansch, A. Leo, D. Hoekman. "Exploring QSAR: Hydrophobic, Electronic, and Steric Constants." ACS, 1995 — compiled experimental logP values (ethanol -0.31). *(value/DOI not web-verified in this session. [CITATION UNVERIFIED])*

## 3D Structures
- [ethanol.xyz](ethanol.xyz)

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
