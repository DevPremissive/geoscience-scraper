# MASTER PLAN — Independent Mineral Property Targeting & Sales System

**Date:** 2026-08-12
**Owner:** vis
**Repo:** `~/projects/canada-geo-data-lake`
**Bulk data:** `/media/vis/Expansion/canada-geo-lake` (external drive) · **Indexes:** `~/infra/canada-geo-lake-data`
**Companion documents:** `PLAN_C0.md` … `PLAN_C6.md` (one component plan per subsystem, each self-contained; read this file first, then the component file being built).
**Audit:** `AUDIT_FINDINGS.md` — these plans were written without access to the machine. Every factual claim was verified on 2026-08-13; corrections are folded in below and evidenced there. **Read it before executing any component.**

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

**Dense-embedding exception (added 2026-08-13, audit D3):** the long-format rule holds for
sparse scalar features. It does *not* apply to dense embeddings — 1,024 dims × ~1.76 M r7
cells is ~1.8 billion long rows for one feature family. Store embeddings as a **wide
float32 array artifact** (`features/<fabric_ver>/<snapshot>/embeddings.parquet`, ~7 GB) beside
the long-format table, carrying the same `manifest.json` provenance. This is an ergonomics
carve-out, not a licence to store wide pivots of anything else.

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
- **Anti-bot browser automation:** Camoufox + `/home/vis/projects/sedi-scraper/antibot.py`
  (1,836 lines: ResponseLogger, ShieldTracker, TrustMetric, exponential backoff, Mullvad
  rotation) — proven against harder targets than any geoscience portal. *Note: this module
  lives in `sedi-scraper`, not `mining-scraper`, which imports it via `sys.path`. Reuse from
  this repo needs a path insert or packaging — budget for it.* `mining-scraper` separately
  provides `src/mining_scraper/browser/` (`BrowserSessionManager`, `detect_shield_square`).
- **Local LLM stack:** llama.cpp chat at `:8082` (`qwen3.6-35b`), mxbai embeddings at `:8083`
  — **verified 1,024 dimensions**, `mxbai-embed-large-v1.Q4_K_M`, and it returns **HTTP 500
  above ~2,700 characters** (512-token context), so chunking is mandatory, not optional.
  ChromaDB, rag-proxy at `:9100`. `RAG_PLAN.md` Ollama references are annotated as superseded.
- **Visualization:** `mining-viz` live at app.premissive.ca (auth + hosting reusable).
- **Adjacent projects:** `pmx` (NI 43-101 → DCF; shares report feedstock with C5),
  `drill-database` (PostgreSQL, 320 holes; to be treated as downstream of the lake).

## 6b. Data gap register (verified on disk 2026-08-13)

The full accounting lives in `HANDOFF_AI_EXPLORATION.md` §2/§5; it is reproduced here —
re-verified and re-ranked — because component plans must not have to read another document
to know what data they do and do not have. **Status column is the authority; the handoff's
effort estimates predate the audit and several were wrong.**

### Modality inventory

