# Example: Water HOMO/LUMO orbitals (GFN2-xTB)

## Goal
Generate viewable wavefunction files for water and extract its frontier (HOMO/LUMO) orbitals as volumetric cube files.

## Calculation run
- **Skill:** visualize-orbitals
- **Method + program:** GFN2-xTB (xtb, via `xtb --molden`)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** 0 / 1
- **Solvent:** gas phase
- Cubes for homo, lumo at grid 50

```bash
# Env: anl_env
python skills/visualize-orbitals/scripts/visualize-orbitals.py --method xtb --cubes homo,lumo --grid 50 water.xyz --out water_orbitals.json
```

Generated files: [water.xyz](water.xyz), [water_orbitals.json](water_orbitals.json), [water_orbitals.molden](water_orbitals.molden), [water_orbitals_homo.cube](water_orbitals_homo.cube), [water_orbitals_lumo.cube](water_orbitals_lumo.cube)

## Result (this calculation)

Wrote `water_orbitals.molden` (full SCF) plus `water_orbitals_homo.cube` and `water_orbitals_lumo.cube`.

| Orbital | Index | Energy |
|---|---|---|
| HOMO | 4 | -12.166 eV |
| LUMO | 5 | +1.994 eV |

## Validation
This skill produces viewable wavefunction files, not a measured property — so there is no experimental number to validate against. Validate instead by IDENTITY/sanity: water has 8 electrons -> 4 doubly-occupied MOs, so HOMO = MO 4, which matches. The HOMO of water is the oxygen lone-pair (b1) orbital; open `water_orbitals_homo.cube` in Avogadro/VMD to confirm the lone-pair shape. Note that no rendering is performed by the skill itself.

## References
- G. Schaftenaar, J. H. Noordik. "Molden: a pre- and post-processing program." J. Comput.-Aided Mol. Des. 2000, 14, 123. https://doi.org/10.1023/A:1008193805436 (molden format).

## 3D Structures
- [water.xyz](water.xyz)

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
