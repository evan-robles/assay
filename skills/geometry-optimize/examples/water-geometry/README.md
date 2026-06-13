# Example: Equilibrium geometry of water (GFN2-xTB)

## Goal
Optimize the geometry of a water molecule and compare the resulting O–H bond
length and H–O–H angle to the experimental gas-phase structure.

## Calculation run

- **Skill:** `geometry-optimize`
- **Method:** GFN2-xTB (semi-empirical tight-binding), program `xtb`
- **Basis / functional:** not applicable (GFN2-xTB is a parameterized
  semi-empirical method, no Gaussian basis set or DFT functional)
- **Charge / multiplicity:** 0 / 1 (neutral singlet)
- **Solvent:** none (gas phase)
- **Convergence:** `fmax = 0.05 eV/Å` (default), converged in 3 steps

```bash
# Env: anl_env
python skills/geometry-optimize/scripts/geometry-optimize.py \
    --method xtb water.xyz \
    --xyz-out water_xtbopt.xyz --out water_opt.json
```

Input structure: [water.xyz](water.xyz)

## Result (this calculation)

| Quantity | GFN2-xTB (this run) |
|---|---|
| O–H bond length | 0.959 Å |
| H–O–H angle | 107.2° |
| Optimizer converged | yes (3 steps) |

## Literature comparison (experimental)

| Quantity | GFN2-xTB | Experiment | Abs. error |
|---|---|---|---|
| O–H bond length (Å) | 0.959 | 0.9572 | 0.002 |
| H–O–H angle (°) | 107.2 | 104.52 | 2.7 |

The experimental equilibrium ($r_e$) structure of gas-phase H₂O is
O–H = 0.9572 Å and H–O–H = 104.52°, determined from rotation–vibration
spectroscopy.

GFN2-xTB reproduces the bond length to ~0.002 Å. The bond angle is ~2.7° too
wide — a known tendency of GFN2-xTB to slightly over-open the H–O–H angle, and
honest to report as a method limitation rather than an error in the run.

## Reference

- W. S. Benedict, N. Gailar, E. K. Plyler. "Rotation–Vibration Spectra of
  Deuterated Water Vapor." *J. Chem. Phys.* **1956**, *24*, 1139–1165.
  https://doi.org/10.1063/1.1742731 — experimental (spectroscopic) equilibrium
  geometry of water.

## 3D Structures

- Input: [water.xyz](water.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
