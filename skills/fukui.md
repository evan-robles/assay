---
description: Fukui Functions + Dual Descriptor (atom-resolved reactivity) — When the user wants to know which atom in a molecule is most electrophilic / nucleophilic / radical-prone (e.g. "Fukui", "fukui function", "f+", "f-", "f0", "dual descriptor", "where is this molecule most reactive", "atom-level reactivity", "Morell dual descriptor"). Single-point — does NOT optimize. For a global / molecule-level reactivity picture (η, ω, χ), use /frontier_orbitals instead.
---

# Condensed Fukui Functions + Dual Descriptor

Atom-level reactivity from three finite-difference partial-charge calculations on the **same geometry**: neutral (N), cation (N−1), anion (N+1).

| Index | Formula | Interpretation |
|---|---|---|
| **f⁺_k** | q_k(N) − q_k(N+1) | electrophilic site — attacked by nucleophiles |
| **f⁻_k** | q_k(N−1) − q_k(N) | nucleophilic site — attacked by electrophiles |
| **f⁰_k** | ½(f⁺_k + f⁻_k) | radical-attack site |
| **dual_k** | f⁺_k − f⁻_k | Morell: > 0 → electrophilic; < 0 → nucleophilic |

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path (required — already-optimized geometry recommended; use `/geometry_optimize` first if needed)
- A method: `xtb` or `mopac` (required — if missing, **AskUserQuestion**)
- Optional: `--charge`, `--mult` (of the neutral reference; defaults 0 / 1)
- Optional: `--cation-mult`, `--anion-mult` (default 2 / 2 — correct for a closed-shell parent; **override** for open-shell parents)
- Optional: `--solvent <name>`
- Optional: `--no-plot` (skip the PNG bar chart)

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion.
2. Run `chemkit fukui --method <M> [--solvent <S>] [--charge <Q>] [--mult <M>] <XYZ>`.
3. Read JSON. Copy to `<basename>_fukui_<method>.json` in cwd.
4. Report:
   - **Most electrophilic atom** (largest f⁺) — symbol, 1-based index, f⁺ value
   - **Most nucleophilic atom** (largest f⁻) — symbol, 1-based index, f⁻ value
   - Full per-atom table: index, symbol, f⁺, f⁻, dual (markdown table — sort by |dual| descending if compact)
   - PNG path (if plotting was on)
   - Partial-charge scheme (Mulliken for both backends)
   - Any warnings — especially the "Σ f± ≠ 1.0" charge-conservation drift, which usually indicates an SCF problem in the N±1 state.

## When to use vs /frontier_orbitals
- **/frontier_orbitals** → global reactivity descriptors (HOMO, LUMO, η, ω, χ) — "is this molecule electrophilic overall?"
- **/fukui** → atom-level — "**which atom** is most electrophilic?"

Both are Koopmans-style finite-difference quantities; they're complementary.

## Common gotchas
- For **open-shell parents** (radicals), set `--mult 2` and pick `--cation-mult`/`--anion-mult` such that each adds/removes a single electron with the right total spin.
- Condensed Fukui from Mulliken charges is basis-set-dependent and somewhat noisy — interpret as **rankings** between atoms in one molecule, not absolute numbers between molecules.

## Errors
- xtb-python / MOPAC missing → install via `conda install -c conda-forge xtb-python mopac`.
- Σ f± drifts > 0.05 → SCF in one of the trio likely diverged or converged to a bad state; try a different solvent setting or fall back to gas phase.
