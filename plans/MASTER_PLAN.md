# MASTER PLAN — Independent Mineral Property Targeting & Sales System

**Date:** 2026-08-12
**Owner:** vis
**Repo:** `~/projects/canada-geo-data-lake`
**Bulk data:** `/media/vis/Expansion/canada-geo-lake` (external drive) · **Indexes:** `~/infra/canada-geo-lake-data`
**Companion documents:** `PLAN_C0.md` … `PLAN_C6.md` (one component plan per subsystem, each self-contained; read this file first, then the component file being built).

---

## 1. Mission

Operate as an independent prospector across Canada: continuously generate long lists of
mineral potential nationally, identify open or acquirable ground within prospective and
active areas, stake or buy a small number of claims, and sell them to exploration
companies — typically operators holding adjacent ground with an active story. The system
must run at low ongoing human effort, but **every staking, purchase, or pricing decision is
gated by human review** of a generated target dossier. The system recommends; the human
decides.

Two consequences shape every component:

1. **Activity is the primary signal; geology is the validator; economics produces the final
   ranking.** The question the system answers is not "where is the best rock in Canada" but
   "where is exploration interest concentrating, which open cells sit on the plausible
   extension of a specific operator's results, what would those cells cost to hold, and who
   would buy them at what price."
2. **The terminal work product is a target dossier**, not a heatmap. The dossier is both the
   internal decision document and, later, the majority of the sales package handed to a
   buyer.

## 2. Label doctrine (binding on all components)

These decisions are settled. Component plans must not revisit them.

- **The only true negative is barren drilling.** A drillhole is a negative observation only
  for the commodities it was assayed for and the depth interval it tested. Negatives are
  therefore encoded as `(location, depth_from, depth_to, commodities_tested[], evidence)`
  tuples, never bare points. Two tiers: **Tier 1 (provisional)** — drilled, no
  occurrence/deposit record at the site, no follow-up work within a lookback window;
  **Tier 2 (confirmed)** — the assessment report behind the hole has been retrieved and its
  assays/conclusions confirm no significant values. Negatives are used **asymmetrically**:
  hard veto/discount in screening and dossiers; calibration weight (not coverage) in model
  training.
- **Tenure history is never a negative label.** Ground being staked, worked, and dropped is
  confounded by financing cycles and hypothesis choice, and the fact it was staked at all
  validates perceived potential. Tenure history is used as (a) the **heat/momentum signal**
  (staking velocity from daily snapshot diffs) and (b) a **weak positive prior** (repeatedly
  staked ground = repeated independent hypotheses).
- **A non-barren historical hole on open ground is a positive dossier item.** "Drilled, hit
  something, dropped anyway" is the highest-value deal pattern the system exists to find:
  exploration companies cannot survive on mediocre results, so any result strong enough to
  be reported yet abandoned usually signals a non-geological failure (financing, management,
  cycle) over live potential.
- **Deposit-scale prediction is out of scope.** Prospectivity models operate at regional
  cell scale and produce shortlists for desktop diligence, never "stake this cell" verdicts.

## 3. System architecture

```
                ┌─────────────────────────────────────────────────────┐
                │  C3  ACQUISITION & AUTOMATION                        │
                │  tenure daily · geoscience weekly · market daily ·   │
                │  rasters monthly · reports on-demand                 │
                └──────┬──────────────┬──────────────┬────────────────┘
                       │              │              │
            ┌──────────▼───┐  ┌───────▼───────┐  ┌───▼────────────┐
            │ C1 LAND &    │  │ C2 PROSPECT-  │  │ C5 TEXT &      │
            │ ACTIVITY     │  │ IVITY (MPM)   │  │ REPORTS        │
            └──────┬───────┘  └───────┬───────┘  └───┬────────────┘
                   │                  │              │
               ┌───▼──────────────────▼──────────────▼───┐
               │ C6 ECONOMICS & MARKET  → deal ranking    │
               └────────────────────┬─────────────────────┘
                                    │
               ┌────────────────────▼─────────────────────┐
               │ C4 VIEWER · DOSSIER · THEORY COMPILER     │
               │        (human review gate)                │
               └──────────────────────────────────────────┘
                        all of the above sit on
               ┌──────────────────────────────────────────┐
               │ C0 FOUNDATION: repaired lake · hex fabric │
               │ versioned feature store · tenure events   │
               └──────────────────────────────────────────┘
```

## 4. Shared data contracts

All components read and write through these stores. Component plans reference them by name.

