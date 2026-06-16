---
name: fukui-reactivity
description: Rank the atoms in a molecule by electrophilic, nucleophilic, and radical reactivity using condensed Fukui functions and the Morell dual descriptor.
category: chemistry
---

# Fukui Reactivity (Atom-Level)

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
Compute atom-resolved reactivity from three finite-difference partial-charge calculations on the **same** geometry — neutral (N), cation (N−1), anion (N+1) — yielding the condensed Fukui functions $f^+$ (electrophilic site), $f^-$ (nucleophilic site), $f^0$ (radical site), and the Morell dual descriptor $f^+ - f^-$. This identifies which atom is most reactive; for a global molecule-level picture ($\eta$, $\omega$, $\chi$) use [frontier-orbitals](../frontier-orbitals/SKILL.md) instead.

| Index | Formula | Interpretation |
|---|---|---|
| $f^+_k$ | $q_k(N) - q_k(N{+}1)$ | electrophilic site — attacked by nucleophiles |
| $f^-_k$ | $q_k(N{-}1) - q_k(N)$ | nucleophilic site — attacked by electrophiles |
| $f^0_k$ | $\tfrac{1}{2}(f^+_k + f^-_k)$ | radical-attack site |
| $\mathrm{dual}_k$ | $f^+_k - f^-_k$ | $>0$ → electrophilic; $<0$ → nucleophilic |

## Instructions
```bash
# Env: anl_env
python skills/fukui-reactivity/scripts/fukui-reactivity.py [args]
```

Arguments:
- An `.xyz` path (required — supply an already-optimized geometry; run [geometry-optimize](../geometry-optimize/SKILL.md) first if needed).
- `--method {xtb,mopac,dft,hf}` (required — if missing, ask).
- `--charge`, `--mult` (of the neutral reference; defaults 0 / 1).
- `--cation-mult`, `--anion-mult` (default 2 / 2 — correct for a closed-shell parent; override for open-shell parents).
- `--solvent <name>`.
- `--no-plot` — skip the PNG bar chart.
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`. **`--density-fit`** enables RI density fitting (~3-10x faster SCF, ~0.1-0.8 mEh error); OFF by default — chemkit uses exact integrals (plain RKS/UKS, matching hand-run PySCF).
- HF-only: `--basis <name>`.
- `--out <path>` (result JSON; default `<stem>_fukui_<method>.json` in the run cwd).

If the `.xyz` is missing → stop and ask. If `--method` is missing, ask.

Then read the JSON and report: the most electrophilic atom (largest $f^+$ — symbol, 1-based index, value); the most nucleophilic atom (largest $f^-$); the full per-atom table (index, symbol, $f^+$, $f^-$, dual — sort by $|\mathrm{dual}|$ descending if compact); the PNG path (if plotting was on); the partial-charge scheme (Mulliken for both backends); and any warning, especially the "Σ f± ≠ 1.0" charge-conservation drift, which usually indicates an SCF problem in an N±1 state.

For open-shell parents (radicals) set `--mult 2` and pick `--cation-mult`/`--anion-mult` so each adds/removes a single electron with the right total spin.


> **Result reading (token-efficient, required):** run with `--out <path> --stdout path` so stdout is a one-line pointer, then read back only the fields you need with `jq` (always include `warnings` and the convergence flag). Surface the live `.out` log path the moment the run starts so the user can `tail -f` it. See [RESULT-READING.md](../RESULT-READING.md).

## Examples
```bash
# Env: anl_env
python skills/fukui-reactivity/scripts/fukui-reactivity.py acrolein.xyz --method xtb
```

See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required.
- **Single-point, not optimized**: the skill does NOT optimize — it runs three single-points on the supplied geometry. Supply an already-optimized xyz.
- **Interpretation**: condensed Fukui from Mulliken charges is basis-set-dependent and somewhat noisy — interpret as rankings between atoms within one molecule, not absolute numbers across molecules.
- **Open-shell parents**: set `--mult` and the cation/anion multiplicities explicitly.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison; report only computed values; compare to experiment only if the user explicitly asks.
- **Install/availability**: `conda install -c conda-forge xtb-python mopac`; `pip install pyscf` for `--method dft` or `--method hf`. Σ f± drift > 0.05 → an SCF likely diverged; try a different solvent setting or fall back to gas phase.

## References
- Yang, Parr, Pucci. *J. Chem. Phys.* **1984**, 81, 2862. https://doi.org/10.1063/1.447964
- Bannwarth, Ehlert, Grimme. *J. Chem. Theory Comput.* **2019**, 15, 1652. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. *J. Mol. Model.* **2013**, 19, 1. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. *J. Chem. Phys.* **2020**, 153, 024109. https://doi.org/10.1063/5.0006074
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV conversion (1 Eh = 27.211386245981 eV) used to report condensed Fukui/orbital energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
