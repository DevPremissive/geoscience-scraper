# PLAN C2 — Prospectivity (MPM): Models, Negatives, Validation, Backtest

**Read `MASTER_PLAN.md` §2–§4 first**, especially the label doctrine — it is binding here.

C2 produces two products:
1. **Long-list generator** — national ranked cells per commodity system (the
   boil-the-ocean screen feeding the watchlist).
2. **Story-extension validator** — given a neighbour's block and commodity, an evidence
   report on whether the geology plausibly continues onto specific open cells.

C2 never ranks deals (C6 does) and never outputs staking verdicts (the human does).

**Depends on:** C0.4–0.6 (rasters, fabric, feature store); C0.7 + C1.3 (backtest labels,
heat covariate); C5.2 (Tier-2 negative upgrades); C3.2–3.3 (geophysics + EO for 2.7).
**Estimated effort:** 2.1 ≈ 1 week; 2.2 ≈ 0.5 day; 2.3 ≈ 1 week; 2.4 ≈ 3 days;
2.5 ≈ 2 days (before any model); 2.6 ≈ 2 days; 2.7 ≈ 2–3 weeks after Phase 3 data lands.
**New package:** `src/mpm/` — `corpus.py`, `labels.py`, `negatives.py`, `validation.py`,
`models/`, `backtest.py`, `anomaly.py`.

---

## 2.1 Text→prospectivity model (first model; Phase 1; ~1 week)

The only end-to-end demonstrated foundation-model pathway in the literature, reproducible
today with zero new acquisition, on datasets already on disk, validatable against
published Canadian Zn-Pb MVT results.

**Corpus assembly (`corpus.py`).** Map-unit *description text*, per r7 cell:
- CGMC legend GPKG unit descriptions (English only for embedding; keep French for
  provenance).
- Provincial bedrock polygon descriptions: QC SIGÉOM (after C0.2 lift), BC bedrock
  shapefile attributes, ON MRD126 (after C0.1 repair).
- MRDS free-text fields (304,632 records): `dep_type, model, alteration, ore_ctrl,
  hrock_type, arock_type, structure, tectonic`.
- MINFILE deposit-type descriptions.
- Join: `gridify_vector(..., mode="text_concat")` — polygon text concatenated per cell
  with area-share tokens; point-record text (MRDS/MINFILE) appended to the containing
  cell. Store the assembled per-cell document alongside its source list.

**Embedding.** mxbai at `:8083` — verified `mxbai-embed-large-v1.Q4_K_M`, **dimension 1,024**;
record model name + dimension in the feature-store manifest.

> **Two hard constraints, measured 2026-08-13 (audit D3/D4).**
>
> 1. **Chunking is mandatory, not a tuning choice.** The server accepts ~2,687 characters
>    (~670 tokens) and returns **HTTP 500** beyond ~2,781 — the model's context is 512
>    tokens. Concatenated cell documents will routinely exceed this, so `text_concat` output
>    must be chunked and pooled (chunk-and-mean, area-share weighted) before embedding. The
>    handoff note below lists this as a delegated nicety; it is not — unchunked input fails
>    outright. Check whether the llama.cpp server context can be raised before engineering
>    around it.
> 2. **Do not write embeddings to the long-format store.** 1,024 dims × ~1.76 M r7 cells is
>    ~1.8 billion long rows for one feature family. Per the Master §4 dense-embedding
>    exception, write `features/<fabric_ver>/<snapshot>/embeddings.parquet` as a wide float32
>    array (~7 GB) with the same manifest provenance. `textemb_NNN` naming stays, as columns
>    of that artifact rather than rows of the long table.
>
> Throughput is not a constraint: ~289 docs/s measured, ≈1.7 h for the full r7 fabric.

**Labels (`labels.py`).** Positives per commodity system from MRDS + MINFILE + QC
occurrence layers, filtered by a deposit-type crosswalk (see 2.8). Start with **Zn-Pb
MVT** solely because two published Canadian studies used it — giving an external answer
key. Second system: **orogenic Au** (business-relevant, label-rich).

