---
trigger: model_decision
description: Mandatory transparency rules for reporting any quantum-chemistry calculation to the user. Follow these whenever a result from a chemkit skill/MCP tool (single-point, optimization, frequency, pKa, logP, redox, reaction profile, orbitals, electrostatics, etc.) is reported — every computed number must arrive with the full method provenance needed to reproduce and trust it.
---

# Calculation Reporting Standards

This document governs **how the results of a computation are communicated to the
user**. It applies the moment a chemkit skill or MCP tool returns a number and
that number is about to be put in front of a human: a total energy, a relaxed
geometry, a barrier height, a pKa, a logP, a dipole, an orbital energy, a redox
potential — anything.

It is the *output-facing* counterpart to the rest of the rules set:

- `skill-standards.md` — how to author one atomic skill.
- `research-standards.md` — how to find, verify, and cite literature/data.
- `workflow-standards.md` — how to compose skills into a vetted procedure.
- `calculation-reporting-standards.md` (this file) — how to report a computed
  result **transparently** so it is reproducible and not mistaken for something
  it is not.

> [!IMPORTANT]
> **The Prime Directive: a number without its method is noise.**
> "The pKa is 4.7" is not a result — it is a rumor. A computed value is only
> meaningful alongside the level of theory, geometry, solvent model, and
> software that produced it. Reporting the number without the method launders a
> model-dependent estimate into something that looks like a measured fact. Be
> **transparent, thorough, and explicit** about the entire chain. When in doubt,
> say *more* about how the number was made, not less.

---

## 0. The nine non-negotiables

1. **Never report a bare number.** Every reported quantity carries its level of
   theory, basis (or semi-empirical Hamiltonian), geometry source, and units.
2. **Never blur provenance.** A *computed* value is never called "experimental,"
   "measured," "accepted," or "literature." Computed is computed (see §6 and
   `research-standards.md`).
3. **Never hide the defaults.** If the engine silently chose a functional,
   basis, tier, solvent, charge, or multiplicity, surface that choice — a
   default is still a decision the user is entitled to see.
4. **Never compare energy zeros across methods.** xtb, PM7, HF, and DFT each
   define their own energy origin. Only *same-method, same-basis* energies are
   directly subtractable. State this whenever a difference is reported.
5. **Never imply more precision than the method supports.** Echoing 12 decimal
   places from the JSON does not make a semi-empirical heat of formation
   accurate to 12 places. Report sensible significant figures and name the
   method's regime.
6. **Never suppress a non-convergence or a warning.** If `converged: false`, if
   an SCF struggled, if MOPAC re-fit STOs, if a fallback fired — say so
   prominently, next to the number it affects.
7. **Always make it reproducible.** The exact command (or the JSON's
   `cli_invocation`) and the output file paths must be reportable on request,
   and the headline method line must be enough to re-run.
8. **Never generate a structure by hand — always use the relevant skill.**
   Whenever a molecular structure is created (an `.xyz` geometry, a structure
   built from a SMILES string or a name, a conformer, a scan frame, etc.),
   produce it by **activating the appropriate chemkit skill** (e.g.
   `build-from-smiles` for SMILES/name → `.xyz`, `conformer-search` for
   conformers, `geometry-optimize` for relaxed structures) — never by
   hand-writing coordinates or fabricating a geometry. This guarantees the
   structure's provenance (resolving database + citation, force-field/QM build
   method, exact command) is captured and reportable per §3.
9. **Always surface the `.out` log LIVE, while the calculation is running.**
   Every skill run streams a live `<subcommand>_<timestamp>.out` log to the
   caller's cwd (see §9). The `.out` path MUST be given to the user **as soon as
   the run is launched — while it is still running — not after it finishes.** The
   whole point is that the user can watch the calculation in real time, so the
   path is useless if it only arrives with the final result. The moment a
   calculation is started (especially a long one run in the background),
   immediately **give its full `.out` path, say it is being written live, and
   offer to open it / note it can be `tail -f`'d** — do not wait for the run to
   complete and do not wait to be asked. State it plainly, e.g. "Calculation
   started; it's logging live to `/abs/path/opt_20260613-101500.out` — want me to
   open it, or you can `tail -f` it now." Surface the path again with the final
   result, but the live, mid-run announcement is the requirement.

