---
trigger: model_decision
description: Mandatory honesty and verification rules for any literature search, citation, or fetching of data/values from the published literature. Follow these whenever a user asks to find references, cite a paper, look up a measured/published value, or validate results against the literature.
---

# Research & Citation Standards

This document governs **how references and literature-sourced data are produced**
in chemkit. It applies the moment a task involves *searching the literature*,
*citing a source*, *looking up a published value* (bond length, frequency,
dipole, ΔG, ΔH, redox potential, pKa, logP, rate constant, crystal structure,
spectrum, etc.), or *validating a calculation against published numbers*.

It is the literature-facing counterpart to `skill-standards.md`. Where
`skill-standards.md` already requires that example validations cite a *genuine
primary source*, **this document is the full, binding procedure** for doing so
honestly.

> [!IMPORTANT]
> **The Prime Directive: honesty outranks helpfulness.**
> A reference you cannot verify is **not** a reference. It is better to tell the
> user "I could not find a verifiable source for this" than to provide one that
> is invented, guessed, or misattributed. Fabricating a citation, a DOI, a
> value, or an attribution is the single most damaging thing this toolkit can
> do — it launders a guess into something that *looks* authoritative. There is
> **zero tolerance** for it.

---

## 0. The five non-negotiables

1. **Never invent.** No fabricated DOIs, authors, titles, journals, years,
   volumes, page numbers, or values. Not even as a "placeholder."
2. **Never cite from memory as if confirmed.** A citation may only be presented
   to the user after its link has been **resolved and metadata-matched by a live
   check in this session** (see §2). Model recall is a *lead*, not a *source*.
3. **Never misattribute provenance.** A computed number is never labeled
   "experimental." A value found in a compilation is never cited as if you read
   the primary paper (see §4–§5).
4. **Never pad.** Do not lengthen a reference list to look thorough. Every entry
   must directly support a specific claim or value actually used.
5. **Always signal uncertainty.** If something is unverified, approximate, or
   provenance-ambiguous, say so in plain language (see §8). Silence reads as
   confidence you have not earned.

---

## 1. What counts as a citation

A citation is only complete when it carries enough information to be
**independently located and checked**. The minimum fields:

- **Authors** (or institutional author / database name)
- **Title** of the work
- **Venue** — journal (CASSI-abbreviated), book, database, or repository
- **Year**
- **Volume / pages** (for journal articles) or identifier (for datasets)
- **A resolvable locator** — DOI (preferred), or a stable URL with an access date

A bare `"Smith et al."`, a lone DOI with no metadata, or `"a 2019 JACS paper"`
is **not** a citation. It is a lead to be resolved into a full record.

---

## 2. The Verification Mandate (hard gate)

**No citation or literature-sourced value is shown to the user until its link has
been resolved AND its metadata matched by a live check in the current session.**
This is a hard gate, not a guideline.

### 2.1 Link liveness check (required, automated)

Every citation's resolvable locator (DOI or URL) MUST be **hit this session** and
return a success status. Use `curl` (preferred — explicit, scriptable, logged) or
an equivalent fetch (`WebFetch`, the project's HTTP tooling) when `curl` is
unavailable. **Assuming a link works is not allowed — hit it.**

**A) DOI must resolve to a live page (follow redirects; want a final 2xx):**
```bash
curl -sIL -o /dev/null -w "%{http_code} %{url_effective}\n" "https://doi.org/<DOI>"
# PASS: final code is 2xx (often after a 30x redirect to the publisher).
# FAIL: 404 / 410 / 000 / a redirect that dead-ends. -> drop it (see §11).
```

**B) Crossref must return the record and the metadata must MATCH (§2.2):**
```bash
curl -s "https://api.crossref.org/works/<DOI>" \
  | python -c "import sys,json; m=json.load(sys.stdin)['message']; \
print('TITLE:', (m.get('title') or ['<none>'])[0]); \
print('YEAR :', m.get('issued',{}).get('date-parts',[['?']])[0][0]); \
print('JRNL :', (m.get('container-title') or ['<none>'])[0])"
# Compare the printed TITLE / YEAR / JRNL against the citation you intend to emit.
```

**C) Any non-DOI cited URL** (NIST WebBook, PubChem, CCDC, COD, RCSB PDB, a
publisher page) MUST also return a live 2xx, and you must read the page to
confirm it contains the value/record you cite:
```bash
curl -sIL -o /dev/null -w "%{http_code}\n" "<URL>"      # want 2xx
```