**Models.** Baseline: logistic regression / Naïve Bayes on embeddings (matches published
setup). Then XGBoost on embeddings + basic categorical geology. All runs go through 2.5
validation and land in the model registry (`models/<system>/<ver>/` + model-card JSON:
data snapshot, fabric version, CV design, scores, top SHAP features, known caveats).

**Acceptance:** spatially-blocked AUC in the published ballpark for MVT; high-scoring
cells visibly coincide with QC MINPOT's 5,622 pre-computed targets and the published
national prospectivity surfaces more than chance; a rendered national map reviewed by
human.

## 2.2 ScienceBase benchmark & evidence pull (~0.5 day)

Register a `sciencebase` source (plain JSON REST, no scraping) for the CMMI data release
(parent item + per-layer children): gridded gravity variants + HGM/worm products,
long-wavelength magnetic derivatives, LAB/tomography depth, sediment-thickness and
proximity layers, and the published MVT/CD prospectivity GeoTIFFs. Ingest via
`rasters.py`; gridify onto r7.

Three uses: (a) reference feature matrix to validate `gridify.py` aggregation choices;
(b) pre-derived geophysical evidence partially substituting for national grids until C3.2
lands; (c) published surfaces as benchmark comparators for every model this component
ships.

## 2.3 Geochemical anomaly layer (~1 week)

The lake's strongest asset: QC SIGÉOM 561,232 stream/lake-sediment samples (124 columns
incl. full REE + PGE) + BC RGS2020 65,008 samples (193 columns, multi-method), plus
FED CDoGS after C0.1.

`anomaly.py` pipeline:
1. **Harmonization:** per-element unit normalization; **multi-method reconciliation** for
   BC (same element by FA/AAS/ICP/INA — define a per-element method preference order,
   keep the runner-up as a QC column); detection-limit handling (values at DL → DL/2
   imputation v1; flag for ROS refinement later); censoring flags preserved.
2. **Compositional transform:** CLR (ILR where a full composition is available) — raw
   ppm must never enter a model.
3. **Anomaly scoring, spatially adaptive:** background varies regionally, so score within
   local windows — v1: robust z-scores (median/MAD) within terrane_id × catchment-order
   strata; v2: local neighborhood models. Emit per-element and multivariate (robust
   Mahalanobis on CLR) anomaly scores per sample, then `gridify_points(..., agg=max&p95)`
   onto r7.
4. **Pathfinder sets** per commodity system (config, not code): e.g. MVT → Zn-Pb-Cd-Ba;
   orogenic Au → Au-As-Sb-W.

**Acceptance:** anomaly maps for two systems reviewed by human; known camps light up;
method-reconciliation spot check on 20 BC samples; no raw-ppm feature reaches the store.

## 2.4 Barren-negative pipeline (~3 days)

Implements Master §2 doctrine exactly. Output: `processed/negatives.parquet`:

```
hole_id, source_code, geometry, depth_from_m, depth_to_m (null = unknown),
commodities_tested[] (null = unknown), tier ∈ {1,2},
evidence: {no_occurrence_within_m, no_restake_within_years, no_followup_drilling,
           report_id, assay_summary, verdict_confidence},
review_status ∈ {auto, human_confirmed}, snapshot
```

**Tier 1 (provisional), SQL-definable from data on hand:**
A drillhole (ON OMEIS 172,259 — see note, QC 187,321, NB 17,887) is provisionally barren iff
ALL of: no occurrence/deposit record (MRDS, MINFILE, OMI, QC occurrences) within 500 m;
no later drillhole within 500 m (no follow-up); no `staked` tenure event overlapping its
location in the N years after its date (default N=7) — **note the deliberate asymmetry
with doctrine:** absence-of-restaking may *support* a provisional negative, but
presence-of-restaking only removes the hole from the negative set; it is never itself a
positive label.

