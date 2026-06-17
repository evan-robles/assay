# Example: n-Butane central C–C torsion scan (GFN2-xTB)

## Goal
Map the relaxed torsional energy profile of n-butane about the central
C1–C2–C3–C4 dihedral and recover the classic anti / gauche minima and the
syn (cis) barrier, comparing to experimental values.

## Calculation run

- **Skill:** `conformational-analysis` (relaxed dihedral scan)
- **Method:** GFN2-xTB (semi-empirical), program `xtb`
- **Basis / functional:** not applicable (semi-empirical)
- **Charge / multiplicity:** 0 / 1
- **Dihedral scanned:** atoms 1,2,3,4 (the C–C–C–C backbone)
- **Scan:** 12 points over 360° (30° spacing); each point relaxed (all other
  degrees of freedom optimized)

```bash
# Env: anl_env
python skills/conformational-analysis/scripts/conformational-analysis.py \
    --method xtb --dihedral 1,2,3,4 --steps 12 butane.xyz --out butane_scan.json
```

Generated files: [butane_scan.json](butane_scan.json),
[butane_torsion_profile.png](butane_torsion_profile.png),
[butane_scan_trajectory.xyz](butane_scan_trajectory.xyz)

## Result (this calculation)

Relative energies vs. the anti minimum (180°):

| Dihedral (°) | ΔE (kcal/mol) | Assignment |
|---|---|---|
| 180 | 0.00 | **anti** (global min) |
| 60 / 300 | 0.78 | **gauche** (local min) |
| 90 / 270 | ~1.3 | gauche barrier region |
| 0 / 360 | 4.93 | **syn / cis** (top barrier) |

- anti→gauche energy difference: **0.78 kcal/mol**
- syn (cis) barrier: **4.93 kcal/mol**

## Literature comparison (experimental)

| Quantity | GFN2-xTB | Experiment |
|---|---|---|
| anti–gauche ΔE (kcal/mol) | 0.78 | 0.67 |
| syn (cis) barrier (kcal/mol) | 4.93 | ~4.5–4.9 |

The experimental anti–gauche enthalpy difference of n-butane is ≈ 0.67 kcal/mol
and the cis barrier is ≈ 4.5–4.9 kcal/mol. GFN2-xTB reproduces both very well —
a strong validation of the relaxed-scan workflow on the textbook case.

## References

- Compton, D. A. C.; Montero, S.; Murphy, W. F. Low-Frequency Raman Spectrum and
  Asymmetric Potential Function for Internal Rotation of Gaseous n-Butane.
  *J. Phys. Chem.* **1980**, *84*, 3587–3591.
  https://doi.org/10.1021/j100463a018.
- Bannwarth, C.; Ehlert, S.; Grimme, S. GFN2-xTB — An Accurate and Broadly
  Parametrized Self-Consistent Tight-Binding Quantum Chemical Method with
  Multipole Electrostatics and Density-Dependent Dispersion Contributions.
  *J. Chem. Theory Comput.* **2019**, *15*, 1652–1671.
  https://doi.org/10.1021/acs.jctc.8b01176.

## 3D Structures

- Input: [butane.xyz](butane.xyz)
- Scan trajectory: [butane_scan_trajectory.xyz](butane_scan_trajectory.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