| Store | Location | Producer | Consumers |
|---|---|---|---|
| Raw snapshots | `raw/<JURIS>/<CODE>/<YYYY-MM-DD>/` + `_source.json` sidecar | C3 (`harvest.py`) | C0 processing |
| Spatial store | `processed/geo.gpkg` (layer naming `<JURIS>__<CODE>__<file>`) | C0 (`process.py`) | all |
| Tabular store | `processed/tables/*.parquet` | C0 | all |
| Raster store | `processed/rasters/*.tif` (COGs) + `rasters.json` registry | C0/C3 | C2, C4 |
| Catalog + FTS | `catalog.duckdb` | C0 (`build_index.py`) | all |
| Harvest ledger | `manifest.sqlite` | C3 | C0 provenance |
| **Hex fabric** | `processed/fabric/r7.parquet`, `r9/` (per-AOI) | C0 (`fabric.py`) | C1, C2, C4, C6 |
| **Feature store** | `features/<fabric_ver>/<snapshot>/features.parquet` (long format: `cell_id, feature, value`) + `manifest.json` (source hashes) | C0 (`gridify.py`) | C2, C4 |
| **Tenure events** | `processed/tenure_events.parquet` | C0 (diff engine) | C1, C2, C6 |
| **Negatives** | `processed/negatives.parquet` (schema in §2) | C2 (+C5 upgrades) | C2, C4, C6 |
| **Heat** | `processed/heat.parquet` (per r7 cell per quarter) | C1 | C2, C4, C6 |
| **Ownership graph** | `processed/ownership.duckdb` (owners, blocks, adjacency) | C1 | C4, C6 |
| **Rules table** | `rules/<juris>.yaml` (human-verified) | C1 | C4, C6 |
| **Comps DB** | `market/comps.parquet` | C6 | C4, C6 |
| **Buyer profiles** | `market/buyers.parquet` | C6 | C4 |
| **Model registry** | `models/<system>/<version>/` (weights + model card JSON) | C2 | C4 |
| **Dossiers** | `dossiers/<target_id>/<version>/` (JSON + HTML + PDF) | C4 | human, sales |
| Text corpus | `pdfs/<JURIS>/<CODE>/<id>.pdf` + Chroma collections | C3/C5 | C5, C2, C6 |

**Versioning rule (binding):** every model run records `(fabric_version, feature_snapshot,
model_version)`; every dossier value carries `(source_id, snapshot_date)`. Nothing is
reported without provenance.

## 5. Component index

| ID | Name | One-line scope | Plan file |
|---|---|---|---|
| C0 | Foundation | Repair broken harvests; hex fabric; raster pipeline; versioned feature store; tenure event stream | `PLAN_C0.md` |
| C1 | Land & Activity | Open-ground computation, jurisdiction rules, heat detection, ownership/adjacency graph, criticality scoring, lapse watch | `PLAN_C1.md` |
| C2 | Prospectivity (MPM) | Text→prospectivity model, geochem anomaly layer, barren-negative pipeline, spatial-CV/PU protocol, full multi-modality MPM, staking backtest | `PLAN_C2.md` |
| C3 | Acquisition & Automation | Schedulers, geophysics grids via browser automation, STAC remote sensing, on-demand report fetch, market connectors, alerting | `PLAN_C3.md` |
| C4 | Viewer, Dossier, Theory | Dossier generator, map viewer, screening DSL, LLM theory front-end, audit affordances | `PLAN_C4.md` |
| C5 | Text & Reports | On-demand due-diligence RAG, barren confirmation extraction, corpus-scale harvest + NER features | `PLAN_C5.md` |
| C6 | Economics & Market | Comps database, buyer graph, momentum overlay, claim valuation, holding-cost/credit calculator, deal score | `PLAN_C6.md` |

## 6. Environment inventory (verified, reuse — do not rebuild)

- **Harvest framework:** `src/sources.py` declarative registry (592 lines), connectors
  `ckan / arcgis / wfs / ogsearth / es_scroll / direct / scrape(stub)`, `harvest.py`
  (URL re-resolution, sha256 change detection, dated snapshots), `process.py`,
  `build_index.py`, `serve.py` (FastAPI, 327 lines).
- **Anti-bot browser automation:** Camoufox + the SEDAR+/SEDI module from `mining-scraper`
  (ResponseLogger, ShieldTracker, TrustMetric, exponential backoff, Mullvad rotation) —
  proven against harder targets than any geoscience portal.
- **Local LLM stack:** llama.cpp chat at `:8082`, mxbai embeddings at `:8083` (verify
  embedding dimension at runtime and record it in Chroma collection metadata), ChromaDB,
  rag-proxy at `:9100`. `RAG_PLAN.md` still references retired Ollama endpoints — C0 fixes.
- **Visualization:** `mining-viz` live at app.premissive.ca (auth + hosting reusable).
- **Adjacent projects:** `pmx` (NI 43-101 → DCF; shares report feedstock with C5),
  `drill-database` (PostgreSQL, 320 holes; to be treated as downstream of the lake).

## 7. Roadmap

Phases are gated; a phase does not start until the prior gate passes human review.

**Phase 0 — Foundation (≈1.5–2 weeks).** All of C0.
*Gate G0:* the five broken harvests verified on disk with correct byte counts; QC vector
layers queryable in `geo.gpkg`; r7 fabric built and a demonstration feature matrix
generated; `tenure_events` populated for ≥2 jurisdictions with a spot-checked diff.

