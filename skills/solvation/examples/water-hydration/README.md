# Example: Hydration free energy of water (GFN2-xTB / ALPB)

## Goal
Compute the (electronic) solvation free energy of a water molecule in implicit
water and compare to the experimental hydration free energy of water.

## Calculation run

- **Skill:** `solvation`
- **Method:** GFN2-xTB (semi-empirical), program `xtb`
- **Solvation model:** ALPB implicit solvent, solvent = water
- **Basis / functional:** not applicable (semi-empirical)
- **Charge / multiplicity:** 0 / 1
- **Definition:** ΔG_solv = E(solvated) − E(gas) at a fixed geometry
  (electronic only, as computed by this skill)

```bash
# Env: anl_env
python skills/solvation/scripts/solvation.py \
    --method xtb --solvent water water.xyz --out water_solv.json
```

Input structure: [water.xyz](water.xyz)

## Result (this calculation)

| Quantity | GFN2-xTB / ALPB(water) |
|---|---|
| ΔG_solv | −6.07 kcal/mol (−0.263 eV) |

## Literature comparison (experimental)

| Quantity | This run | Experiment |
|---|---|---|
| Hydration free energy of H₂O (kcal/mol) | −6.07 | −6.3 |

The experimental hydration free energy of water is −6.3 kcal/mol. **Important
attribution note:** this value is *experimental* and must be cited to an
experimental source — not to the computational solvation-model papers (e.g.
Kelly/Cramer/Truhlar) that merely tabulate it. The standard experimental
primary reference is Ben-Naim & Marcus.

GFN2-xTB/ALPB reproduces it within ~0.2 kcal/mol. Note this skill reports the
*electronic* solvation energy at a fixed geometry; a rigorous ΔG_hyd would also
include thermal/entropic terms, so close agreement here is partly fortuitous —
honest to flag.

## Reference

- A. Ben-Naim, Y. Marcus. "Solvation thermodynamics of nonionic solutes."
  *J. Chem. Phys.* **1984**, *81*, 2016–2027. https://doi.org/10.1063/1.447824 —
  experimental solvation free energies (source of the −6.3 kcal/mol value for
  water). *(DOI/value not web-verified in this session. [CITATION UNVERIFIED])*

## 3D Structures

- Input: [water.xyz](water.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
