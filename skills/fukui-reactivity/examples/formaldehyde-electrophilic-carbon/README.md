# Example: Formaldehyde Electrophilic Carbon via Fukui Dual Descriptor (GFN2-xTB)

## Goal
Compute Fukui dual descriptors for formaldehyde to identify the most electrophilic site, and check the result against established organic chemistry.

## Calculation run
- **Skill:** fukui-reactivity
- **Method:** GFN2-xTB (program: xtb)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** 0 / 1
- **Solvent:** gas phase
- **Charges:** Mulliken; plotting disabled (`--no-plot`)

```bash
# Env: anl_env
python skills/fukui-reactivity/scripts/fukui-reactivity.py --method xtb --no-plot formaldehyde.xyz --out formaldehyde_fukui.json
```

Generated files: [`formaldehyde_fukui.json`](formaldehyde_fukui.json), [`formaldehyde.xyz`](formaldehyde.xyz)

## Result (this calculation)

| Atom | Dual descriptor | fukui_plus |
|---|---|---|
| C | +0.116 | 0.135 |
| O | -0.022 | 0.399 |
| H | -0.047 | 0.233 |
| H | -0.047 | 0.233 |

## Literature comparison

| Check | Result | Verdict |
|---|---|---|
| Most electrophilic site (most positive dual descriptor) | Carbonyl carbon C (+0.116) | Correctly identifies the carbonyl carbon as the most electrophilic site — consistent with the textbook fact that nucleophiles attack the carbonyl carbon of formaldehyde (H2C=O). This is a qualitative correctness check against established organic chemistry, not a numerical literature comparison. |

## References
- R. K. Roy, S. Pal, K. Hirao. "On non-negativity of Fukui function indices." *J. Chem. Phys.* **1999**, *110*, 8236. https://doi.org/10.1063/1.478792 (Fukui-dual-descriptor methodology).
- C. Morell, A. Grand, A. Toro-Labbé. "New dual descriptor for chemical reactivity." *J. Phys. Chem. A* **2005**, *109*, 205. https://doi.org/10.1021/jp046577a

## 3D Structures
- [formaldehyde.xyz](formaldehyde.xyz)

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
