# Example: Benzene HOMO-LUMO Frontier Orbitals (GFN2-xTB)

## Goal
Compute the frontier molecular orbital energies (HOMO, LUMO) and the HOMO-LUMO gap of benzene using a fast semi-empirical method.

## Calculation run
- **Skill:** frontier-orbitals
- **Method:** GFN2-xTB (program: xtb)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** 0 / 1
- **Solvent:** gas phase

```bash
# Env: anl_env
python skills/frontier-orbitals/scripts/frontier-orbitals.py --method xtb benzene.xyz --out benzene_frontier.json
```

Generated files: [`benzene_frontier.json`](benzene_frontier.json), [`benzene.xyz`](benzene.xyz)

## Result (this calculation)

| Property | Value | Notes |
|---|---|---|
| HOMO | -10.91 eV | orbital index 14 |
| LUMO | -6.085 eV | orbital index 15 |
| HOMO-LUMO gap | 4.825 eV | |

## Literature comparison

| Quantity | Computed | Literature (experiment) | Verdict |
|---|---|---|---|
| First vertical ionization energy | -HOMO = 10.91 eV (Koopmans') | 9.24 eV | GFN2-xTB orbital energies are not rigorous IEs; by Koopmans' theorem -HOMO is only a rough, qualitative estimate of the experimental IE. |
| HOMO-LUMO gap | 4.825 eV | — | This is an orbital-energy gap, not an optical gap; no claim is made that it matches a measured optical/excitation gap. |

## References
- National Institute of Standards and Technology. Benzene, Ion Energetics Data; NIST Chemistry WebBook, NIST Standard Reference Database Number 69. https://webbook.nist.gov/cgi/cbook.cgi?ID=C71432&Mask=20 (accessed 2026-06-17). (Ionization energy IE ≈ 9.24 eV, as compiled in the NIST WebBook.)

## 3D Structures
- [benzene.xyz](benzene.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