---

## 1. The Method Provenance Block (required for every result)

Every calculation report MUST open with — or clearly contain — a compact block
that answers *"what exactly was computed?"*. Think of it as the methods sentence
of a paper, inline. The required fields, in order:

1. **Property** — what was calculated (total energy, ΔG, barrier, pKa, dipole…).
2. **Level of theory** — see §2. The headline identifier, e.g.
   `ωB97X-V/def2-TZVP`, `HF/def2-TZVP`, `GFN2-xTB`, `PM7`.
3. **Geometry provenance** — see §3. Where the coordinates came from and whether
   they were relaxed at this level or carried over from another.
4. **Solvent / environment** — see §4. The implicit-solvation model and solvent,
   or **"gas phase"** explicitly. Never leave this unstated.
5. **Charge and multiplicity** — see §5. Always state both, even for the
   neutral closed-shell default (charge 0, multiplicity 1).
6. **Software + version** — the backend that produced it (PySCF, xtb, MOPAC,
   Open Babel) and version when available.
7. **Convergence + caveats** — converged? step count / gradient norm? any
   warnings or fallbacks (§7).

A minimal, honest example:

> **Acetone HOMO/LUMO energies** — DFT, **ωB97X-V/def2-TZVP** (PySCF), gas
> phase, charge 0, multiplicity 1. Geometry: built from PubChem SMILES,
> GFN2-xTB-relaxed (not re-optimized at DFT). SCF converged. HOMO = −9.67 eV,
> LUMO = +2.05 eV.

If a field genuinely does not apply (e.g. solvent for a deliberately gas-phase
run), state the value anyway ("gas phase") rather than omitting it. **Omission
reads as "I didn't think about it."**

---

## 2. Level of theory — be specific

The single most important line. Report the method at the granularity that lets a
reader reproduce it.

### 2.1 DFT (`--method dft`, PySCF RKS/UKS)
- Report **functional/basis** explicitly, e.g. `ωB97X-V/def2-TZVP`.
- If a **tier** shorthand was used, expand it — the tier name alone is *not*
  enough for an outside reader:
  - `fast` → **r²SCAN / def2-SVP**
  - `standard` → **ωB97X-V / def2-TZVP**
  - `accurate` → **ωB97M-V / def2-QZVPP**
  - Report it as "tier `standard` (ωB97X-V/def2-TZVP)" so both the chemkit knob
    and the underlying theory are visible.
- Note **density fitting (RI/RIJK)** if it was on (it is on by default in
  chemkit) — it is an approximation to the two-electron integrals and belongs in
  an honest methods line.
- For range-separated / VV10-containing functionals (ωB97X-V, ωB97M-V), the
  non-local correlation is part of the functional name — don't abbreviate it
  away.

### 2.2 Hartree–Fock (`--method hf`, PySCF RHF/UHF)
- Report **`HF/<basis>`**, e.g. `HF/def2-TZVP`. HF has no functional; do not
  invent one. Mention RHF vs UHF if the system is open-shell.

### 2.3 GFN2-xTB (`--method xtb`)
- Report as **GFN2-xTB** (a semi-empirical tight-binding method). There is **no
  basis set** in the Gaussian-basis sense and **no functional** — do not attach
  one. Its accuracy regime is geometries/conformers/relative trends, not
  chemical-accuracy absolute energies; say so when it matters.

### 2.4 PM7 (`--method mopac`, MOPAC)
- Report as **PM7** (semi-empirical, NDDO). Heats of formation come back as
  `final_heat_of_formation_kcal_mol`; report **that** quantity by its proper
  name (ΔH_f), not as a "total energy" comparable to ab initio energies.