**D) PubMed / Europe PMC** for biomedical/chemical work — confirm the
PMID/PMCID resolves and the title/year match (via E-utilities or `WebFetch` of
the record page).

### 2.2 Metadata match (required, in addition to a live status)

A live 2xx status is **necessary but not sufficient**. The resolved record's
**title, year, and (where checkable) authors/journal must match** the citation
you are about to emit. A 200 that returns a *different* paper, a journal landing
page, or a paywall stub with no matching metadata **fails the gate**. Mismatched
metadata = drop it and follow §11.

### 2.3 Value confirmation

For a **measured/reported value**, opening the source (WebFetch/curl the page or
PDF) and confirming the **number, units, and conditions are actually present** is
required — a search-result snippet you never opened does not satisfy this.

### 2.4 What does NOT satisfy the gate

- "I'm fairly sure this is the right DOI." → resolve it with curl (§2.1A).
- "This value is well known." → still hit a source and read the number.
- A 2xx with a non-matching title (§2.2).
- A search snippet naming a paper you never fetched, used to back a specific
  value.

### 2.5 Record the result

Each citation carries a provenance tag noting the HTTP result and the match (see
§9), e.g. `[verified: DOI 200 via curl + Crossref title/year match, 2026-06-12]`.
If any check fails, the citation **fails the gate** — drop it and follow §11.

---

## 3. Forbidden practices (the fraud catalog)

All of the following are prohibited:

- **Fabricating a DOI** or constructing one that "looks plausible"
  (`10.1021/...` guesses are still fabrication).
- **Guessing** authors, year, volume, or page numbers to "complete" a record.
- **Citing a link without hitting it** this session (§2.1).
- **Citing a paper you did not open** for a specific value inside it.
- **Reusing a real DOI** to back a claim that source does not actually make.
- **Accepting a 2xx without a metadata match** (§2.2).
- **Misattributing experiment vs. computation** (see §5).
- **Citing a compilation/database as the primary source** without tracing or
  honestly labeling it (see §4).
- **Padding** the reference list with tangential or unread works.
- **Hallucination-laundering** — taking a remembered citation, running one
  unrelated search, and presenting the remembered version as "verified."
- **Silent unit conversion or rounding** of a quoted value (see §7).
- **Presenting a preprint, blog, vendor page, encyclopedia, or forum answer as a
  peer-reviewed primary source** (see §6).

If you catch yourself about to do any of these, **stop** and switch to the
not-found protocol (§11).

---

## 4. Primary, secondary, tertiary — and tracing

- **Primary source:** the work that first *reports* the measurement, structure,
  or result (the paper that did the experiment or the calculation).
- **Secondary source:** review, meta-analysis, or computational paper that
  *discusses or tabulates* others' results.
- **Tertiary source:** database, handbook, or compilation (NIST WebBook, CRC,
  PubChem, CCDC, COD, RCSB PDB).

**Rule:** When citing a *specific measured value*, prefer the **primary**
source. If you obtained the value from a tertiary/secondary source:

- **(a) Trace it** to the primary reference the compilation cites, verify that
  primary reference (§2), and cite it; **or**
- **(b) If you cannot trace it, label it honestly** for what it is — e.g.
  *"value as compiled in the NIST Chemistry WebBook"* — and cite the database as
  the source. Do **not** dress a database value as if you read the original
  paper.

Either path is acceptable. Inventing a primary reference is not.

---

## 5. Experimental vs. computational provenance

(*This hardens the experimental-source integrity rule in `skill-standards.md`.*)

Every value carries a **provenance label**: **experimental**, **computational**
(with method/level if known), or **reference/benchmark**.

- A value described as **experimental** MUST cite the paper that **measured**
  it. Never cite a modeling paper, a method-development paper, or a theory review
  as if it were the experiment — even if that paper tabulates the experimental
  number.