> **Two corrections 2026-08-13 (audit C1).**
>
> **Filter by hole type first — this is a correctness requirement, not a refinement.** The
> Ontario Borehole Database (`ON_ODHD`) mixes percussion, overburden, sonic, geotechnical and
> water-well holes with exploration holes. A water well that found no gold is not evidence of
> barrenness, and treating it as a negative would poison the veto and the calibration set.
> Use the **OMEIS Drill Hole layer** (LIO ArcGIS layer 47, 172,259 records) instead, and
> filter on `HOLE_TYPE` / `HOLE_TYPE_CODE` (`DD` = Diamond Drill Hole) before any hole enters
> the negatives pipeline. Apply the equivalent filter to QC and NB where a hole-type field
> exists; where none exists, record `hole_type = unknown` and weight the veto down.
>
> **`commodities_tested` is largely obtainable, not null.** OMEIS layer 47 publishes an
> `ELEMENTS` field per hole, plus `YEAR_DRILLED`, `LENGTH`, `AZIMUTH` and `DIP` for the depth
> interval. The fallback below still applies wherever `ELEMENTS` is empty, but it should be
> the exception for Ontario rather than the rule.

Where the source lacks assayed-commodity info, `commodities_tested = null` and the veto
applies only generically with reduced weight; depth nulls likewise.

