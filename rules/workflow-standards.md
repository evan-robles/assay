---
trigger: model_decision
description: Rules to author and run a multi-step research workflow in chemkit. Follow these whenever a user asks for a high-level objective that chains several skills/MCP tools end-to-end (e.g. "find the most stable conformer and its pKa", "screen these molecules for redox potential", "reproduce the reaction profile from this paper").
---

# Workflow Standards

A **workflow** is a high-level research objective that chains multiple chemkit
skills and MCP-server tools into an end-to-end procedure. Workflows live as
single Markdown files in `chem-skills/workflows/` and exist to make a complex,
multi-step scientific goal **reproducible, honest, and ready for an agent to
execute**.

This is the third member of the chemkit rules set:

- `skill-standards.md` — how to author one atomic skill.
- `research-standards.md` — how to find, verify, and cite literature/data
  (**binding for every literature step in a workflow**).
- `workflow-standards.md` (this file) — how to compose skills into a vetted,
  reproducible research procedure.

---

## What is a workflow?

Workflows are distinct from individual skills or MCP tools:

1. **High-level objective.** A workflow answers a scientific question
   ("Is this molecule a viable one-electron reductant in water?"), not a single
   calculation.
2. **Journal-article scope.** It states the scientific problem, the method, the
   acceptance criteria, and the expected result — the way a methods section
   does.
3. **Flexibility with rigor.** A workflow may be a methodology distilled from a
   paper, or a curated sequence of skills/MCP calls. Either way it must be
   *deterministic enough to re-run* and *honest enough to trust*.

> [!IMPORTANT]
> **A workflow is a scientific claim generator.** Every value it ultimately
> reports — computed or literature-sourced — must be reproducible and correctly
> attributed. The integrity rules below are not optional polish; they are what
> separates a workflow from a plausible-looking guess.

---

## Directory structure

Workflows are single Markdown files:

```
chem-skills/workflows/
├── conformer-then-pka.md
├── redox-screen.md
└── reproduce-reaction-profile.md
```

Use **kebab-case**, **purpose-over-method** names (`redox-screen`, not
`xtb-batch-run`) — the same naming philosophy as `skill-standards.md`.

---

## Workflow file format

Markdown with YAML frontmatter. Required sections in order: **frontmatter →
Problem → Prerequisites → Methodology → Decision logic → Acceptance criteria →
Reproducibility → Limitations → References**.

### 1. YAML frontmatter
```yaml
---
description: Concise one-sentence summary of the workflow's research objective.
---
```
- `description`: clear enough that the agent matches it to a user request and a
  reader understands the goal. State what it produces, not how. Avoid mid-line
  colons in unquoted YAML.

### 2. Title and problem definition
```markdown
# <Workflow Name>

This workflow guides you through <high-level objective>.

**Scientific problem:** <Abstract-style context — why this workflow exists, what
question it answers, and what a correct result looks like.>

**Inputs:** <structures/SMILES/parameters the user must supply.>
**Outputs:** <the JSON files, structures, plots, and the final reported quantity,
with units.>
```

### 3. Prerequisites
List exactly what must be in place before running, so failures surface early:
- Required external binaries / backends (`xtb`, `mopac`, `openbabel`,
  optionally `pyscf`, `sella`) — mirror the skill's own requirements.
