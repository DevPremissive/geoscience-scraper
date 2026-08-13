# PLAN C4 — Viewer, Dossier, Theory Compiler (the Human Review Gate)

**Read `MASTER_PLAN.md` §2–§4 first.** Every staking, purchase, and pricing decision
passes through a human reading artifacts this component renders. The **dossier is the
terminal work product** — the internal decision document and, later, most of the sales
package. The map is navigation. The theory compiler turns plain-language exploration
ideas into testable, repeatable screens.

**Depends on:** C0 (fabric, feature store, provenance), C1 (land/heat/ownership/
criticality), C2 (scores, SHAP, negatives, story-extension JSON), C5 (report excerpts),
C6 (comps, buyers, valuation, holding costs).
**Build order within C4: dossier first, map second, DSL third, LLM front-end last.**
**Estimated effort:** 4.1 ≈ 1 week (v1) · 4.2 ≈ 1 week · 4.3 ≈ 1 week · 4.4 ≈ 3 days ·
4.5 ≈ continuous discipline + 2 days of plumbing.
**New package:** `src/dossier/` (`schema.py`, `assemble.py`, `render/`),
`src/screens/` (`dsl.py`, `compile.py`, `runner.py`), frontend under `viewer/`.

---

## 4.1 Dossier generator (build first; v1 in Phase 1)

### Data model

A dossier is a versioned JSON document rendered to HTML and PDF. Identity:
`target_id` (stable), `dossier_version`, `fabric_version`, `feature_snapshot`,
`generated_at`, `status ∈ {draft, reviewed, approved, rejected, sent}`,
`reviewer_notes`, `signoff {by, date, decision}`.

Sections (each independently regenerable; missing upstream data renders an explicit
"NOT AVAILABLE — <reason>" block, never silence):

1. **Identity & land** — cells (r9 ids + map inset), jurisdiction, area, current
   `land_state` per cell with `as_of` date, staking cost and licence prerequisites from
   `rules/<juris>.yaml`, expiry mechanics relevant to acquisition timing.
2. **Activity** — heat sparkline (8 quarters, both normalizations), tenure-event timeline
   within 5 km, `ever_staked_count` history with the doctrine note (prior interest =
   weak positive prior, per Master §2).
3. **Neighbours** — adjacency-graph excerpt: blocks within 5 km, owners
   (entity-resolved + SEDAR issuer link where joined), block growth history, each
   neighbour's story summary (C6.2 profile) and financing state.
4. **Criticality** — per neighbour block: score, reason codes, trend source, and a
   rendered corridor map figure. Includes the **manual-azimuth input field**: a human-
   entered trend overrides computed trends (C1.5 priority (a)) and is recorded with
   attribution.
5. **Geology** — prospectivity score (calibrated, with model card link), SHAP top-k
   table, per-evidence-layer values vs regional percentiles, story-extension JSON
   rendered as a continuity table (C2.7) when a neighbour block is designated.
