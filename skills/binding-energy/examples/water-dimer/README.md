# Example: Water dimer binding energy (GFN2-xTB)

## Goal
Compute the gas-phase electronic binding energy of the water dimer relative to two optimized water monomers.

## Calculation run
- **Skill:** binding-energy
- **Method + program:** GFN2-xTB (xtb)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** 0 / 1
- **Solvent:** gas phase

Complex = optimized water dimer; monomers = two optimized water molecules.

```bash
# Env: anl_env
python skills/binding-energy/scripts/binding-energy.py --method xtb --monomer water_monomer.xyz --monomer water_monomer.xyz water_dimer.xyz --out water_dimer_binding.json
```

Generated files: [water_dimer.xyz](water_dimer.xyz), [water_monomer.xyz](water_monomer.xyz), [water_dimer_binding.json](water_dimer_binding.json)

## Result (this calculation)

| Quantity | Value |
|---|---|
| Binding energy | -4.39 kcal/mol |
| Binding energy | -0.19 eV |

## Literature comparison

| Source | Binding energy | Type |
|---|---|---|
| This calculation (GFN2-xTB) | -4.39 kcal/mol | computed (electronic, no BSSE) |
| CCSD(T)/CBS De | ~ -5.0 kcal/mol | high-level computational benchmark |
| Experimental D0 (incl. ZPE) | ~ -3.0 kcal/mol | experimental |

The CCSD(T)/CBS value is a high-level **computational** benchmark, not an experimental value. The experimental D0 (~-3.0 kcal/mol) includes ZPE.

**Verdict:** GFN2-xTB (-4.39) sits reasonably between the ZPE-inclusive D0 (~-3.0) and the electronic De (~-5.0); good for a semi-empirical method. Note this skill reports an electronic binding energy with no BSSE correction.

## References
- B. Temelso, K. A. Archer, G. C. Shields. "Benchmark structures and binding energies of small water clusters." J. Phys. Chem. A 2011, 115, 12034. https://doi.org/10.1021/jp2069489 — CCSD(T)/CBS computational benchmark. *(value/DOI not web-verified in this session. [CITATION UNVERIFIED])*

## 3D Structures
- [water_dimer.xyz](water_dimer.xyz)
- [water_monomer.xyz](water_monomer.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
