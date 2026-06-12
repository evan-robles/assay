---
description: Transition State Search — When the user wants to locate the saddle point (first-order transition state) for a chemical reaction (e.g. "find the TS", "transition state", "saddle point", "TS search", "locate TS", "reaction barrier geometry", "activation energy geometry"). Requires a reasonable TS guess geometry as input. Do NOT use for minima — that's /geometry_optimize. After finding the TS, use /irc to confirm which reactants and products it connects.
---

# Transition State Search

Locate a first-order saddle point starting from a TS-guess geometry. Use the
energy-maximum frame of a /conformational_analysis dihedral scan as a good
guess for rotation barriers; otherwise build the guess by hand.

By default MOPAC's native EF saddle-search drives the optimization (most
reliable for PM7); the xtb, dft, and hf backends use the Sella optimizer,
which must be installed separately (`pip install sella`).

## Dependencies
- `--method mopac` — uses MOPAC's native `TS` keyword. **No extra install.**
- `--method xtb`, `--method dft`, `--method hf` — require **Sella**, which
  is not bundled with chemkit. Install once with `pip install sella`. If
  Sella is missing the task errors out immediately with a clear message.

## Arguments
`$ARGUMENTS` should include:
- An `.xyz` path with the TS guess (required)
- `--method {xtb,mopac,dft,hf}` (required — if missing, default to `mopac` since
  every other backend needs Sella to be installed separately)
- Optional: `--solvent <name>`, `--charge N`, `--mult N`, `--steps N` (default 500),
  `--no-verify-freq` to skip the post-TS frequency verification
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

DFT/HF TS searches are 10–100× slower than MOPAC and benefit a lot from a
high-quality guess. Default workflow: locate the saddle with `--method mopac`,
then `/geometry_optimize` the converged geometry once at `--method dft --tier fast`
to refine it.

## Steps
1. Parse `$ARGUMENTS`. If `.xyz` missing → stop and ask. If method missing → AskUserQuestion (header "Method", default `mopac`).
2. Run `chemkit ts --method <METHOD> [--tier <T>] [--functional <F>] [--basis <B>] [--solvent <S>] [--charge <Q>] [--mult <M>] <XYZ>`.
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

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- `xtb`/`dft`/`hf` selected but Sella not installed → suggest `pip install sella` or fall back to `--method mopac`.
- pyscf not installed → `pip install pyscf` (required for `--method dft` or `--method hf`).
- MOPAC TS did not converge → likely the input is too far from the saddle; recommend running a /conformational_analysis to find an energy maximum as a better guess.