6. **Drilling** — every hole within N km (default 2): tier, commodities_tested, depth,
   veto flags where applicable with the conditionality spelled out ("barren for Au to
   120 m; does not test Li thesis"), and the **non-barren highlights** — intercept
   extracts with report citations ("drilled, hit, dropped anyway" items get top billing).
7. **History (reports)** — C5.1 RAG outputs for the standard question set (see C5):
   work-history summary, best historical results, apparent reason work stopped — every
   claim cited as `(report_id, page)`.
8. **Economics** — comps table (C6.1) filtered to (juris, commodity, stage), valuation
   range with geological-value and strategic-value shown **separately** (C6.4), holding
   schedule + transferable assessment credits (C6.5), named probable buyer(s) with
   capacity flags and `buyer_count` (C6.2), deal score decomposition (C6.6).
9. **Recommendation & signoff** — system recommendation with its top-3 drivers and top-3
   risks, then the human decision fields. **No dossier auto-advances past `draft`.**

### Implementation

- `schema.py`: pydantic models for the above; `assemble.py`: pulls each section from its
  producing component's store, stamping `(source_id, snapshot_date)` on every value —
  a value without provenance fails assembly (hard error, by design).
- Rendering: Jinja2 → HTML (self-contained, inline map figures as static PNGs generated
  via the map service's render endpoint, 4.2); WeasyPrint → PDF.
- **Sales variant:** `render(profile="sales")` drops internal sections (deal score,
  buyer capacity assessment, recommendation, valuation internals) and keeps land,
  geology, drilling, history — the buyer-facing package. Licence attribution lines from
  `_source.json` sidecars are auto-inserted (SK requires layer+date citation).
- Storage: `dossiers/<target_id>/<version>/{dossier.json, dossier.html, dossier.pdf}`.

**Acceptance:** one end-to-end dossier for a real Ontario open-ground target adjacent to
a real story, with every section either populated or explicitly NOT-AVAILABLE, reviewed
by human; the sales render contains zero internal-only fields (checked by test, not
eyeball).

## 4.2 Map viewer (~1 week)

Extend `serve.py` (FastAPI, 327 lines, already serving features/tiles) rather than a new
service; reuse `mining-viz` hosting + auth at app.premissive.ca.

> **Scope correction 2026-08-13 (audit D5).** `serve.py` currently exposes `/layers`,
> `/geojson/{layer}`, `/search`, `/coverage`, `/stats` and `/tenure` — **GeoJSON only, no
> vector-tile endpoint**. Tiles are new work, not an existing pattern to extend, and the
> 202,407-claim and 231,389-disposition layers will need them. Budget accordingly, or ship
> v1 on GeoJSON with viewport bbox filtering and add MVT when frame rates demand it.

- **Endpoints:** vector tiles / GeoJSON per `geo.gpkg` layer (GeoJSON exists; tiles are new); fabric
  choropleths (r7) for heat, prospectivity (per model version), and criticality (per
  watched block); `land_state` (r9) within a viewport; tenure-event pulses (recent
  events as a time-filtered layer); `/render?layers=&bbox=` returning a static PNG (used
  by dossier figures — one map code path, not two).
- **Frontend:** MapLibre + deck.gl, `viewer/`. Left rail: jurisdiction/layer toggles,
  model-version picker, watched-blocks list. Click r7 cell → evidence popover (top
  features vs percentile). Click r9 cell / claim → mini-dossier panel with "generate
  full dossier" action (enqueues 4.1). Time scrubber over tenure events (the point-in-
  time archive is a differentiator — surface it).
- 3D drillhole visualization stays in `mining-viz`; this viewer links out per target
  rather than reimplementing.

**Acceptance:** pan/zoom over Ontario with heat + claims + open ground at interactive
frame rates; cell click round-trips to a generated dossier; a second human can navigate
to a named target unaided.

## 4.3 Screening DSL — the theory compiler core (~1 week)

Structured screens first; natural language is a front-end (4.4), never the engine.

**Grammar (v1):**

```
screen "<name>" [commodity <system>] [juris ON,BC,...] {
  where:
    <feature> <op> <value|percentile(p, scope)>
    dist_to(<feature_class>) < <km>
    count(<point_class>[, radius_km]) == 0 | >= n
    tenure == open | expiring(<days>)
    no_barren(commodity=<c>[, min_depth_m=<d>])     # negatives-aware predicate
    heat >= percentile(p, juris)
    within(<named_geometry|block_id buffer km>)
  rank by: <expression over features/scores>
  limit: <n>
}
```

- `compile.py`: DSL → DuckDB SQL over the long-format feature store + spatial predicates
  against fabric/`land_state`/`negatives`/`heat`. Percentile scopes resolve against
  jurisdiction or terrane strata (matching C2.3's normalization so screens and models
  agree on what "anomalous" means).
- `runner.py`: executes against a **named feature snapshot** (no implicit latest —
  Master §4), persists `screens/<name>/<run_id>/` = {screen text, compiled SQL, snapshot
  refs, result cells, run date}. Screens are therefore versioned, diffable
  ("what entered/left this screen since last month" is a standing report), and feed
  lapse-watch rule (c) in C1.6.
- Results surface as a map layer (4.2) + CSV + "generate dossiers for top-k" action.

**Acceptance:** three screens written by hand — a lithology+structure+geochem screen, a
"non-barren hole on open ground" screen (the doctrine's positive corollary as a
one-liner: `count(nonbarren_holes, 1km) >= 1 AND tenure == open`), and an expiring-claims
screen — all compile, run, render, and re-run identically on the same snapshot.

## 4.4 LLM theory front-end (~3 days; Phase 4)

- Local chat model (`:8082`) translates plain-text theories → DSL, few-shot prompted
  with the grammar and worked examples. The compiled screen is **always displayed for
  human approval before execution** — the review gate applies to theories, not just
  money. Unknown feature names fail loudly with the valid-feature list; the model never
  invents layers.
- Round-trip check: DSL → natural-language paraphrase shown beside the original so the
  human can spot translation drift.
- Explicitly out of scope (do not build): LLM-driven evidence re-weighting, automatic
  mineral-systems model synthesis, agentic multi-step targeting. These are open research;
  the DSL is the boundary.

## 4.5 Audit affordances (~2 days plumbing + standing discipline)

- Provenance drawer on every rendered value: `(source_id, layer, snapshot, harvest
  date, licence)` — direct from the stamps that 4.1 assembly enforces.
- Model transparency: every score links its model card (registry, C2); every dossier
  pins `(fabric_version, feature_snapshot, model_versions[], screen run_ids[])`.
- Reproducibility contract: `regenerate(dossier_id, version)` must rebuild
  byte-comparable JSON from pinned inputs; a CI test regenerates one reference dossier
  weekly and diffs.
- Decision log: `dossiers/decisions.parquet` — every signoff (approve/reject + notes)
  accumulates the ground truth for later calibration of the deal score (C6.6) and the
  models (C2): the system's own decisions become training signal, but only through this
  logged, human-labeled path.

## Handoff notes for detailed planning

Delegated: DSL parser implementation (Lark vs hand-rolled — suggest Lark), tile format
(MVT vs GeoJSON by layer size), PDF styling, queue mechanism for dossier generation.
Settled (do not reopen): dossier-first build order, hard-fail on provenance-less values,
sales-render field exclusion by test, DSL-as-engine with LLM-as-front-end-only,
human approval before any screen executes from natural language, no auto-advance past
draft.
