---
description: Reaction Profile — When the user wants a full reaction-energy diagram with activation energy ΔG‡ and reaction energy ΔG_rxn, plus a verified IRC connectivity check (e.g. "reaction profile", "energy diagram", "compute the activation energy AND the reaction energy", "profile this reaction end-to-end", "publication-ready reaction diagram"). Chains opt(R) + opt(P) + TS search + freq×3 + IRC + diagram PNG. Requires xyz files for the reactant, product, and a TS guess.
---

# Reaction Profile

Run the full reactant → TS → product characterization pipeline in one command.
Outputs ΔE / ΔH / ΔG for both the activation and the overall reaction, an IRC
connectivity verdict confirming the TS actually connects the supplied
stationary points, and a publication-style energy diagram PNG.

This is a **composition skill**: under the hood it calls
`/geometry_optimize`, `/transition_state`, `/vibrational_analysis`, and
`/irc` in a deterministic order with the *same* method/basis/solvent on
every species. The main value over running these by hand is:
1. **Method consistency is enforced** — the single most common error is
   scoring reactants and products with different basis sets.
2. **The IRC connectivity check** verifies the TS actually connects the
   supplied reactant and product (forward IRC endpoint → P, reverse → R, by
   Kabsch RMSD). This is the step every reviewer asks about and that
   ad-hoc workflows skip.
3. **One headline figure** — an annotated three-level energy diagram with
   ΔG‡ and ΔG_rxn labeled.

## Arguments
`$ARGUMENTS` should include:
- `--reactant <path>` (required) — reactant xyz
- `--product <path>` (required) — product xyz
- `--ts-guess <path>` (required) — TS guess xyz (the highest-energy frame
  from a `/conformational_analysis` scan is usually a good starting point;
  otherwise build by hand)
- `--method {xtb,mopac,dft,hf}` (required — if missing, **AskUserQuestion**)
- `--charge N`, `--mult N` (defaults 0, 1; must match across all species)
- `--solvent <name>` (optional)
- `--temperature K` (default 298.15), `--pressure Pa` (default 101325)
- `--rmsd-tol Å` (default 0.5) — IRC-endpoint matching threshold
- `--no-irc` — skip the IRC connectivity check (useful when the IRC backend
  is slow or you're confident in the TS for other reasons; the verdict
  block then omits `irc_connects_R_and_P`)
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`
- HF-only: `--basis <name>`

**Note**: IRC is xtb/mopac only. With `--method dft` or `--method hf`, the
IRC stage is automatically skipped and only the RMSD-based verdict (which
is much weaker) is reported. The typical workflow for DFT-quality profiles
is: run this skill with `--method mopac` first to validate the topology,
then re-run with `--method dft` (which skips IRC but gives the energetics).

**Sella dependency**: the internal TS step uses MOPAC's native saddle-search
for `--method mopac` but requires the Sella package
(`pip install sella`) for `--method xtb`, `--method dft`, and `--method hf`.
If Sella is missing the pipeline will error out at the TS stage; either
install it or fall back to `--method mopac`.

## Atom ordering matters
The Kabsch RMSD used in the IRC check is **not** permutation-invariant —
the reactant, product, TS-guess, and IRC endpoints must all share the same
atom indexing. If you built the three xyz files independently, eyeball the
first few lines of each to confirm the atom order matches before running.

## Cost
The pipeline runs 2 opts + 1 TS + 3 freqs + 1 IRC = ~7 backend
calculations. Rough wall-clock for a 10-atom molecule:
- `--method xtb`: 10–30 s
- `--method mopac`: 30–90 s
- `--method dft --tier fast`: 5–20 min (skips IRC)
- `--method dft --tier standard`: 30 min – 3 h (skips IRC)

## Examples
```bash
# Cheap end-to-end characterization (sanity check)
chemkit profile --method mopac \
  --reactant hcn.xyz --product hnc.xyz --ts-guess hcn_ts.xyz

# DFT-quality energetics (IRC auto-skipped)
chemkit profile --method dft --tier standard --solvent water \
  --reactant reactant.xyz --product product.xyz --ts-guess ts_guess.xyz

