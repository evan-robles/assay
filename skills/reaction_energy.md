---
description: Reaction Energy — When the user wants ΔE, ΔH, or ΔG for a balanced chemical reaction (e.g. "reaction energy", "ΔG of reaction", "ΔH_rxn", "enthalpy of reaction", "is this exo/endothermic", "is this reaction spontaneous", "thermodynamics of A + B → C"). Composes opt + freq across every species and enforces method/basis/solvent consistency. For an activation energy (TS), use transition_state + vibrational_analysis or the reaction_profile skill.
---

# Reaction Energy

Compute ΔE / ΔH / ΔG for a stoichiometrically balanced reaction by evaluating
each species at the **same** level of theory and subtracting products from
reactants.

Three modes, from cheap to thorough:
- `--mode sp` (default) — single point on each input xyz. Returns ΔE only.
  Use when you already have optimized geometries.
- `--mode opt` — optimize each species first, then SP. Returns ΔE on the
  relaxed geometries. Use when input geometries are sketch-quality.
- `--mode freq` — full opt + freq on each species. Returns ΔE, ΔH(T), ΔG(T)
  at the requested temperature. Use whenever ΔG matters (i.e. almost always
  for "is this reaction spontaneous").

## Arguments
`$ARGUMENTS` should include:
- `--reactant SPEC` (required, repeatable, ≥1)
- `--product SPEC` (required, repeatable, ≥1)
- `--method {xtb,mopac,dft,hf}` (required — if missing, **AskUserQuestion**)
- `--mode {sp,opt,freq}` (default `sp`)
- `--solvent <name>` (optional)
- `--temperature <K>` (default 298.15, only used with `--mode freq`)
- `--pressure <Pa>` (default 101325, only used with `--mode freq`)
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

### Species spec syntax
`[COEF*]PATH[,charge=Q][,mult=M]`

| Spec | Coef | Charge | Mult |
|---|---|---|---|
| `h2.xyz` | 1 | 0 | 1 |
| `2*h2.xyz` | 2 | 0 | 1 |
| `acetate.xyz,charge=-1` | 1 | -1 | 1 |
| `3*radical.xyz,mult=2` | 3 | 0 | 2 |
| `complex.xyz,charge=-2,mult=3` | 1 | -2 | 3 |

The top-level `--charge` flag on the CLI is **ignored** for `rxn-energy`;
each species carries its own charge in the spec, because reactants and
products can have different charges (acid-base, redox, fragmentation).

## Examples
```bash
# H2 + 1/2 O2 → H2O at xtb (stoichiometry: 2 H2 + O2 → 2 H2O)
chemkit rxn-energy --method xtb --mode opt \
  --reactant '2*h2.xyz' --reactant o2.xyz,mult=3 \
  --product  '2*h2o.xyz'

# ΔG of dimerization in water at DFT
chemkit rxn-energy --method dft --tier standard --solvent water --mode freq \
  --reactant '2*monomer.xyz' --product dimer.xyz
```

## Refuses
- Charge imbalance is flagged as a warning, not a hard refusal — many real
  reactions are written with a counter-ion or H⁺/e⁻ implicit. Surface the
  warning but proceed.
- Atom-count imbalance per element is also flagged as a warning. Almost
  always indicates a user error; mention it prominently.

## Steps
1. Parse `$ARGUMENTS`. If `--reactant` or `--product` missing → stop and ask.
   If method missing → AskUserQuestion (header "Method").
2. Run `chemkit rxn-energy --method <M> --mode <MODE> --reactant ... --product ... [...]`.
3. Read the JSON. Copy to a sensible filename in the cwd (default name uses
   the first reactant's stem).
4. Report:
   - The balanced reaction string (e.g. `2 H2 + O2 → 2 H2O`)
   - **ΔE** (always), in kcal/mol and eV
   - **ΔH** and **ΔG** (when `--mode freq`)
   - For each species: energy / enthalpy / G + a converged-yes/no
   - Method, solvent, temperature, pressure (when applicable)
   - **Sign convention reminder**: negative ΔG → spontaneous (products favored
     at equilibrium); negative ΔH → exothermic.
   - Surface every warning from the JSON, especially atom/charge imbalance
     and any imaginary-mode flags from the freq step.

## Caveats
- The "same method" constraint is enforced by the CLI — you pass one `--method`
  and every species uses it. The most common error this prevents: scoring
  reactants at one functional and products at another.
- For `--mode freq`, every species runs a preopt + Hessian; ΔG accuracy
  depends on each species being a true minimum (n_imaginary_modes == 0).
  Imaginary modes are flagged in warnings.
- Semi-empirical reaction energies are screening-grade (±3–5 kcal/mol typical).
  For publication numbers use `--method dft --tier standard` (or accurate).
- For an **activation energy** ΔG‡, this skill is not the right tool — use
  `/transition_state` + `/vibrational_analysis` on the saddle, or the
  composite `/reaction_profile` skill.

## Errors
- Mismatched stoichiometry → check the `balance` block in the JSON; the
  per-element difference is reported.
- A species file missing → fix the path in the spec.
- Backend dependency missing → `conda install -c conda-forge xtb-python mopac`
  or `pip install pyscf`.
