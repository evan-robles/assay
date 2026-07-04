---
name: reaction-profile
description: Characterize a reaction end-to-end to produce activation and reaction free energies, an IRC connectivity verdict, and an annotated energy diagram.
category: chemistry
---

# Reaction Profile

> [!IMPORTANT]
> **Before running — confirm the level of theory; never guess.** If the user did
> not specify `--method` (xtb | mopac | dft | hf) — and, where relevant,
> `--functional`/`--basis`/`--tier`, `--solvent` (or explicit gas phase),
> `--charge`, `--mult` — **stop and ask the user** (do not silently default or
> carry over the previous run's choice). The engine refuses a DFT/HF run that
> omits the consequential knobs unless you pass `--accept-defaults`.
> **At launch, immediately give the user the live `.out` log path and offer
> `tail -f`** — do not wait for the run to finish. (calculation-reporting-standards
> non-negotiables #10 and #9.)

## Goal
Run the full reactant → TS → product pipeline in one command and report $\Delta G^\ddagger$ (activation free energy) and $\Delta G_\mathrm{rxn}$ (reaction free energy), an IRC connectivity verdict confirming the transition state actually connects the supplied stationary points, and a publication-style three-level energy diagram. The single method/basis/solvent is enforced across every species so reactants and products are never scored inconsistently.

## Instructions
This is a composition skill: it chains opt(reactant) + opt(product) + TS search + freq×3 + IRC in a deterministic order, then emits the diagram. Run:

```bash
# Env: anl_env
python skills/reaction-profile/scripts/reaction-profile.py [args]
```

Arguments:
- `--reactant <path>` (required) — reactant xyz.
- `--product <path>` (required) — product xyz.
- `--ts-guess <path>` (required) — TS guess xyz (the highest-energy frame from a [conformational-analysis](../conformational-analysis/SKILL.md) scan is a good starting point; otherwise build by hand).
- `--method {xtb,mopac,dft,hf}` (required — if missing, ask).
- `--charge N`, `--mult N` (defaults 0, 1; must match across all species).
- `--solvent <name>` (optional).
- `--temperature K` (default 298.15), `--pressure Pa` (default 101325).
- `--out <path>` (result JSON; default `<reactant-stem>_profile_<method>.json` in the run cwd).
- `--rmsd-tol Å` (default 0.5) — IRC-endpoint matching threshold.
- `--no-irc` — skip the IRC connectivity check (the verdict then omits `irc_connects_R_and_P`).
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. **`--density-fit`** enables RI density fitting (~3-10x faster SCF, ~0.1-0.8 mEh error); OFF by default — chemkit uses exact integrals (plain RKS/UKS, matching hand-run PySCF).
- HF-only: `--basis <name>`.

Stop if any required xyz is missing. If `--method` is missing, ask.

**Atom ordering matters**: the Kabsch RMSD used in the IRC check is not permutation-invariant — reactant, product, TS-guess, and IRC endpoints must share the same atom indexing. Eyeball the first lines of each xyz before running.

Then read the JSON and report: $\Delta G^\ddagger$ and $\Delta G_\mathrm{rxn}$ (kcal/mol), plus $\Delta E$ and $\Delta H$ for each; the reverse barrier $\Delta G^\ddagger_\mathrm{rev} = G(\mathrm{TS}) - G(\mathrm{P})$; the verdict block (reactant a true minimum, product a true minimum, TS a first-order saddle with exactly one imaginary mode, and whether IRC connects R and P within `--rmsd-tol`); the overall `is_fully_characterized` flag; the path to the diagram PNG (the headline deliverable — surface it prominently); paths to all `_opt.xyz` files; and every warning from the result JSON, reproduced verbatim — none dropped, summarized, or paraphrased (call out an IRC connectivity failure especially); if there are no warnings, say so.

Verdict interpretation: TS with 0 imaginary modes → optimizer fell into a nearby minimum, build a better guess; TS with >1 imaginary modes → higher-order saddle, inspect the modes; IRC `connects_R_and_P: false` → the TS connects different species than supplied; IRC skipped → verdict relies on imaginary-mode counts only, note this caveat.

Related skills: [geometry-optimize](../geometry-optimize/SKILL.md), [transition-state](../transition-state/SKILL.md), [vibrational-analysis](../vibrational-analysis/SKILL.md), [intrinsic-reaction-coordinate](../intrinsic-reaction-coordinate/SKILL.md).


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

> **Skill name / discovery.** This skill's engine subcommand is `profile` (`reaction-profile` is an accepted alias). Do **not** invent flags: gas phase is the default (or `--solvent none`); there is no `--phase`/`--environment` flag, and each geometry is passed with `--reactant`/`--product`/`--ts-guess` — not `--geometry`/`--xyz`/`--input`. If unsure of the exact name or flags, run `chemkit --list-skills` or `chemkit reaction-profile --help-json` (or `--help`) to discover them instead of guessing.

## Examples
```bash
# Env: anl_env
python skills/reaction-profile/scripts/reaction-profile.py --method mopac \
  --reactant hcn.xyz --product hnc.xyz --ts-guess hcn_ts.xyz
```

Suggested two-phase workflow: validate topology + IRC connectivity at `--method mopac` first, then refine energetics with `--method dft --tier standard` using the mopac-optimized geometries (IRC is auto-skipped at DFT/HF).

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required.
- **Inputs**: requires reactant, product, and TS-guess xyz files; charge/mult must match across all species.
- **IRC availability**: IRC is xtb/mopac only. With `--method dft` or `--method hf` the IRC stage is auto-skipped and only the weaker RMSD verdict is reported.
- **Sella dependency**: the internal TS step uses MOPAC's native saddle-search for `--method mopac`, but requires the Sella package (`pip install sella`) for `--method xtb/dft/hf`. If Sella is missing, the pipeline errors at the TS stage — install it or fall back to `--method mopac`.
- **Cost**: ~7 backend calculations (2 opt + 1 TS + 3 freq + 1 IRC); the slowest single invocation by design.
- **Diagram**: matplotlib at 150 dpi; regenerate from the JSON `delta_G_*` numbers if vector output is needed. Temperature in the diagram is whatever was passed via `--temperature`.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison; report only computed values; compare to experiment only if the user explicitly asks.
- **Install/availability**: backends via `conda install -c conda-forge xtb-python mopac` or `pip install pyscf`. "TS search did not converge" → supply a closer guess.

## References
- Bannwarth, Ehlert, Grimme. *J. Chem. Theory Comput.* **2019**, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. *J. Mol. Model.* **2013**, 19, 1. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- Larsen et al. *J. Phys.: Condens. Matter* **2017**, 29, 273002. https://doi.org/10.1088/1361-648X/aa680e
- Hermes, Sargsyan, Schaefer. *J. Chem. Theory Comput.* **2019**, 15, 6536. https://doi.org/10.1021/acs.jctc.9b00869
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV (1 Eh = 27.211386245981 eV) and eV→kcal/mol (23.060547830619) conversions used to report barrier/reaction energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
