---
description: Transition State Search — When the user wants to locate the saddle point (first-order transition state) for a chemical reaction (e.g. "find the TS", "transition state", "saddle point", "TS search", "locate TS", "reaction barrier geometry", "activation energy geometry"). Requires a reasonable TS guess geometry as input. Do NOT use for minima — that's /geometry_optimize. After finding the TS, use /irc to confirm which reactants and products it connects.
---

# Transition State Search

Locate a first-order saddle point starting from a TS-guess geometry. Use the
energy-maximum frame of a /conformational_analysis dihedral scan as a good
guess for rotation barriers; otherwise build the guess by hand.

By default MOPAC's native EF saddle-search drives the optimization (most
reliable for PM7); the xtb backend requires the Sella optimizer to be
installed.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path with the TS guess (required)
- A method: `xtb` or `mopac` (required — if missing, default to `mopac` since
  the xtb path needs Sella to be installed separately)
- Optional: `--solvent <name>`, `--charge N`, `--mult N`, `--steps N` (default 500),
  `--no-verify-freq` to skip the post-TS frequency verification

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion (header "Method", default `mopac`).
2. Run `chemkit ts --method <METHOD> [--solvent <S>] [--charge <Q>] [--mult <M>] <XYZ>`.
3. Read the printed JSON. Copy to `<basename>_ts_<method>.json` in the cwd. The CLI also writes a `<basename>_ts_<method>.xyz` with the converged TS geometry; copy it next to the user's input.
4. Report:
   - **Converged?** (true/false) and the optimizer status message
   - **Heat of formation** (kcal/mol, MOPAC only) and total energy (eV)
   - **Verification freq** results:
     - **Is this a valid TS?** (yes iff `verify_freq.n_imaginary_modes == 1`)
     - **Imaginary frequency** (cm⁻¹) of the reaction-coordinate mode
     - **Number of imaginary modes** — flag explicitly if 0 (geometry collapsed to a minimum) or >1 (higher-order saddle).
   - Path to the saved TS xyz
5. Recommend running `/irc` next to confirm which reactant and product the TS connects.

## Errors
- `xtb` selected but Sella not installed → suggest `pip install sella` or fall back to `--method mopac`.
- MOPAC TS did not converge → likely the input is too far from the saddle; recommend running a /conformational_analysis to find an energy maximum as a better guess.
