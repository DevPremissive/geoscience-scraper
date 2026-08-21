# Next steps, derived from the plan documents — 2026-08-20

Phase 1 is complete. This works out what comes next **from the source documents
and the dependency graph**, not from preference. Every claim below cites the plan
it comes from or the check that established it.

## What the plans say Phase 2 is

`MASTER_PLAN.md` §7, verbatim:

> **Phase 2 — Economics + on-demand text (≈3 weeks).** C6.1–C6.5 built properly;
> C3.4 on-demand report fetch for ON AFRI + BC ARIS; C5.1 due-diligence RAG;
> C5.2 barren confirmation (Tier-2 upgrades); C4.3 screening DSL v1.
> *Gate G2:* one Phase-1 dossier re-issued with **report-backed history, Tier-2
> drill labels, comps-anchored valuation, and buyer capacity** — i.e., a dossier
> you would actually send.

Gate G2 names four requirements. Tracing each to its component and its blocker
is the whole decision:

| G2 requirement | Component | Depends on | Status |
|---|---|---|---|
| report-backed history | C5.1 | **C3.4** | **unblocked** |
| Tier-2 drill labels | C5.2 | **C3.4** | **unblocked** |
| comps-anchored valuation | C6.1 → C6.4 | SEDAR+ filing PDFs | blocked, other project |
| buyer capacity | C6.2's null fields | SEDAR+ financial statements | blocked, same corpus |

**Two of the four run through C3.4, and both are unblocked. The other two run
through a PDF campaign in `mining-scraper` that this repo does not control**
(audit L1: the captured SEDAR+ index gives document type and date only, never
content, which is why `treasury_estimate`, `burn_estimate` and
`drill_program_status` are emitted null with `missing_because`).

That makes C3.4 the answer. It is not a judgement call; it is what the graph says.

## C3.4 is much cheaper than its estimate

PLAN_C3 3.4 budgets "~3–4 days for ON+BC". The Ontario half is far smaller than
that now, and three checks today establish why:

1. **The resolver input is already on disk.** PLAN_C3 3.4 step 1 says to resolve
   report IDs through "LIO ArcGIS layer 50, `OMEIS Technical File Area` — 62,436
   records with spatial footprints" (audit C1 corrected this route away from the
   OAFD Elasticsearch path, withdrawing the API-key dependency).
   `ON_OMEIS_TECHFILE` is **harvested, SPATIAL, 62,436 features in `geo.gpkg`**,
   carrying `TECH_ID`, `PROPERTY`, `YEAR_FROM`, `WORK_TYPE`, `COMMODITIES`.
   Step 1 is done; it just needs querying.

2. **The PDFs actually download.** Two real `TECH_ID`s pulled from that layer,
   fetched from the Azure blob pattern in `connectors/scrape.py`:

   ```
   31C13SE0051   http=200  568,336 bytes  application/pdf   (PDF 1.4, 5 pages)
   41O04SW0026   http=200  2,242,675 bytes application/pdf  (PDF 1.4, 5 pages)
   ```

   No key, no anti-bot, no Camoufox. `SCRAPERS_BLOCKED.md`'s per-system
   difficulty does not apply to Ontario.

3. **The scraper is not a stub.** `scrape.SCRAPERS["ON_AFRI_PDF"].ready()` is
   `True` — both `enumerate_ids` and `pdf_url` are implemented. `harvest_pdfs.py`
   already has the download, sidecar and manifest plumbing.

So C3.4-for-Ontario is: write `fetch_reports(geometry, juris, max_reports)` to
the contract in PLAN_C3 3.4, back it with a spatial query against
`geo_ON__ON_OMEIS_TECHFILE`, and route the fetch through the existing PDF
plumbing with the format sniff `harvest.py` already applies. **Closer to a day
than to four.** BC ARIS is the other half and is not needed for G2 — Phase 1 and
Phase 2's gate are both Ontario.

## Then C5.1, which is what actually fills the gap

PLAN_C5 5.1 (~4 days) is the consumer, and its output is dossier **section 7**,
one of the three sections that currently render `NOT AVAILABLE`. Its inputs are
all present:

- `pdf_extract.py` exists — PLAN_C5 describes it as "95 lines, written but never
  run at scale", and names the OCR fallback as the part that matters, since "a
  large fraction of pre-1990 assessment reports are scans".
- The embedding service is live and already used by C2.1 — mxbai on `:8083`,
  1,024-dim, with the ceiling audit K2 established: **the limit is ~512 tokens,
  not characters**, so PLAN_C5's "under ~2,700 characters" is the wrong unit and
  chunking must be conservative and back off on refusal.
- The six-question set and the "cite or say not-found" contract are specified in
  the plan; C4's renderer already has a section-7 slot waiting.

C5.2 (Tier-2 barren confirmation) then reuses the same ingested corpus, which is
why the two G2 requirements share one dependency.

## What was considered and set aside, with reasons

- **C6.3 momentum** (~2 days). Now genuinely unblocked — its acceptance criterion
  (a) needs "the momentum surface renders in the viewer" and the viewer exists
  since C4.2 (audit M0). But it feeds the **screening queue** (C4.3) and lapse
  watch, not Gate G2. It makes finding candidates better; G2 is about making one
  candidate *sendable*. Do it after, or in parallel if there is slack.
- **C4.3 screening DSL** (~1 week, listed in Phase 2). Same category — target
  selection, not dossier completeness.
- **C6.5 holding costs.** Code-complete behind `rules/ON.yaml`. Zero engineering
  remains; signing the file turns it on. This is on vis, not on the next session.
- **C6.1 / C6.4 / C6.6.** Blocked on the SEDAR+ PDFs. Worth starting only when
  the A1/A2 campaign lands, and the sensible preparation is to agree the extraction
  schema with that project rather than to build against a corpus that does not exist.
- **C2.3 geochem anomaly layer** (~1 week). PLAN_C2 calls QC/BC geochem "the
  lake's strongest asset", but it is not in the Phase 2 list, Phase 1 and 2 are
  Ontario, and it is currently blocked from the other side too: audit O3 leaves
  QC's wide geochem tables unprocessable on this machine.
- **C3.2 national geophysics via browser automation** (~1–2 days). Should be
  **re-scoped before it is built**. C2.2 landed readable national gravity and
  magnetics covering Ontario at 96–100% cell coverage (audit N1), which is most
  of what C3.2 was for. What remains is whether the GDR grids add resolution over
  CMMI's — a question, not a build.

## Recommended order

1. **C3.4 (Ontario only)** — `fetch_reports()`; ~1 day. Unblocks half of G2.
2. **C5.1** — ingest → chunk → embed → the six-question set → dossier section 7;
   ~4 days. This is the step that changes what a dossier says.
3. **C5.2** — Tier-2 barren labels off the same corpus; ~4 days.
4. **C6.3** — momentum, if there is slack; ~2 days.

Steps 1–3 complete both halves of Gate G2 that this repo can reach. The other two
halves stay blocked on the SEDAR+ PDFs regardless of what is built here, so **G2
cannot be fully met from inside this project** — that constraint should be stated
before the work starts, not discovered at the gate.

## Two decisions that belong to vis, not to the next session

- **Gate G1** is still open: run it on dossiers that name ground and a buyer but
  carry no price, or hold for comps. C3.4 → C5.1 improves the G1 dossiers either
  way, by populating section 7, so it is not blocked behind the G1 answer.
- **`rules/ON.yaml` signature.** Still gates `stakeable_now()`,
  `holding_schedule()` and C6.5, and still needs `consultation_notes` and
  `exempt_lands_notes` plus a numeric `reopening_delay_days` for C1.6.
