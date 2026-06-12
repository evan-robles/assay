# Example: 1,4-Benzoquinone one-electron reduction potential (GFN2-xTB)

## Goal
Estimate the one-electron reduction potential of 1,4-benzoquinone in water versus the standard hydrogen electrode (SHE).

## Calculation run
- **Skill:** redox-potential
- **Method + program:** GFN2-xTB (xtb)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** ox 0 / mult 1 -> red -1 / mult 2 (one-electron reduction)
- **Solvent:** water (ALPB); reference electrode SHE

```bash
# Env: anl_env
python skills/redox-potential/scripts/redox-potential.py --method xtb --solvent water --ox-charge 0 --red-charge -1 --ox-mult 1 --red-mult 2 --ref SHE benzoquinone.xyz --out benzoquinone_redox.json
```

Generated files: [benzoquinone.xyz](benzoquinone.xyz), [benzoquinone_redox.json](benzoquinone_redox.json)

## Result (this calculation)

| Quantity | Value |
|---|---|
| E vs SHE | +4.68 V |
| delta_E_redox | -206.5 kcal/mol |

## Literature comparison

| Source | E vs SHE | Type |
|---|---|---|
| This calculation (GFN2-xTB) | +4.68 V | computed |
| 1,4-benzoquinone/semiquinone in water | ~ +0.10 V | experimental (varies with pH/conditions) |

**Verdict:** VERY POOR absolute agreement (+4.68 V computed vs ~+0.10 V). Honest to report: this skill's absolute redox potential from semi-empirical energies + implicit solvation is screening-grade only; absolute potentials carry large systematic error. Useful at best for relative ranking within a closely related series, not absolute values. State this plainly.

## References
- P. S. Guin, S. Das, P. C. Mandal. "Electrochemical reduction of quinones in different media." Int. J. Electrochem. 2011, 816202. https://doi.org/10.4061/2011/816202 — experimental quinone reduction potentials. *(value/DOI not web-verified in this session. [CITATION UNVERIFIED])*

## 3D Structures
- [benzoquinone.xyz](benzoquinone.xyz)

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