# Suggested two-phase workflow:
#   1. Verify topology + IRC connectivity at mopac:
chemkit profile --method mopac --reactant R.xyz --product P.xyz --ts-guess T.xyz
#   2. Refine energetics at DFT using the mopac-optimized geometries:
chemkit profile --method dft --tier standard \
  --reactant R_profile_mopac_reactant_opt.xyz \
  --product  R_profile_mopac_product_opt.xyz \
  --ts-guess R_profile_mopac_ts_opt.xyz
```

## Steps
1. Parse args. Stop if any required xyz missing. If method missing → AskUserQuestion.
2. Run `chemkit profile [...]`. Be patient — this is the slowest individual
   chemkit invocation by design.
3. Read the JSON. Copy the diagram PNG and three `*_opt.xyz` files into the
   cwd next to the user's inputs.
4. Report:
   - **ΔG‡** (activation free energy) and **ΔG_rxn**, both in kcal/mol
   - Also ΔE and ΔH for each
   - **Reverse barrier** ΔG‡_rev = G(TS) − G(P) (for equilibrium / microscopic-
     reversibility checks)
   - **Verdict block**:
     - Is the reactant a true minimum? (n_imaginary_modes == 0 + opt converged)
     - Is the product a true minimum?
     - Is the TS a first-order saddle? (exactly 1 imaginary mode + ts converged)
     - Does IRC connect R and P? (forward + reverse endpoints both within
       `--rmsd-tol` of their target)
   - **is_fully_characterized** — overall yes/no
   - **Path to the diagram PNG** (the headline deliverable; surface this prominently)
   - Paths to all `_opt.xyz` intermediate files
   - Every warning, especially the connectivity failure (which means the
     supplied TS connects different species than the user thought)

## Verdict interpretations
- All four ticks green + `is_fully_characterized: true` → publication-quality
  characterization. Report ΔG‡ as is.
- TS has 0 imag modes → the TS optimizer fell into a nearby minimum. The
  guess geometry was too close to a well; build a better guess (e.g. from a
  dihedral scan).
- TS has >1 imag modes → higher-order saddle. Inspect the imaginary modes;
  often one is a spurious low-frequency rotation. May still be useful if the
  large-magnitude imag mode is clearly the reaction coordinate.
- IRC `connects_R_and_P: false` → the TS connects something other than the
  supplied R/P. Either (a) the wrong stationary points were supplied,
  (b) the TS is for a different rearrangement, or (c) one of the IRC
  trajectories didn't reach a minimum (rare; check the trajectories).
- IRC skipped → verdict relies only on imaginary-mode counts. Mention this
  caveat in the report.

## Reporting policy
- **Never automatically provide experimental or literature data for comparison.** Report only the values this calculation produced. Do not volunteer "accepted", measured, or reference values, and do not editorialize about agreement with experiment. Only include an experimental comparison if the user explicitly asks for one.

## Errors
- Backend missing → install (xtb / mopac / pyscf).
- Sella missing for `--method xtb/dft/hf` TS → `pip install sella`.
- "TS search did not converge" → supply a closer guess (use
  /conformational_analysis to find an energy maximum along the relevant
  internal coordinate).

## Notes
- The diagram is matplotlib at 150 dpi — fine for slides, fine for most
  papers. If you need vector output, regenerate with a one-off script using
  the `delta_G_*` numbers from the JSON.
- Reverse-direction ΔG‡_rev is computed automatically; useful for
  equilibrium-constant sanity checks (ΔG_rxn = ΔG‡_fwd − ΔG‡_rev).
- The temperature in the diagram is whatever you passed (`--temperature`,
  default 298.15 K). For non-standard conditions, re-run.

## Running this skill

This skill is a single self-contained script. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python reaction_profile.py --help                 # full argument list
```

The chemistry engine is inlined into `reaction_profile.py`; no other files are required.

### In-terminal 3D view (asciimol)

When run on an interactive terminal with `asciimol` installed, this skill opens
the resulting geometry in an ASCII 3D viewer automatically (press `q` to quit).
Pass `--no-view` to disable it. The viewer never launches in non-interactive
runs (pipes, tests, agent automation), so it is safe to script.

```bash
pip install asciimol     # one-time: enables the in-terminal viewer
```
