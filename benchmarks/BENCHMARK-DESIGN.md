# chemkit Benchmark Design (paper, v0.1)

**Purpose.** Turn the per-skill one-off example validations into a *systematic,
statistical* accuracy benchmark for the arXiv paper ("agent-driven, reproducible,
honest computational chemistry"). The benchmark serves three jobs:

1. **Accuracy table** — computed-vs-reference error (MAE/RMSE/max) per property
   class and per method, so the paper reports honest error bars instead of
   anecdotes.
2. **Honesty substrate** — the same curated, link-checked reference set is reused
   by the integrity red-team study (Wk3–4): every reference value here has cleared
   `research-standards.md`, so we can show the agent reporting them correctly *and*
   refusing the fabricated ones.
3. **Reproducibility** — one curated input set + one driver script = a number
   anyone can regenerate (Wk6 repro package).

> **Binding rules.** Every reference value MUST pass `rules/research-standards.md`
> (live DOI/URL check + Crossref metadata match + value read from source + ACS
> citation + honest experimental-vs-computational provenance). Every computed
> number MUST be reported per `rules/calculation-reporting-standards.md` (method
> block, charge/mult, solvent or "gas phase", convergence, warnings). No value
> enters a table until both gates pass.

---

## 1. Scope decisions

- **Methods compared:** `xtb` (GFN2-xTB) and `dft` (tier `standard` =
  B3LYP/def2-TZVP) as the two primary columns; `mopac` (PM7) and `hf` as
  secondary columns where chemically meaningful. Not every property runs every
  method (e.g. PM7 ΔH_f is not comparable to ab initio total energies — keep it in
  its own column, never subtract across methods).
- **Set size:** target **20–50 systems per property class**, biased toward small,
  rigid, well-measured molecules so the *method* (not conformer sampling) is what
  the error measures. Honestly cap and disclose where curation of verifiable
  references runs out (per `research-standards.md` §11 — report the gap, don't pad).
- **Provenance discipline:** each row tags the reference as `experimental`,
  `benchmark/computational` (e.g. CCSD(T) reference geometry), or `database`. We do
  NOT mix these silently in an error statistic — report MAE within a provenance
  class.

---

## 2. Property classes, skills, metrics, reference sources

Grade = how defensible the *accuracy claim* is for the paper. Honesty about this
is itself a selling point of the paper.

| # | Property class | Skill(s) | Metric | Primary reference source | Grade |
|---|----------------|----------|--------|--------------------------|-------|
| A | Equilibrium geometry | `geometry-optimize` | MAE in bond length (Å), angle (°) vs. gas-phase structures | Experimental (microwave/IR) primary papers; CCSD(T) ref geometries labeled as computational | **Defensible** |
| B | Vibrational frequencies | `vibrational-analysis` | MAE in harmonic ν (cm⁻¹), ZPE | Experimental gas-phase fundamentals (note: harmonic-vs-fundamental gap disclosed) | **Defensible (with anharmonic caveat)** |
| C | Dipole moment | `electrostatics` | MAE (Debye) | Experimental gas-phase dipoles (primary) | **Defensible** |
| D | Reaction energy (ΔH/ΔG) | `reaction-energy` | MAE (kcal/mol) vs. ΔH_f cycles | NIST/ATcT enthalpies of formation (database, labeled) | **Defensible (xtb screening; DFT mid)** |
| E | Atomization / bond dissoc. | `single-point-energy` + `reaction-energy` | MAE (kcal/mol) | ATcT / NIST (database) | **Mid** |
| F | Conformer relative energy | `conformer-search`, `conformational-analysis` | MAE (kcal/mol) of rotamer gaps & barriers | Experimental rotational-barrier / Δenergy papers | **Screening** |
| G | pKa (aqueous) | `pka-acidity` | MAE (pKa units), reference-anchored mode | Experimental pKa primary/IUPAC compilation (labeled) | **Screening** |
| H | Redox potential | `redox-potential` | MAE (V vs. SHE) | Experimental E° (primary; conditions carried) | **Screening** |
| I | logP (octanol-water) | `logp-partition` | MAE (log units) | Experimental logP (primary/database, labeled) | **Screening** |
| J | Solvation free energy | `solvation` | MAE (kcal/mol) | Experimental ΔG_hyd (FreeSolv traces to primary) | **Mid** |
| K | Reaction barrier / TS | `transition-state`, `reaction-profile`, `intrinsic-reaction-coordinate` | ΔG‡ error (kcal/mol); 1-imaginary-mode check | Experimental/benchmark barriers (e.g. inversions) | **Screening** |
| L | Frontier orbitals | `frontier-orbitals`, `single-point-energy` | HOMO vs. −IP (Koopmans) trend (eV) | Experimental vertical IP (primary) | **Qualitative/trend only** |
| M | Binding energy | `binding-energy` | MAE (kcal/mol) | Benchmark interaction energies (e.g. A24/S22 subset, computational — labeled) | **Mid** |
| N | Fukui / reactivity | `fukui-reactivity` | Correct site ID (categorical accuracy) | Textbook/established regiochemistry (qualitative) | **Qualitative** |

`build-from-smiles`, `name-to-smiles`, `visualize-orbitals` are infrastructure
(no measured property) — validated by identity/sanity checks, not in the error
tables. Mention them as "supporting skills," not benchmark rows.

---

## 3. Candidate molecule sets (to be curated + verified in Wk1–2)

Small, well-measured anchors first; expand to hit 20–50/class only with
verifiable references.

- **A/B/C (geometry, freq, dipole):** H2O, NH3, CH4, CO, CO2, H2CO, CH3OH, C2H4,
  C2H2, HCN, H2O2, N2, F2, HF, HCl, CH3F, CH3Cl, benzene, ethane, formic acid.
  (Diatomics + small polyatomics dominate the verifiable-reference space.)
- **D/E (reaction/atomization):** Haber (already an example), combustion of CH4,
  hydrogenation of ethylene/acetylene, isomerizations with ATcT data.
- **F (conformers):** butane, n-pentane, ethanol, 1,2-dichloroethane, glycol,
  biphenyl torsion.
- **G (pKa):** acetic/formic/benzoic acids, phenol, methylamine, pyridine,
  imidazole — reference-anchored to one trusted experimental acid per family.
- **H (redox):** quinone/hydroquinone family, ferrocene (Fc reference), TEMPO,
  nitrobenzene — disclose pH/electrode/solvent for every E°.
- **I (logP):** ethanol, methanol, benzene, toluene, phenol, aniline, pyridine —
  small molecules with trustworthy experimental logP.
- **J (solvation):** FreeSolv subset traced to primary measurements.
- **K (barriers):** NH3 inversion (have it), HCN↔HNC, ethane rotation, SN2 model.
- **M (binding):** water dimer (have it), methane dimer, ammonia dimer, a few S22
  members (label interaction energies as high-level *computational* benchmarks).

---

## 4. Reference-curation procedure (per value, BEFORE it enters a table)

For each (system, property) target:

1. Find the candidate reference (Crossref / NIST WebBook / PubChem / primary paper).
2. **Live link check:** `curl -sIL` the DOI/URL → require final 2xx.
3. **Metadata match:** Crossref title/year/journal must match the citation.
4. **Value read:** open the source; confirm the number, **units**, and
   **conditions** (T, phase, solvent, electrode, reference state) are present.
5. **Provenance tag:** experimental / computational-benchmark / database.
6. Record an ACS citation + a `[verified: ... , YYYY-MM-DD]` provenance tag.
7. If any step fails → **drop the row** and log it in `UNVERIFIED.md` (§11
   not-found protocol). Do not pad, do not guess.

Store curated references in `benchmarks/references.bib` (or `references.md`, ACS
format) keyed by system+property, plus a machine-readable
`benchmarks/reference_values.json`:

```json
{
  "h2o": {
    "bond_length_OH_angstrom": {"value": 0.9572, "uncertainty": null,
      "provenance": "experimental", "conditions": "gas phase",
      "citation_key": "benedict1956", "verified": "DOI 200 + Crossref match, 2026-06-17"},
    "hoh_angle_deg": {"value": 104.5, "provenance": "experimental", ...}
  }
}
```

---

## 5. Driver + outputs

- `benchmarks/run_benchmark.py` (Wk2): iterates the curated input `.xyz` set ×
  methods × skills, writes each result JSON to `benchmarks/results/<sys>_<prop>_<method>.json`
  with `--out` + suppressed stdout (per reporting-standards §9.1), and reads back
  only the needed fields via `jq`/json (always incl. `converged` + `warnings`).
- `benchmarks/score.py` (Wk2–3): joins `results/` to `reference_values.json`,
  emits per-class MAE/RMSE/max tables (`benchmarks/tables/*.csv`) and the paper
  plots (computed-vs-reference scatter, error-by-method bars).
- All artifacts (input xyz, result json, tables, png) are retained per repo
  artifact-retention policy.

---

## 6. Honesty controls baked into the benchmark

- Screening-grade classes (F/G/H/I/K) are reported **as screening-grade**, with
  larger expected error, never as publication-accuracy claims.
- xtb and MOPAC energy zeros are never subtracted across methods.
- Harmonic-vs-fundamental, implicit-only solvation, single-conformer, and
  density-fitting caveats are stated next to the affected tables.
- Every dropped/unverifiable reference is logged in `benchmarks/UNVERIFIED.md`,
  and the paper states the curation yield (how many candidates survived the gate).

---

## 7. Sweep-results integrity gate (BEFORE committing any summary.csv)

> **Binding.** A fidelity sweep runs unattended across many short PBS
> allocations (see `tools/aurora_*`). Allocations expire and nodes die mid-run,
> so a raw `summary.csv` can be contaminated by **infrastructure artifacts** that
> masquerade as model failures. NEVER commit a sweep `summary.csv` (or report its
> pass-rates) until it passes BOTH checks below. This exists because the
> 2026-07-03 fukui run initially reported gpt-4o=0.22 / gpt-4.1-nano=0.20, which
> were dominated by dead-node artifacts, not model behavior.

**Check 1 — purge dead-node artifacts (rc=255).** When `CHEMKIT_REMOTE_HOST`
(the compute node) dies mid-run, the engine `ssh` fails at the transport layer
(**rc=255** / "connection refused" / "no route to host" / "timed out") *before
any chemistry runs*. The agent is handed that error, writes a "calculation
failed" report, and it scores as a bogus `engine_s=0.0` FAIL. These are NOT model
failures.
- Scan every FAIL run's `transcript.json` for a tool result containing
  `"engine run failed (rc=255)"` (equivalently, a new-driver `result.json` with
  `error="remote_host_unreachable"`).
- **Delete** those run dirs and **re-run** the freed slots on a LIVE allocation
  (the fixed `fidelity_driver.py` now flags them ERROR/exit-2 and
  `parallel_suite.sh` `_completed_runs` no longer counts them, so a clean re-run
  refills them automatically).
- Re-scan until **zero rc=255** remain, THEN regenerate `summary.csv`.

**Check 2 — confirm every rc=1 (engine-ran) FAIL is scientifically valid.** A
FAIL where the engine actually executed and exited nonzero (**rc=1**, engine
`.out` present) must be inspected — it is only legitimate benchmark data if it
reflects genuine *model* behavior, not a harness/spec bug.
- Read each rc=1 run's engine `.out` and the agent's `chemkit` tool `args`.
- **Valid (KEEP):** the model made a real fidelity error — e.g. it invented a
  nonexistent CLI flag (`--phase`, `--geometry`, `--convergence`,
  `--environment` were seen on 2026-07-03) that argparse rejected, or passed a
  wrong charge/mult. These SHOULD score FAIL; they are the signal the benchmark
  measures.
- **Invalid (FIX, don't keep):** the failure is a spec/engine/harness defect (a
  correct invocation the engine mishandled, a bad reference input, an env
  problem) — fix the root cause and re-run; do not let it count against the model.
- Record the rc=1 classification (which molecules, which flag/mistake) in the
  commit message so the pass-rate is auditable.

Only after Check 1 = 0 remaining and every Check 2 rc=1 is confirmed valid may
the corrected `summary.csv` be committed.

---

## 8. Wk1 action checklist

- [ ] Approve property-class list (§2) and per-class target N.
- [ ] Lock the anchor molecule list (§3) and gather `.xyz` inputs via
      `build-from-smiles` (never hand-written coordinates).
- [ ] Curate + verify the first ~10 reference values end-to-end (§4) as a
      pipeline shakedown; populate `reference_values.json` + `references.md`.
- [ ] Re-verify the two existing `[CITATION UNVERIFIED]` example refs
      (electrostatics dipole, redox quinone) or relabel them.
- [ ] Draft `run_benchmark.py` skeleton (no scoring yet).
