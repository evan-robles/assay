# Example: n-Butane conformer search (MMFF94)

## Goal
Sample the conformers of n-butane and rank their relative energies to locate the anti and gauche minima.

## Calculation run
- **Skill:** conformer-search
- **Method + program:** Open Babel confab, force-field MMFF94 (openbabel); ranked by force-field energy; `--postopt none` (no PM7 re-opt in this run)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** 0 / 1
- **Solvent:** gas phase

```bash
# Env: anl_env
python skills/conformer-search/scripts/conformer-search.py --method xtb --postopt none butane.xyz --out butane_confsearch.json
```

Generated files: [butane.xyz](butane.xyz), [butane_confsearch.json](butane_confsearch.json)

## Result (this calculation)

| Conformer | Relative energy (kcal/mol) |
|---|---|
| anti | 0.00 |
| gauche | 2.14 |
| gauche | 2.14 |

3 conformers found.

## Literature comparison

| Source | anti-gauche gap | Type |
|---|---|---|
| This calculation (MMFF94) | 2.14 kcal/mol | computed |
| Experiment | ~0.67 kcal/mol | experimental |

**Verdict:** The search correctly finds the anti global minimum plus two equivalent gauche conformers, but MMFF94 OVERESTIMATES the anti-gauche gap (2.14 vs ~0.67 kcal/mol). This is force-field-level sampling; running with `--postopt mopac` (PM7 re-optimization) would tighten the energetics. Honest to flag.

## References
- Compton, D. A. C.; Montero, S.; Murphy, W. F. Low-Frequency Raman Spectrum and Asymmetric Potential Function for Internal Rotation of Gaseous n-Butane. *J. Phys. Chem.* **1980**, *84*, 3587–3591. https://doi.org/10.1021/j100463a018.

## 3D Structures
- [butane.xyz](butane.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