> **Jurisdiction coverage warning (gap register #13).** C2 is national, so the negatives
> table is built from wherever drillholes exist — QC 187,321, NB 17,887, ON 172,259 via
> OMEIS. **British Columbia, the Phase-1 jurisdiction, has no drillhole source registered
> at all.** Phase-1 BC dossiers will therefore have no drill-based veto flags and no
> non-barren highlights, and C4.1's "NOT AVAILABLE — <reason>" block must state that
> plainly rather than rendering an empty section. C0.1 carries a half-day discovery task to
> establish whether a bulk BC drillhole dataset exists; if it does not, BC drill evidence
> only becomes available through C5.2 intercept extraction over ARIS reports, which would
> justify pulling C5.2 forward from Phase 2.

**Tier 2 (confirmed):** C5.2 retrieves the assessment report behind the hole, extracts
assay summaries/conclusions, and upgrades tier with `report_id + assay_summary`. Human
review of a 10% sample before any Tier-2 batch enters training.

**Consumption rules (enforced in code, not convention):**
- Screening/dossier: any target cell containing a negative gets a visible veto flag with
  the tuple details (commodity- and depth-conditional — a hole barren for Au does not
  veto a Li thesis; a 50 m hole does not veto a deep target; nulls veto weakly and say so).
- Training: negatives enter PU learning as high-confidence labeled negatives with weights
  by tier and null-ness; they are a calibration aid, not the negative class (counts will
  be small vs. a ~2 M-cell fabric).
- **Positive corollary:** `negatives.py` also emits `nonbarren_holes.parquet` — holes with
  an occurrence within 500 m or with extracted intercepts (from C5.2) on currently open
  ground. This is a first-class C4 dossier feed and a C6 valuation input ("drilled, hit,
  dropped anyway").

## 2.5 Validation module — built before the first model (~2 days)

`validation.py`, mandatory import for every training run:
- **Spatially blocked CV:** block size from the empirical autocorrelation range of 2–3
  key evidence layers (variogram; default fallback 50 km blocks).
- **Leave-one-terrane-out:** using `terrane_id` from the fabric (C0.5) — the honest
  transferability estimate.
- **PU protocol:** PU bagging (spy/two-step) over unlabeled cells; class-prior sensitivity
  sweep reported on every model card.
- **Calibration:** reliability curves; prospectivity scores shipped to C4/C6 must be
  calibrated probabilities-of-usefulness, not raw margins.
- Random-split results may be computed for curiosity but are barred from model cards.

## 2.6 Staking backtest (~2 days build; **cannot run until the archive spans two periods**)

> **Deferred 2026-08-13 (audit A1/A2).** This is the plan's business-shaped metric and it is
> currently unrunnable. The lake holds **two snapshot dates one day apart**; C0.7's assumed
> backfill does not exist. The obvious substitute — deriving year-Y staking from tenure
> attributes — **does not work either**, and the reason matters: BC and YT publish
> *current-registry* layers (BC `TERMINATION_DATE` non-null on 23 of 42,285 rows; YT 2,603
> expired against 164,985 active). Ground staked in year Y and dropped before today is
> simply absent. A backtest trained on survivors would score the model on "ground that was
> staked *and kept*", flattering it in exactly the direction that matters least — the
> business is built on finding ground that gets dropped.
>
> **Therefore:** build `backtest.py` in Phase 1 against synthetic and single-pair fixtures so
> the harness is ready and tested, but do not report backtest numbers, and do not gate Phase
> 3 on them (Master §7 G3 amended). Run it for real when C3.1's daily snapshots span two
> comparable periods. Yukon's `STAKING_DATE` series (back to 1902, int64 epoch-ms) may be
> shipped meanwhile as a **descriptive** staking-rate chart carrying
> `survivorship_biased = true` — never as backtest ground truth.

The business-shaped metric: does the model rank ground the industry subsequently paid to
stake?
- `backtest.py`: features frozen at snapshot year Y → labels = r7 cells intersecting
  `staked` tenure events in year Y+1 (from the C3.1 forward archive; multiple Y once
  snapshots allow).
- **Leakage control (binding):** run every backtest twice — with and without
  tenure-derived features (heat, `ever_staked_count`) — and report both. The gap measures
  momentum-following vs geological skill; both are useful, but the model card must say
  which one the model has.
- Metric: precision@k and lift over a population-of-claims baseline, per jurisdiction.

## 2.7 Full multi-modality MPM (Phase 3; ~2–3 weeks)

After C3.2 (geophysics grids) and C3.3 (EO + DEM):
- Evidence stack per r7 cell: geology categoricals + distances (faults/contacts),
  text embeddings (2.1), geochem anomalies (2.3), mag/grav/radiometric derivatives (grids
  + ScienceBase products), EO alteration indices + DEM derivatives (C3.3), heat +
  weak-prior (flagged for backtest exclusion).
- Models: WoE and RF/XGBoost baselines first — via **EIS Toolkit** (prospectivity-specific,
  preferred) with UNCOVER-ML as fallback; SHAP attribution mandatory (feeds C4 dossiers).
  *Availability check 2026-08-13: `eis-toolkit` is **not on PyPI** — plan on a source/conda
  install and verify Python 3.12 compatibility before committing to it. `uncover-ml` 0.4.0
  installs cleanly, so the fallback is the safer default if the spike runs long.*
  Deep/GNN variants only if baselines plateau and only through 2.5.
- **Story-extension validator mode:** given (block_id, commodity) from C1/C4 —
  restrict to the block's r7 neighborhood, report per-open-cell: evidence continuity vs
  the block's own cells (same unit? same anomaly trend? structure continues?), SHAP-style
  contribution table, negative-veto flags. Output is a structured JSON consumed directly
  as a dossier section — this, not the national map, is the mode used in live deals.

## 2.8 Label crosswalk (~2 days, alongside 2.1)

Commodity + deposit-type crosswalk across MRDS free text, MINFILE codes, QC French
substance codes, NB — a versioned mapping table (`labels/crosswalk.csv`), aligned to a
public vocabulary where convenient. Scope-limited to the commodity systems actually
modelled; not a general ontology project.

## Acceptance (component-level)

- [ ] 2.1 model card with blocked-CV scores + benchmark comparison, human-reviewed map
- [ ] 2.4 negatives table populated for ON+QC+NB Tier 1, **hole-type-filtered**; veto flags rendering in a sample dossier
- [ ] 2.5 in place before any training run exists in the registry
- [ ] 2.6 harness built and fixture-tested — **backtest report deferred until the C3.1 archive spans two periods** (audit A1/A2)
- [ ] 2.7 4-modality model card + story-extension JSON for one live block

## Handoff notes for detailed planning

Delegated: embedding pooling for very long cell documents (truncate vs chunk-and-mean),
variogram implementation, PU bagging library vs bespoke, XGBoost hyperparameters.
Settled (do not reopen): negatives schema and asymmetric use, tier system, Zn-Pb MVT as
first system for external validation, blocked-CV-only reporting, dual-regime backtest,
calibrated outputs.