- A **computed** value is labeled with its origin (e.g. *"CCSD(T)/CBS reference
  value"*, *"DFT (ωB97X-D) value from ref X"*). Never call it "experiment."
- If you cannot determine whether a tabulated number is measured or modeled,
  **say so** and label it ambiguous rather than guessing.

> Presenting computational chemists' modeling output as an experimental
> measurement is fraud. So is the reverse.

---

## 6. Chemistry data-source hierarchy

Preferred sources and how to treat each:

**Tier 1 — Peer-reviewed primary literature (most authoritative for claims)**
- Journal articles, verified via DOI/Crossref/PubMed link check (§2). Cite in
  ACS format (§9).

**Tier 2 — Curated databases / standard references (authoritative for data,
cite as the database)**
- **NIST Chemistry WebBook** — thermochemistry, spectra, ion energetics.
- **PubChem** — compound identity, computed/curated properties (note which).
- **CCDC / Cambridge Structural Database** and **COD (Crystallography Open
  Database)** — crystal structures (cite the deposition/CCDC number or COD ID,
  and trace to the structure paper where possible).
- **RCSB PDB** — macromolecular structures (cite the PDB ID *and* the primary
  citation listed on the entry).
- **CRC Handbook**, **IUPAC** recommendations — physical constants, nomenclature.

**Tier 3 — Discovery / fallback only (never a final primary citation for a
measured value)**
- General **web search** (`WebSearch`/`WebFetch`) to *locate* candidate sources,
  which must then be resolved up to Tier 1 or Tier 2 and link-checked before
  citing.
- **Explicitly distrust as primary sources:** preprints (label as preprint and
  not peer-reviewed), vendor/SDS pages, Wikipedia and other encyclopedias,
  blogs, forums, and AI-generated summaries. These may *point* you to a real
  source but are never the citation themselves.

---

## 7. Numerical fidelity

When you report a value pulled from the literature:

- **Quote it in the source's original units and precision.** If you convert,
  show both and name the conversion (e.g. *"4.18 kcal/mol (= 17.5 kJ/mol)"*).
- **Carry the uncertainty** if the source gives one (`± value`).
- **Carry the conditions** that define the measurement: temperature, pressure,
  phase, solvent, ionic strength, reference electrode (for redox), reference
  state (for pKa), basis/level (for computed values). A pKa or redox potential
  without its conditions is not a usable number.
- **Never round silently** in a way that changes the meaning, and never invent
  digits the source did not provide.

---

## 8. Confidence & uncertainty signaling

Use plain, unambiguous language. Approved tags:

- **Verified** — link resolved (2xx) and metadata matched this session (state
  how).
- **Unverified — could not confirm** — found a lead but failed the gate (dead
  link or metadata mismatch); not shown as a citation, reported as a gap (§11).
- **Approximate / order-of-magnitude** — value is rough; say why.
- **Provenance ambiguous** — could not determine experimental vs. computational,
  or could not trace to primary.

Never upgrade a tag to make an answer look stronger. When in doubt, under-claim.

---

## 9. Citation output format — **ACS style**

References are emitted in **ACS format**, under a `## References` heading
(consistent with the `References` section in skill `SKILL.md`/`README.md`
files). Journals use **CASSI abbreviations**. Style markers: **bold year**,
*italic volume*, *italic journal abbreviation*, en-dash (–) page ranges,
semicolon-separated authors with initials.

**Journal article**
```
Author, A. A.; Author, B. B.; Author, C. C. Title of the Article. Journal Abbrev. Year, Volume, FirstPage–LastPage. https://doi.org/10.xxxx/xxxxx.
```
Rendered (italic journal/volume, bold year):
> Klamt, A.; Schüürmann, G. COSMO: A New Approach to Dielectric Screening in Solvents. *J. Chem. Soc., Perkin Trans. 2* **1993**, *5*, 799–805. https://doi.org/10.1039/P29930000799.

**Book / chapter**
```
Author, A. A. Chapter Title. In Book Title, Edition; Editor, E. E., Ed.; Publisher: City, Year; Vol. X, pp FirstPage–LastPage.
```

**Database / dataset / online (with access date)**
```
Author or Organization. Title of Entry; Database Name; Identifier. URL (accessed YYYY-MM-DD).
```
Examples:
> National Institute of Standards and Technology. Water; NIST Chemistry WebBook, NIST Standard Reference Database Number 69. https://webbook.nist.gov/cgi/cbook.cgi?ID=C7732185 (accessed 2026-06-12).

> Cambridge Crystallographic Data Centre. Deposition CCDC 1234567. https://www.ccdc.cam.ac.uk/structures/ (accessed 2026-06-12).

**Inline citation:** ACS default is **numbered superscript** keyed to the
`## References` list (e.g. "...matches the reported value.¹"). Numbered brackets
`[1]` are acceptable where superscripts are impractical (plain-text logs). Be
consistent within a document.

**Provenance tag (chemkit-specific, required during a session):** append a short
note recording the link-check result so the user sees *how* the citation cleared
the gate:
```
[verified: DOI 200 via curl + Crossref title/year match, 2026-06-12]
[verified: URL 200 via curl, value read from NIST WebBook entry, 2026-06-12]
```
This tag is for transparency in chat/output; it may be omitted from a final
polished reference list once the user has accepted the sources.

---

## 10. Pre-submission checklist (hard gate)

Before showing **any** reference or literature value to the user, confirm **every**
box. If any fails, do not present it — go to §11.

- [ ] The DOI/URL was **hit with curl (or equivalent) this session** and returned
      a final **2xx** status (§2.1).
- [ ] The resolved record's **title, year, and journal/authors MATCH** the
      citation I'm emitting (§2.2).
- [ ] The **value** I quote is actually in that source (I read it, not a snippet) (§2.3).
- [ ] **Units, uncertainty, and conditions** are carried over correctly (§7).
- [ ] **Provenance** (experimental / computational / database) is labeled honestly (§4–§5).
- [ ] If from a compilation, I **traced to primary** or **labeled the database** (§4).
- [ ] The citation is in **ACS format** with correct CASSI abbreviation (§9).
- [ ] Nothing is **fabricated, guessed, padded, or misattributed** (§3).
- [ ] Confidence is **signaled** where less than fully verified (§8).

---

## 11. The "I couldn't find it" protocol

When the link check or metadata match fails, the honest answer is a gap report —
never a manufactured citation. Say, in substance:

> I could not verify a source for **[claim/value]**. I searched
> **[Crossref / PubMed / NIST WebBook / web]** and the candidate
> **[dead-linked (HTTP 404) / returned a non-matching record / had no readable
> value]**, so it fails the verification gate. I will not provide an unverified
> citation. Options: (a) I can report the value from **[database]** labeled as a
> database value rather than a primary source; (b) I can broaden the search with
> different terms; or (c) you may have a source in mind I can verify.

For a calculation validation where no literature value can be confirmed: report
the computed result **without** a comparison, and state explicitly that no
verified reference value was found — rather than inventing one to "validate"
against.

---

## 12. Worked examples

**✅ Good — verified primary citation with link-check provenance**
> The experimental gas-phase H–O–H bond angle of water is 104.5°.¹
> `[verified: DOI 200 via curl + Crossref title/year match, 2026-06-12]`
>
> **References**
> 1. Benedict, W. S.; Gailar, N.; Plyler, E. K. Rotation–Vibration Spectra of Deuterated Water Vapor. *J. Chem. Phys.* **1956**, *24*, 1139–1165. https://doi.org/10.1063/1.1742731.

**❌ Bad — fabricated / dead DOI**
> ~~Smith, J. Water Geometry. *J. Chem. Phys.* **2001**, *115*, 4400–4410. https://doi.org/10.1063/1.9999999.~~
> `curl -sIL https://doi.org/10.1063/1.9999999` returns 404 → fails §2.1 →
> fabrication. Prohibited.

**❌ Bad — 2xx but metadata mismatch**
> DOI resolves to HTTP 200, but Crossref returns a *different* title/year than
> the citation claims → fails §2.2. Drop it; do not emit.

**❌ Bad — misattributed experiment**
> ~~The experimental pKa of acetic acid is 4.76 [cited to a DFT benchmarking paper].~~
> A computational paper that *tabulates* 4.76 is not the source that *measured*
> it. Either trace to the measuring paper, or label it: *"4.76, as compiled in
> [database]"*. Calling it "experimental" while citing a modeling paper is
> prohibited (§5).

**✅ Good — honest database citation when primary can't be traced**
> The standard reduction potential is reported as −0.76 V vs. SHE (aqueous,
> 25 °C), as compiled in the NIST Chemistry WebBook.¹
> `[verified: URL 200 via curl, value read from NIST WebBook entry, 2026-06-12]`

**✅ Good — honest not-found**
> I could not confirm a peer-reviewed primary source for this rate constant.
> The candidate DOI dead-linked (404) and Crossref returned no matching record,
> so it fails the gate. I will not supply an unverified citation. I can instead
> report it as a database value, or broaden the search.

---

## 13. Relationship to other rules

- `skill-standards.md` §"Examples Section" requires literature-validated
  examples with a genuine primary source. **This document is the procedure** that
  requirement points to — follow it whenever building or validating an example.
- When a skill's output reports a literature value to the user, that report is
  subject to these standards in full.

---

**These standards exist so that every number and every citation chemkit
produces can be trusted and independently checked. A fabricated or dead-linked
reference is never acceptable, under any time pressure, for any reason.**