| Modality | State | Detail |
|---|---|---|
| **Geochemistry** | **STRONG** | QC SIGÉOM 561,232 stream/lake sediment (124 cols, full REE + PGE); BC RGS2020 65,008 (193 cols, multi-method); BC water 4,332; QC heavy minerals/erratics/geochron 3,606/1,562/2,735. Compositional — CLR/ILR required before any ML (C2.3) |
| **Tenure** | **VERY STRONG (spatially), ABSENT (temporally)** | ~1.0 M polygons: ON 466,287 across 5 layers; YT 244,703 historical + 168,481 quartz + 33,934 placer; NU 34,411 + leases/permits; BC 42,285; NB, NS, NT. **But two snapshot dates and current-registry attributes only — see gap #10** |
| **Deposit labels** | **GOOD, unharmonised** | MRDS 304,632 (rich free text); USMIN ~750k; BC MINFILE 15,142 + 1,696 products + 13,731 reserves; QC métalliques/mines/activités 9,291/79/1,078; QC MINPOT 5,622 pre-computed targets (benchmark comparator); NB 1,611; SK SMDI 140 (**suspect — see gap #15**) |
| **Drillholes** | **MODERATE, and mis-distributed** | QC 187,321 with downhole `PROF/LITH/MINR` interval structure; NB 17,887; ON 172,259 available via OMEIS ArcGIS (gap #5). **BC — none registered (gap #13)**; NS 28,341 blocked |
| **Geology** | **PARTIAL — mostly present but unreadable** | FED CGMC 610 MB national lithology raster + legend GPKG (untouched by `process.py`); QC bedrock 778,153 + 385,731 outcrop points and polygons; BC Bedrock Geology 2018; QC quaternary 120,184 + 18,175. **Most of it is trapped behind gap #12** |
| **Geophysics** | **EMPTY** | Only QC EM-anomaly SHP. No gravity, magnetics, radiometrics, DEM |
| **Remote sensing** | **ZERO** | No Sentinel-2, Landsat, ASTER, hyperspectral, DEM of any kind |
| **Text** | **ZERO bytes** | `pdfs/` empty; Chroma effectively empty (4 rows). Enumerable but unharvested: ON AFRI 62,357, BC ARIS 33,500+, SK SMAD ~14,889, NL GeoFiles 5,000+, NS DCDH, NTGS, NB PARIS |

### Ranked gaps

| # | Gap | Impact | Owner | Status (2026-08-13) |
|---|---|---|---|---|
| 10 | **No historical tenure archive — outside Ontario.** 2 snapshot dates 1 day apart. BC/YT/NU publish current holdings only, so their attribute history omits dropped ground | Blocks heat, staking backtest, momentum for every jurisdiction except ON | **C3.1** (Phase 0) | **Partially closed (audit F2).** Ontario retains 431,557 cancelled claims with issue + termination dates back to 2018-04 — an unbiased 8-year record. Everywhere else still accrues forward only, so C3.1 stays in Phase 0 |
| 1 | **National geophysics grids** (mag 200 m/1 km, grav 2 km, radiometrics 250 m) | Blocks the most-used MPM evidence layers | C3.2 | Open. GDR portal needs browser automation; tooling exists at `sedi-scraper/antibot.py` |
| 12 | **46 misnamed containers, ~7.7 GB** — `.shp`/`.gpkg`/`.fgdb`/`.gdb` files that are actually ZIPs (QC 37, NB 6, NS 2, BC 1) | Most of the geology modality is on disk but unreadable; **includes BC Bedrock Geology 2018, which Phase 1 needs** | **C0.2** | **NEW.** One content-sniff fix in `process.py:expand()` unblocks all 46 |
| 2 | **Remote sensing + DEM: nothing** | Blocks EO-derived evidence and alteration indices | C3.3 | Open. STAC APIs — open, no auth, no scraping |
| 3 | **No raster pipeline or grid fabric** | Without it there is no feature matrix, so no MPM at all | C0.4/C0.5 | Open. All packages verified installable for py3.12 |
| 4 | **Text corpus at zero** | Blocks due-diligence RAG, Tier-2 negatives, NER features | C5 / C3.4 | Open. **BC ARIS first** — now aligns with BC Phase 1 |
| 11 | ~~**Ontario ownership + expiry absent**~~ | — | C0.1 | **CLOSED same day (audit F1).** The gap was in the *product harvested*, not in Ontario. `mlas_operational_gis_data.zip` carries `HOLDER` + `ISSUE_DATE` + `ANNIVERSAR` + `CLAIM_DUE_` on all 401,594 claims, 100% populated, no auth. Register it in `sources.py`; C0.9's MLAS scrape is withdrawn |
| 16 | **The Ontario tenure harvest is the wrong product and undercounts by ~50%** — 202,407 KMZ claims vs 401,594 actual, with no attributes | Every ON land, ownership and activity computation runs on half the province | **C0.1** | **NEW (audit F3).** The province labels the harvested KMZ "unofficial… for viewing purposes only" |
| 5 | **Broken/never-run harvests** | CGMC, ON bedrock/surficial/ODHD/OMI/geochem/geophys, OAFD, AMIS, FED CDoGS + tenure + deposits | C0.1 | **Re-diagnosed.** Not "1 day of re-runs": ~1.3 GB was fetched then lost to a `harvest.py` defect, and the Ontario items are better served by public ArcGIS REST than the original paths |
| 13 | **BC has no drillhole source registered** | Phase-1 dossier "Drilling" section (C4 §6) will be empty for BC targets; C2.4 negatives must come from QC/NB/ON | C0.1 discovery + C5.2 | **NEW.** See Phase-1 note below |
| 14 | **4 of 5 BC raw datasets never reached `geo.gpkg`** — only `BC_MTA_CURRENT` did | BC bedrock, MINFILE spatial and MTA grid absent from the spatial store; BC bedrock is needed by C2.1 corpus text | C0.2 | **NEW.** Largely a consequence of #12 |
| 6 | **No label harmonisation** | MPM positives unusable across jurisdictions | C2.8 | Open |
| 8 | **No spatial CV / PU protocol** | Results silently inflated | C2.5 | Open. Must exist before the first model |
| 7 | **No modelling layer** | A lake with nothing attached | C2.7 | Open. `eis-toolkit` is **not on PyPI**; `uncover-ml` 0.4.0 is |
| 15 | **`SK__SK_SMDI` holds 140 rows** | Implausibly low for the SK Mineral Deposit Index | C0.1 | **NEW.** Flagged, undiagnosed |
| 9 | **No 3D/voxel or uncertainty story** | The most open frontier | deferred | Deferred until the above land; QC drillholes + `mining-viz` are the substrate |

### What this means for Phase 1 (Ontario)

Once #11 and #16 are closed by registering the MLAS bundle, Ontario is complete across every
component Phase 1 exercises:

- **Tenure & ownership:** 401,594 cell claims with `HOLDER` and three date fields; 16,812
  alienations; 22,940 mining land tenures with `EXPIRY_DAT`; 799 plans & permits.
- **Activity history:** 431,557 cancelled claims, 2018-04 onward, unbiased — but split
  `STATUS` first (303,138 `Cancelled` are real drops; 102,966 `Amalgamated` and 2,379
  `Merged` are not).
- **Drilling:** 172,259 OMEIS holes with `HOLE_TYPE` and `ELEMENTS` (#5 via ArcGIS).
- **Reports:** 62,436 assessment-file footprints with geometry, keyless (#4/C3.4).
- **Geology:** MRD126 bedrock via the same ArcGIS service.

Residual Ontario weakness is *geochemistry* — its lake-sediment survey never harvested (#5)
— where BC's RGS2020 (65,008 × 193) and QC's SIGÉOM (561,232 × 124) are far stronger. Since
C2 is national, the geochemical anomaly layer (C2.3) should be built on QC/BC data even while
C1/C4/C6 run on Ontario. That is a feature of the split, not a conflict.

Two Phase-1 dependencies remain open regardless of jurisdiction: the raster/fabric pipeline
(#3) and the container-sniffing fix (#12), which unblocks QC and BC bedrock geology for the
C2.1 corpus.

## 6c. Tenure-history horizon by jurisdiction (verified 2026-08-13, audit G)

Every jurisdiction's digital tenure record begins at its **conversion from ground staking to
online map staking**. That single event, not digitisation policy, is why the usable history
horizon differs by more than twenty years across Canada. Planning must treat the horizon as a
per-jurisdiction constant.

| Juris | Converted | System | Unbiased drop history from | Pre-conversion recoverable |
|---|---|---|---|---|
| QC | 2000 | GESTIM | 2000 (if cancellations retained — unverified) | — |
| BC | 2005-01-12 | MTO | 2005 (via Tenure History SP + Client XREF) | ground-staked "legacy claims" continued |
| NL | 2005-02-28 | MIRIAD | 2005 (unverified) | — |
| NB | 2010-04-14 | e-CLAIMS | 2010 (unverified) | — |
| SK | 2012-12-06 | MARS | shallow — Lapsed layer holds 357 rows | — |
| NS | 2013-08 | NovaROC | 2013 (unverified) | — |
| **ON** | **2018-04-10** | MLAS | **2018-04** — 431,557 cancelled claims | `MENDM_Legacy_Claims`: 33,439 survivors with recording dates to **1980**; scanned claim maps; pre-MLAS CLIMS database is access-restricted |
| NU | 2021-01-30 | Map Selection | 2021-01 | ground-staked claims converted to "unit claims" |
| **YT** | **never** | physical staking | **1990s onward, already on disk** — `YT_HISTORICAL_CLAIMS` 244,703 with owner + dates back to 1899 | n/a |
| MB / NT | never | physical staking | none published | — |

**Two counter-intuitive consequences.**

1. **The jurisdictions that never modernised have the deepest history.** Yukon never converted,
   so its register was never truncated — 238,398 expired tenures with owners and staking dates,
   already harvested and sitting unexamined in `geo.gpkg`. Ontario, the most modern system,
   has the shortest unbiased window of the major jurisdictions.
2. **Ontario's pre-2018 window is asymmetric.** Staking dates for *survivors* reach back to
   1980 via `MENDM_Legacy_Claims`; ground *dropped* before 2018-04 is not recoverable from any
   public product. Any pre-2018 Ontario series must therefore be labelled
   `survivorship_biased = true`, exactly as the BC/YT current-registry layers are — while the
   post-2018 Ontario series is clean.

## 6d. New-dataset acquisition register (audit G4)

None of these is registered in `sources.py`. Ordered by value per unit of effort — the top
three cost essentially nothing.

| # | Jurisdiction / dataset | What it unlocks | Effort |
|---|---|---|---|
| **N1** | **YT `YT_HISTORICAL_CLAIMS`** — already harvested | 244,703 owner-attributed expired tenures, staking dates to 1899. Makes YT a full C1/C2/C6 jurisdiction immediately | **none** — promote the layer, parse epoch-ms dates |
| **N2** | **NS Mineral Rights Database** — already harvested | NS tenure entirely absent from `geo.gpkg` today; unblocks a whole jurisdiction | **none** — fixed by the C0.2 container sniff (gap #12) |
| **N3** | **ON `endm_administrative_gis_data.zip`** | `MENDM_Legacy_Claims` (33,439, dates to 1980) + the 5.2 M-cell provincial grid, mining divisions, lots/concessions | one registry entry, 640 MB |
| **N4** | **SK `Mineral_Tenure_Crown_Dispositions`** FeatureServer | SK tenure with `OWNERS` + `EFFECTIVED` + `GOODSTANDI` (7,456) — we hold no SK tenure at all today; plus **Re-opening Lands** with `POSTEDON`, a direct staking-opportunity feed | existing `arcgis` connector |
| **N5** | **BC Client Tenure XREF + Person Organization MVW** | Ownership join and client identity — turns BC's 303,235-row tenure history from geometry into attributed events; feeds C1.4 entity resolution | BCGW download |
| **N6** | **BC `MTA_ACQUIRED_TENURE_HISTORY_SP`** | 303,235 historical tenure geometries back to 2005 | existing `wfs` connector |
| **N7** | **BC Reserve Sites + Land Reserve History** | The "no-registration reserve" subtraction layer C1.1 lists as a needed new source, current and historical | WFS/BCGW |
| **N8** | **QC GESTIM bulk FTP** | QC *tenure* — we harvest SIGÉOM geoscience but hold no QC claims at all. Weekly refresh | new registry entry |
| **N9** | **NU Mineral Tenure — Mineral Claims** | Daily claim extents; NU history starts 2021-01 | `open.canada.ca` |
| **N10** | **BC Application / Application Event** | Candidate true tenure-event log rather than snapshot diffs | BCGW |
| **N11** | ON Historical Mining Claim Maps | Scanned township claim maps, pre-digital. Diligence aid only — not georeferenced, no bulk download | low priority |
| **N12** | ON Mining Claims Information Database | The pre-MLAS system of record | **access-restricted — send one email** |

**Sequencing note.** N1–N3 belong in C0 alongside the MLAS registration, because they change
what Phase 1 can demonstrate at no acquisition cost. N4–N7 belong with the jurisdiction they
serve. N8 matters when QC comes into scope and is the only route to Québec tenure, which
§8 already flags as the largest coverage risk.

## 7. Roadmap

Phases are gated; a phase does not start until the prior gate passes human review.

**Phase 0 — Foundation (≈2–2.5 weeks).** All of C0, **plus C3.1 (daily tenure scheduler) and
C3.6 (alerting) pulled forward from C3.**

C3.1 moves here because the archive clock is the one thing that cannot be caught up later.
The lake holds **two snapshot dates one day apart** (audit A1), and the BC/YT attribute
dates that partially substitute describe only *surviving* tenures (audit A2), so every week
without daily snapshots is a week of unbiased history permanently lost. C3.6 moves here
because C1.6's acceptance depends on it.

*Gate G0:* repaired harvests verified on disk by `verify_harvest.py`; QC vector layers
queryable in `geo.gpkg`; r7 fabric built and a demonstration feature matrix generated;
**daily tenure snapshots running unattended for ≥7 consecutive days with heartbeats**; the
`tenure_events` diff engine demonstrated on those consecutive days for ≥2 jurisdictions
(replaces the old "backfill ≥2 jurisdictions" criterion, which the data cannot satisfy);
the MLAS operational bundle registered and Ontario claims at 401,594 with `HOLDER` parsed (C0.1).

**Phase 1 — Ontario vertical slice (≈4 weeks).** *Briefly switched to BC on 2026-08-13 and
reverted the same day — see audit F.* The switch rested on a finding that Ontario carries no
owner or dates. That was true of the OGSEarth KMZ the project had been harvesting, and false
of Ontario: the **MLAS operational bulk shapefiles** publish `HOLDER`, `ISSUE_DATE`,
`ANNIVERSAR` and `CLAIM_DUE_` on all **401,594** cell claims (100% populated, 1,403 distinct
holders), plus **431,557 cancelled-claim records with termination dates** — an eight-year
staked-and-dropped history with no survivorship bias.

Ontario is consequently the strongest slice available on every axis that matters here:
ownership and expiry (C1.4/C1.5/C1.6/C6.2), real activity history (C1.3/C2.6/C6.3 runnable
now, not in four quarters), 172,259 OMEIS exploration drillholes with `HOLE_TYPE` and
`ELEMENTS` (C2.4), 62,357 AFRI assessment reports (C3.4/C5), and online map staking.

Build: C1.1–C1.6 for ON; C2.1 (text→prospectivity, national corpus, validated on published
Canadian Zn-Pb results) + C2.2 (ScienceBase benchmark layers) + C2.4 Tier-1 negatives from
OMEIS + C2.5 validation module; C4.1 dossier generator v1 + C4.2 minimal map; C6.2 buyer
identification for 2–3 live Ontario stories; C6.1 comps collection begins.
*Gate G1 (thesis test):* **5–10 human-reviewed dossiers for real open ground adjacent to
real active stories, each with a named probable buyer and a defensible price range.** If
the best-covered jurisdiction cannot produce credible candidate deals, the fix is in deal
selection (C1/C6), not in more modelling — decide before horizontal investment.

*British Columbia is the second jurisdiction,* and remains the stronger one for evidence:
RGS2020 geochem (65,008 × 193), MINFILE 15,142 deposits, and tenure carrying `OWNER_NAME`
and `CLIENT_NUMBER_ID`. Its weakness is temporal — a current-registry layer with no dropped
ground — which C3.1 fixes forward.

**Phase 2 — Economics + on-demand text (≈3 weeks).** C6.1–C6.5 built properly; C3.4
on-demand report fetch for ON AFRI + BC ARIS; C5.1 due-diligence RAG; C5.2 barren
confirmation (Tier-2 upgrades); C4.3 screening DSL v1.
*Gate G2:* one Phase-1 dossier re-issued with report-backed history, Tier-2 drill labels,
comps-anchored valuation, and buyer capacity — i.e., a dossier you would actually send.

**Phase 3 — Modality closure + expansion (≈4 weeks).** C3.2 national geophysics grids;
C3.3 STAC remote sensing + DEM; C2.7 full-stack MPM baselines with SHAP; C1 extended to
BC, SK, YT, NU; QC GESTIM bulk-licensing decision executed (see Risks).
*Gate G3:* a 4-modality prospectivity model with spatially-blocked CV scores, run with and
without tenure-derived features. **The year-N→N+1 staking backtest is deferred to whenever
the snapshot archive spans two comparable periods** — it cannot be run at Phase 3 on data
that starts accumulating in Phase 0 (audit A1/A2). Until then C2.6 ships only the
survivorship-caveated descriptive series; the backtest is a gate on its own timeline, not
on Phase 3.

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
- **The point-in-time archive does not exist yet — and cannot be bought or backfilled.**
  (Audit A1/A2.) Two snapshot dates, one day apart, 61 days stale as of 2026-08-13.
  Tenure registries publish *current* holdings only: BC has `TERMINATION_DATE` on 23 of
  42,285 rows, and Yukon retains 2,603 expired against 164,985 active claims, so
  attribute-derived history systematically omits dropped ground — precisely the signal the
  business depends on. The moat is real but its clock starts the day C3.1 runs, which is
  why C3.1 is now Phase 0. Treat any pre-archive staking series as descriptive and label it
  survivorship-biased wherever it appears.
- **The Ontario tenure licence restricts the sales package, not the analysis.** (Audit H.)
  The MLAS bundle ships under MNDM Electronic Information Products terms, not an open licence:
  commercial distribution, creation of value-added products, and reproduction of maps or figures
  all require **prior written permission from MNDM**. Internal harvesting, modelling and
  decision-making are unaffected; the buyer-facing dossier — §1's terminal work product — is
  exactly what is reserved. Start the permission request early (`Pubsales.ndm@ontario.ca`,
  `Copyright@gov.on.ca`) and ask in the same letter whether an Open Government Licence – Ontario
  version exists. Until answered, the sales render carries attribution and our own derived
  figures rather than reproduced MNDM maps.
- **Verify the product, not just the portal.** (Audit A2 → F.) For half a day this plan
  recorded "Ontario publishes no ownership or expiry" as a standing risk and moved Phase 1 to
  BC because of it. The finding was rigorous about the OGSEarth KMZ — checked to the raw tile
  — and wrong about Ontario: the MLAS operational bulk shapefiles publish `HOLDER`,
  `ISSUE_DATE`, `ANNIVERSAR` and `CLAIM_DUE_` openly, daily, on twice as many claims as the
  KMZ carries. **The standing risk is the general one:** a jurisdiction's most visible
  endpoint is often not its authoritative one, and an absence proved against one product is
  not an absence. Before recording any dataset as unavailable, enumerate the agency's bulk
  download page — not only its map service and open-data catalogue.
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