- PM7 likewise has **no basis set**. For the *orbital-visualization* path only,
  chemkit **re-fits each PM7 STO as STO-3G Gaussians** to synthesize a molden —
  this is an approximation for plotting shapes; absolute amplitudes differ from a
  native PM7 plot. **Always surface this re-fit caveat** when reporting MOPAC
  orbitals.

### 2.5 Anything multireference / specialized
If a result came from outside the standard four backends (e.g. a CASSCF output
file), state the active space, reference, and program explicitly — never let a
specialized result inherit a generic "DFT" label.

---

## 3. Geometry provenance — coordinates are part of the answer

A property is computed *at a geometry*. Two numbers at the same level of theory
but different geometries are different results. Always state:

1. **Source of the input coordinates** — user-supplied `.xyz`, built from a
   SMILES/name (cite the resolving database per `research-standards.md`),
   a prior optimization, a conformer search, a scan frame, etc.
2. **Whether this calculation relaxed the geometry** or used it as-is:
   - A single-point reports an **energy at a fixed geometry** — name the geometry
     it sat on.
   - An optimization reports the **relaxed** structure — give the optimizer
     (ASE BFGS for xtb/dft/hf; MOPAC native EF for PM7), the **fmax / gradient
     criterion**, and the **step count** (or `mopac_status` +
     `mopac_gradient_norm_kcal_per_A`).
3. **Level mismatch between geometry and property** — the common and important
   case. "DFT single point **on an xtb-optimized geometry**" is a legitimate and
   cheap protocol, but it MUST be labeled as such (often written
   `DFT//GFN2-xTB`). Silently reporting a DFT energy on a non-DFT geometry as if
   it were a fully DFT result is a transparency failure.
4. **The optimized `.xyz` itself** is a deliverable — paste it (or give its path)
   so the geometry behind the number is inspectable.

---

## 4. Solvent / environment — never silent

Implicit solvation shifts energies, pKa, redox potentials, and dipoles
substantially. The environment is therefore **always** reported.

- If a solvent was used, name **both the model and the solvent**: chemkit uses
  **ddCOSMO** (domain-decomposition COSMO, a PCM-family continuum model) in
  PySCF for DFT/HF, and the backend-native implicit models for xtb (ALPB/GBSA)
  and MOPAC. Report e.g. "ddCOSMO implicit water (PCM-family continuum)".
- If **no** solvent was used, write **"gas phase"** — explicitly. Do not leave
  the field blank; blank is ambiguous between "gas phase" and "forgot to say."
- State that the treatment is **implicit/continuum only** — there are no explicit
  solvent molecules, no specific H-bonding, no first-shell structure. This
  matters for anything where specific solute–solvent interactions dominate (e.g.
  pKa of strong H-bonders); flag the limitation rather than overselling the
  number.
- For any **dielectric or cavity** assumptions the model carries, mention them if
  the user is comparing solvents or reporting a solvation free energy.

---

## 5. Charge, multiplicity, and open-shell handling

- **Always report charge and multiplicity**, including the defaults
  (charge 0, multiplicity 1). They are too consequential to leave implicit, and
  Open Babel does **not** infer charge — an ion only carried the right charge if
  it was passed explicitly. Make the value visible so an error is catchable.
- For **open-shell** systems, say **UHF/UKS** and report **alpha and beta**
  separately where the property is spin-resolved (e.g. SOMO/orbital energies).
- If charge/multiplicity were *assumed* rather than *specified by the user*, say
  "assumed neutral singlet" so the user can correct a wrong assumption before
  trusting downstream numbers.

---

## 6. Computed vs. reference — keep the wall up

This section is binding and overlaps `research-standards.md` (which wins on any
conflict).

