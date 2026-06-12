# Example: Water Dipole Moment from Atomic Charges (GFN2-xTB)

## Goal
Compute the molecular dipole moment of water from atomic charges using a fast semi-empirical method, and compare it against the experimental gas-phase value.

## Calculation run
- **Skill:** electrostatics
- **Method:** GFN2-xTB (program: xtb)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** 0 / 1
- **Solvent:** gas phase

```bash
# Env: anl_env
python skills/electrostatics/scripts/electrostatics.py --method xtb water.xyz --out water_electrostatics.json
```

Generated files: [`water_electrostatics.json`](water_electrostatics.json), [`water.xyz`](water.xyz)

## Result (this calculation)

| Property | Value | Notes |
|---|---|---|
| Dipole magnitude | 2.29 D | Mulliken-based, GFN2-xTB |

## Literature comparison

| Quantity | Computed | Literature (experiment) | Verdict |
|---|---|---|---|
| Dipole moment | 2.29 D | 1.855 D | GFN2-xTB overestimates the water dipole (2.29 vs 1.855 D); a known tendency, honest to report. |

## References
- T. R. Dyke, J. S. Muenter. "Electric dipole moments of low J states of H2O and D2O." *J. Chem. Phys.* **1973**, *59*, 3125. https://doi.org/10.1063/1.1680453 — experimental. *(value/DOI not web-verified in this session. [CITATION UNVERIFIED])*

## 3D Structures
- [water.xyz](water.xyz)

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