- Required skills/MCP tools (by exact name).
- Required inputs and their assumed state (e.g. "geometry must already be
  optimized at the same method", "charge/multiplicity known").

### 4. Step-by-step methodology
A numbered sequence; each step is a conceptual phase that maps to a concrete
action.

- **Reference skills by their exact kebab-case names** (`geometry-optimize`,
  `conformer-search`, `vibrational-analysis`, `redox-potential`, `pka-acidity`,
  …) and link them: `[geometry-optimize](../skills/geometry-optimize/SKILL.md)`.
- **Give the literal invocation** where helpful, matching the skill's own CLI:
  ````markdown
  ```bash
  python chem-skills/skills/conformer-search/scripts/conformer-search.py \
      --method xtb --postopt mol.xyz
  ```
  ````
- **Keep method/solvent/charge consistent across steps.** State explicitly when
  a downstream step *requires* the same level of theory as an upstream one
  (e.g. a frequency job must use the geometry-optimization method). Flag any
  point where energies from different backends (xtb vs. MOPAC) are **not**
  comparable.
- **Persist artifacts between steps.** Each step says which JSON/`.xyz`/`.png`
  it writes and which file the next step consumes — never assume a tmp file
  survives.

### 5. Decision logic
Make branching explicit so the agent does not improvise:
- Convergence/quality gates ("if no imaginary frequencies, proceed; if one
  imaginary mode, re-optimize the TS").
- Fallbacks ("if PM7 lacks parameters for this transition metal — see the
  schema `warnings` — switch to xtb and note it").
- Loops with a stopping rule ("repeat conformer post-opt until the lowest-energy
  set is stable; a single surviving conformer is the converged answer, not a
  bug").
- When to **stop and ask the user** (use AskUserQuestion) — e.g. ambiguous
  protonation state, unknown charge, or a missing required input.

### 6. Acceptance criteria
State what makes the result trustworthy *before* reporting it:
- Numerical convergence thresholds actually checked.
- Physical sanity checks (e.g. a TS has exactly one imaginary frequency;
  ΔG signs make chemical sense).
- For any **literature comparison**, the validation must follow
  `research-standards.md` in full (see §Literature).

### 7. Reproducibility
- Record every parameter (method, solvent model, charge, multiplicity,
  symmetry, thresholds) — consistent with the skill standard's
  `input_configs.yaml` persistence rule.
- The final report lists the exact `cli_invocation`s (the skills already emit
  these in their JSON header) so the run can be repeated verbatim.
- Do **not** bury configs inside result JSON; keep them separate and complete
  (defaults included).

### 8. Limitations / honesty
State the workflow's screening-vs-publication grade plainly. Carry forward the
repo's known caveats where relevant (PM7 transition-metal parameters are spotty;
redox and conformer search are screening-grade). **Never present a
screening-grade number as definitive.**

### 9. References
- For methods/software the workflow relies on, cite per the skill standard.
- **Any value or claim drawn from the literature MUST be produced under
  `research-standards.md`** — verified via a live link check (curl/Crossref/
  DOI resolve + metadata match), correctly attributed (experimental vs.
  computational), and formatted in **ACS style**. Citations that fail that gate
  are not included; report the gap instead.

```markdown
## References
- Author, A. A.; Author, B. B. Title. *Journal Abbrev.* **Year**, *Vol.*, Pages. https://doi.org/10.xxxx/xxxxx.
```

---

## Integrity rules (binding)

> [!IMPORTANT]
> 1. **No fabricated results.** Never report a computed number that wasn't
>    actually produced by a run, and never a literature value that wasn't
>    verified per `research-standards.md`.
> 2. **No silent method-mixing.** If a reported quantity combines results from
>    different backends or levels of theory, say so and justify it; flag
>    non-comparable energy zeros (xtb vs. MOPAC).
> 3. **No skipped acceptance checks.** If a convergence or sanity gate fails,
>    report the failure — do not present the unconverged number as the answer.
> 4. **No undisclosed truncation.** If the workflow caps cost (sampled a subset,
>    limited conformers, skipped a step), state exactly what was dropped.
> 5. **Literature defers to `research-standards.md`** — always, in full.
> 6. **Least skills to complete the task (no gratuitous runs).** Run the MINIMUM
>    set of skills that actually answers the question, and never invoke a skill
>    whose output you will not use. Stop as soon as the answer is in hand.
>    Concretely: a pure *identity / lookup* question — molecular formula, atom
>    count, canonical SMILES, a name→SMILES resolution — is answered by
>    `name-to-smiles` (or reading the SMILES) and needs **no** 3D structure, so do
>    NOT call `build-from-smiles`. Only build a 3D geometry when a **downstream
>    skill requires one** (single-point, geometry-optimize, vibrational-analysis,
>    fukui, electrostatics, …). Likewise, do not run a calculation for a question
>    that is purely about identity or provenance. Each extra run costs compute and
>    manufactures a step the task did not require — which is its own small
>    dishonesty about what was necessary.

---

## Best practices

- **One objective per workflow.** Compose skills; don't reinvent them. Heavy
  logic belongs in the skills/MCP engine, not the workflow prose.
- **Deterministic phrasing.** Prefer explicit thresholds and invocations over
  vague guidance, so two runs of the same workflow agree.
- **Surface caveats early** (Prerequisites/Limitations) rather than after a
  failed run.
- **Cross-link** related skills and workflows with relative paths.
- **Validate against literature when possible**, and only via the verified,
  ACS-formatted citation procedure in `research-standards.md`.

---

## Relationship to other rules

- Authoring the individual steps → follow `skill-standards.md`.
- Any literature search, data fetch, or cited value → follow
  `research-standards.md` (hard gate: live link check + metadata match + honest
  provenance + ACS format).
- Composing the vetted, reproducible end-to-end procedure → this file.

---

**A workflow is only as trustworthy as its least-verified step. Reproducible
runs, honest provenance, and verified citations are required — not aspirational.**