- **Do not volunteer experimental/literature comparisons.** Report only what
  *this calculation produced*. Provide an accepted/measured value **only if the
  user explicitly asks** — and then it must pass the full Verification Mandate in
  `research-standards.md` (live link + metadata match + value confirmation).
- **Never editorialize about agreement.** Do not say a number "matches
  experiment," "looks right," or "is in good agreement" unless the user asked for
  a comparison and you have a *verified* reference in hand. An unprompted "this
  looks about right" fabricates a validation that did not happen.
- **Provenance labels are not comparisons.** Reporting that a geometry's SMILES
  came from PubChem (input provenance, always required) is different from
  reporting a measured value for comparison (output validation, only on
  request). Keep them distinct.

---

## 7. Convergence, warnings, and fallbacks — report them loudly

A clean-looking number can sit on top of a silent problem. Surface, next to the
affected value:

- **SCF / optimization convergence.** For dft/hf/xtb: converged? `n_steps`?
  For MOPAC: `mopac_status` and `mopac_gradient_norm_kcal_per_A` (no `n_steps`).
  If **not converged**, still deliver the last geometry/energy but flag
  `converged: false` **prominently** — do not bury it.
- **Engine warnings.** Echo every entry in the result JSON's `warnings` array
  **verbatim**. They are there for a reason.
- **Fallbacks that fired.** e.g. PySCF DM warm-start falling back to a cold SCF
  on shape mismatch or non-convergence; a tier downgrade; an xtb-then-DFT
  pre-optimization. The protocol that actually ran is the one to report — not the
  one originally requested.
- **Known approximations of the chosen path.** density fitting (§2.1), the MOPAC
  STO-3G molden re-fit (§2.4), implicit-only solvation (§4), single-conformer
  geometries (no Boltzmann averaging unless a conformer search was run).

---

## 8. Units and significant figures

- **State units on every quantity.** Energies: be explicit about Hartree vs eV
  vs kcal/mol (the engine often returns all three — pick the natural one for the
  context and label it). Distances Å, angles degrees, dipoles Debye, charges in
  e, frequencies cm⁻¹, pKa/logP dimensionless.
- **Round honestly.** Do not paste full machine precision as if it were
  meaningful. A GFN2-xTB or PM7 energy is not accurate to µHartree; a DFT total
  energy's last digits are basis- and grid-dependent. Relative quantities
  (barriers, reaction energies) are more transferable than absolutes — report
  them to the precision the *method* supports, not the precision the *float*
  shows.
- When a **difference** is the deliverable (ΔE, ΔG‡, ΔΔG), compute it from
  same-method values and show the inputs so the subtraction is checkable.

---

## 9. Reproducibility artifacts

Every report must be able to point at:

- the **exact command** that ran (the JSON's `cli_invocation`, or the
  `python skills/.../<skill>.py ...` line);
