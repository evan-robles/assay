# Example: Single-point energy and HOMO of water (GFN2-xTB)

## Goal
Evaluate the electronic energy and frontier-orbital energies of water at a fixed
geometry, and sanity-check the HOMO energy against the experimental first
ionization energy of water via Koopmans' theorem.

## Calculation run

- **Skill:** `single-point-energy`
- **Method:** GFN2-xTB (semi-empirical), program `xtb`
- **Basis / functional:** not applicable (semi-empirical)
- **Charge / multiplicity:** 0 / 1
- **Solvent:** none (gas phase)
- **Geometry:** as-supplied (no optimization — this is a single point)

```bash
# Env: anl_env
python skills/single-point-energy/scripts/single-point-energy.py \
    --method xtb water.xyz --out water_sp.json
```

Input structure: [water.xyz](water.xyz)

## Result (this calculation)

| Quantity | GFN2-xTB |
|---|---|
| Total electronic energy | −5.070208 Hartree (−137.967 eV) |
| HOMO | −12.166 eV |
| LUMO | +1.994 eV |
| HOMO–LUMO gap | 14.16 eV |

## Literature comparison

The absolute electronic energy has no experimental counterpart (its zero is
method-specific), so it is reported for reproducibility only.

For a measurable check, Koopmans' theorem approximates the first vertical
ionization energy as −E(HOMO):

| Quantity | This run (−HOMO) | Experiment (IE₁ of H₂O) |
|---|---|---|
| First ionization energy (eV) | 12.17 | 12.62 |

The experimental first (vertical) ionization energy of gas-phase water is
12.62 eV. The GFN2-xTB Koopmans estimate (12.17 eV) is within ~0.45 eV.
Koopmans' theorem is an approximation and GFN2-xTB orbital energies are not
rigorous IEs, so this is a qualitative sanity check, not a benchmark — honest to
state.

## Reference

- K. Kimura, S. Katsumata, Y. Achiba, T. Yamazaki, S. Iwata. *Handbook of HeI
  Photoelectron Spectra of Fundamental Organic Molecules*; Japan Scientific
  Societies Press, **1981** — experimental photoelectron (ionization energy)
  data for H₂O (IE₁ ≈ 12.62 eV). *(Exact value/source not web-verified in this
  session. [CITATION UNVERIFIED])*

## 3D Structures

- Input: [water.xyz](water.xyz)

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
