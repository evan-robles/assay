# Example: Haber–Bosch ammonia synthesis reaction energy (GFN2-xTB)

## Goal
Compute the reaction energy, enthalpy, and free energy of the Haber–Bosch reaction N₂ + 3 H₂ → 2 NH₃ using the `reaction-energy` skill.

## Calculation run
- **Skill:** reaction-energy
- **Method + program:** GFN2-xTB (xtb), mode = freq (opt + freq each species, gives dE/dH/dG)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** charge 0 / multiplicity 1, all species
- **Solvent:** gas phase

Reaction: N₂ + 3 H₂ → 2 NH₃ (Haber–Bosch).

```bash
# Env: anl_env
python skills/reaction-energy/scripts/reaction-energy.py --method xtb --mode freq --reactant n2.xyz --reactant "3*h2.xyz" --product "2*ammonia.xyz" --out haber_reaction_energy.json
```

Generated files: [n2.xyz](n2.xyz), [h2.xyz](h2.xyz), [ammonia.xyz](ammonia.xyz), [haber_reaction_energy.json](haber_reaction_energy.json)

## Result (this calculation)

| Quantity | Value (kcal/mol) |
|---|---|
| ΔE (electronic) | −88.16 |
| ΔH | −69.16 |
| ΔG | −54.56 |

## Literature comparison

| Quantity | Computed | Literature (experimental) | Verdict |
|---|---|---|---|
| ΔH°(298 K) | −69.16 kcal/mol | −21.9 kcal/mol (−91.8 kJ/mol) | ~3× too exothermic |

The skill machinery works end-to-end (dE, dH, dG all produced; note dE alone, −88, is electronic-only and not comparable to dH). But GFN2-xTB substantially over-binds: computed dH = −69.2 vs experimental −21.9 kcal/mol (~3× too exothermic). Honest assessment: GFN2-xTB is inaccurate for reaction thermochemistry involving the strong N≡N triple bond. The workflow is validated; the method accuracy for this hard reaction is poor.

## References
- Chase, M. W. NIST-JANAF Thermochemical Tables, 4th ed.; *J. Phys. Chem. Ref. Data*, Monograph 9; American Institute of Physics: Woodbury, NY, **1998**. https://doi.org/10.18434/T42S31. (NH₃ standard enthalpy of formation ΔH°f = −45.9 kJ/mol.)

## 3D Structures
- [n2.xyz](n2.xyz)
- [h2.xyz](h2.xyz)
- [ammonia.xyz](ammonia.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