**Phase 1 — Ontario vertical slice (≈4 weeks).** Ontario first: daily OGSEarth tenure,
202,407 claims, 126k drillholes post-repair, enumerable AFRI index, online map staking.
Build: C1.1–C1.6 for ON; C2.1 (text→prospectivity, national corpus, validated on published
Canadian Zn-Pb results) + C2.2 (ScienceBase benchmark layers) + C2.4 Tier-1 negatives from
ODHD + C2.5 validation module; C4.1 dossier generator v1 + C4.2 minimal map; C6.2 manual
buyer identification for 2–3 live Ontario stories; C6.1 comps collection begins.
*Gate G1 (thesis test):* **5–10 human-reviewed dossiers for real open ground adjacent to
real active stories, each with a named probable buyer and a defensible price range.** If
the best-covered jurisdiction cannot produce credible candidate deals, the fix is in deal
selection (C1/C6), not in more modelling — decide before horizontal investment.

**Phase 2 — Economics + on-demand text (≈3 weeks).** C6.1–C6.5 built properly; C3.4
on-demand report fetch for ON AFRI + BC ARIS; C5.1 due-diligence RAG; C5.2 barren
confirmation (Tier-2 upgrades); C4.3 screening DSL v1.
*Gate G2:* one Phase-1 dossier re-issued with report-backed history, Tier-2 drill labels,
comps-anchored valuation, and buyer capacity — i.e., a dossier you would actually send.

**Phase 3 — Modality closure + expansion (≈4 weeks).** C3.2 national geophysics grids;
C3.3 STAC remote sensing + DEM; C2.7 full-stack MPM baselines with SHAP; C1 extended to
BC, SK, YT, NU; QC GESTIM bulk-licensing decision executed (see Risks).
*Gate G3:* a 4-modality prospectivity model with spatially-blocked CV scores and a
year-N→N+1 staking backtest, run with and without tenure-derived features.

**Phase 4 — Scale (ongoing).** C5.3 corpus-scale ARIS/AFRI harvest + NER cell features;
C4.4 LLM theory front-end; C6.4 fitted valuation model as comps accumulate; alerting
maturity; remaining jurisdictions.

## 8. Risks and standing decisions

- **Québec blindness.** GESTIM claim boundaries have no public API; MB, NL, AB similar.
  QC is a premier map-staking jurisdiction — being blind there is the single largest
  coverage risk to the business. **Action (Phase 1, one email):** request MRNF bulk-license
  pricing; decide buy/skip at G1.
- **Single-buyer dynamics.** Most targets have one natural buyer who can wait you out.
  Mitigations are encoded, not hoped for: criticality scoring (C1.5) prefers cells a buyer
  eventually needs; the deal score (C6.6) carries a `buyer_count > 1` bonus and a buyer
  drill-program-timing term.
- **Heat is public.** Staking velocity is observable by anyone with the same idea; ON/BC
  professional stakers already watch lapses. The defensible edge is the joined signal
  (heat × criticality × geology × barren-veto × buyer capacity) plus the point-in-time
  archive, which a competitor would need years of daily snapshots to reconstruct.
- **Backtest leakage.** The staking backtest can flatter a model that merely learned "near
  existing claims." Always run with and without tenure-derived features and report both.
- **Negative sparsity.** Tier-2 confirmed-barren labels will number in the hundreds at
  first. Acceptable by design — they are a veto and a calibration aid, not the training set.
- **Jurisdictional legal reality.** Staking prerequisites (prospector licences), physical
  staking in YT, consultation obligations, and exempt lands must be human-verified into
  `rules/<juris>.yaml` before first staking in that jurisdiction. No scraper substitutes
  for this.
- **Deferred by decision:** portfolio management, renewal automation, kill-discipline
  tooling — built only after claims are actually held.

## 9. Instructions for component-plan execution (for the model building each component)

1. Read this file, then the single `PLAN_C*.md` being built. Do not modify contracts in §4
   or doctrine in §2; if a task appears to require it, stop and surface the conflict.
2. Never hardcode source URLs; extend `src/sources.py` and let `discover.py`/`harvest.py`
   resolve current URLs at runtime, as the existing connectors do.
3. Every new dataset gets a `_source.json` provenance sidecar and a manifest entry; every
   new derived table gets a snapshot date column.
4. Prefer extending existing modules (`process.py`, `serve.py`, connector classes) over
   parallel implementations. New top-level modules only where the component plan names one.
5. Effort estimates in component plans are calibrated to prior measured work in this repo;
   if actuals exceed estimate by >2×, stop and report rather than pushing on.
6. All licensing follows the Open Government Licence family already documented per source;
   Saskatchewan requires layer+date citation. Check `_source.json` before any redistribution
   inside a dossier that leaves the machine.
