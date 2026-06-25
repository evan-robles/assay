# Geometry source

- Molecule: methylamine
- Source: PubChem (3D conformer)
- PubChem CID: 6329
- URL: https://pubchem.ncbi.nlm.nih.gov/compound/6329
- Accessed: 2026-06-24

National Center for Biotechnology Information. PubChem Compound Summary for CID 6329, methylamine. https://pubchem.ncbi.nlm.nih.gov/compound/6329 (accessed 2026-06-24).

Note: this is an unoptimized PubChem 3D conformer (a starting geometry), not a structure optimized at the calculation's level of theory.

---

# Geometry source — a_minus.xyz

- Molecule: methylamine (neutral conjugate base), charge 0, 7 atoms
- Source: chemkit build-from-smiles (SMILES `CN`)
- Built: 2026-06-24

Deprotonated conjugate base of the acid in this folder, generated
from its anion SMILES with the chemkit build-from-smiles skill (Open
Babel 3D embedding). Force-field starting geometry, not optimized at
the calculation's level of theory; the pka-acidity task re-optimizes it.

---

# Geometry source — ha.xyz (CORRECTED)

- Molecule: methylammonium cation CH3NH3+, charge +1, 8 atoms
- Source: chemkit build-from-smiles (SMILES `C[NH3+]`)
- Built: 2026-06-24

The protonated form HA of this cationic-acid pKa case. (A prior fetch
wrongly supplied NEUTRAL methylamine as HA from PubChem; the correct HA
is the +1 cation, built here from its SMILES.)