- the **output file paths** (optimized `.xyz`, `.molden`/`.cube`, result
  `.json`) — and note **where they were written** (the engine writes relative to
  its run directory, which may not be the user's cwd);
- the **live `.out` log** — every skill run streams the engine's stdout/stderr
  (PySCF at `--verbose 4` by default) to a `<subcommand>_<timestamp>.out` file in
  the caller's cwd, written line-by-line so the user can `tail -f` it *while the
  calculation runs*. It carries a header block (subcommand, args, exact command,
  cwd) and ends with the result JSON under a banner, so it is self-contained.
  Surface its path when reporting a result, and treat it as the primary live
  progress and post-mortem artifact (SCF cycles, optimizer steps, warnings,
  backend banners);
- enough of the **method block** (§1) to re-run without reading the transcript.

Offer to move or clean up generated artifacts rather than leaving them
scattered, and never claim a file exists without it actually being on disk.

---

## 10. The reporting checklist

Before a computed result goes to the user, confirm:

- [ ] **Property** named, with **units**.
- [ ] **Level of theory** explicit (functional/basis, or Hamiltonian); tier
      expanded; density fitting noted if on.
- [ ] **Geometry provenance** stated, including any level mismatch
      (`property//geometry`) and the optimizer/criterion if relaxed.
- [ ] **Solvent model + solvent**, or explicit **"gas phase."**
- [ ] **Charge and multiplicity** reported (even the defaults); open-shell
      treatment named.
- [ ] **Software/backend** named (version if available).
- [ ] **Convergence** state + step count / gradient; **warnings echoed
      verbatim**; **fallbacks** surfaced.
- [ ] **Significant figures** honest; **no fabricated precision**.
- [ ] **No unsolicited** experimental/literature comparison; provenance ≠
      validation.
- [ ] **Reproducibility**: command + output paths available; method block
      sufficient to re-run.

> [!IMPORTANT]
> If you cannot fill in the method block, you do not yet understand the result
> well enough to report it. Go read the result JSON (`cli_invocation`, `method`,
> `solvent`, `charge`, `multiplicity`, `converged`, `warnings`) and the skill's
> `SKILL.md` before putting a number in front of the user.

---

## 11. Worked example (good vs. bad)

**Bad** (opaque, non-reproducible, blurs provenance):

> The HOMO–LUMO gap of acetone is 11.72 eV, which agrees well with experiment.

What's wrong: no level of theory, no geometry, no solvent, no charge/mult, no
software, an *unsolicited and unverified* experimental comparison, and false
precision implying a validated result.

**Good** (transparent, reproducible, honest about scope):

> **Acetone frontier-orbital gap** — DFT, tier `standard`
> (**ωB97X-V/def2-TZVP**, density-fit) in PySCF, **gas phase**, charge 0,
> multiplicity 1. Geometry: built from name → PubChem (SMILES `CC(=O)C`),
> GFN2-xTB-relaxed; the DFT was a single point on that xtb geometry
> (`DFT//GFN2-xTB`), not re-optimized at DFT. SCF converged.
> HOMO = −9.67 eV, LUMO = +2.05 eV → **gap ≈ 11.7 eV** (Koopmans-style orbital
> gap, not an excitation energy). Files: `…_orbitals_dft.molden`,
> `…_homo.cube`, `…_lumo.cube`. Command: `cli … orbitals … --method dft --tier
> standard`. No experimental comparison is offered — ask if you want one and I'll
> verify a source first.

---

## References

The method-provenance fields this document requires are grounded in the
backends chemkit reports on. Cite the relevant one(s) in any methods line that
leans on them.

- Bannwarth, C.; Ehlert, S.; Grimme, S. "GFN2-xTB — An Accurate and Broadly Parametrized Self-Consistent Tight-Binding Quantum Chemical Method", *J. Chem. Theory Comput.* **2019**, *15* (3), 1652–1671. [DOI](https://doi.org/10.1021/acs.jctc.8b01176)
- Stewart, J. J. P. "Optimization of Parameters for Semiempirical Methods VI: PM7", *J. Mol. Model.* **2013**, *19* (1), 1–32. [DOI](https://doi.org/10.1007/s00894-012-1667-x)
- Sun, Q.; et al. "Recent Developments in the PySCF Program Package", *J. Chem. Phys.* **2020**, *153*, 024109. [DOI](https://doi.org/10.1063/5.0006074)
- Larsen, A. H.; et al. "The Atomic Simulation Environment (ASE)", *J. Phys.: Condens. Matter* **2017**, *29*, 273002. [DOI](https://doi.org/10.1088/1361-648X/aa680e)
- Lipparini, F.; Stamm, B.; Cancès, E.; Maday, Y.; Mennucci, B. "Fast Domain Decomposition Algorithm for Continuum Solvation Models: Energy and First Derivatives", *J. Chem. Theory Comput.* **2013**, *9* (8), 3637–3648. [DOI](https://doi.org/10.1021/ct400280b)

---

*This file is part of the chemkit rules set. On any conflict touching
literature-sourced values or citations, `research-standards.md` governs.*

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
