# PLAN C3 — Acquisition & Automation: Schedulers, Geophysics, Remote Sensing, On-Demand Reports, Market Feeds, Alerting

**Read `MASTER_PLAN.md` §2–§4 first.** C3 keeps the substrate current at decision-driven
cadence and closes the two missing evidence modalities (geophysics, remote sensing). It
also owns the connectors that feed C5 (reports) and C6 (market data).

**Depends on:** C0.1 (repaired registry discipline), C0.4 (raster pipeline for grids/EO).
**Feeds:** everything.
**Estimated effort:** 3.1 ≈ 2 days · 3.2 ≈ 1–2 days · 3.3 ≈ 2–3 days · 3.4 ≈ 3–4 days
(first two jurisdictions) · 3.5 ≈ 3 days · 3.6 ≈ 1 day · 3.7 ≈ 1 day.
**New modules:** `src/connectors/sciencebase.py`, `src/connectors/stac.py`,
`src/connectors/market/`, `src/fetch_reports.py`, `src/alerting.py`, `ops/` (timers,
health checks).

---

## 3.1 Scheduling & operational hardening (~2 days) — **MOVED TO PHASE 0**

> **Resequenced 2026-08-13 (audit D1), and it stays resequenced (audit F2).** This was the
> last thing to be built; it is now among the first. The lake holds **two snapshot dates one
> day apart**, and most tenure registries publish current holdings only — so no amount of
> later work recovers history that was not captured.
>
> Ontario turns out to be an exception: its cancellation register supplies an unbiased
> 2018-onward history, which unblocks C1.3, C2.6 and C6.3 for Phase 1 without waiting. **That
> does not relax this item.** BC, YT, NU and SK have no equivalent register and never will
> without forward capture; Ontario's own register begins at map-staking conversion in 2018-04
> and is refreshed in bulk rather than continuously, so daily snapshots still catch
> within-period churn — stake-and-drop inside a single refresh cycle, and same-day
> corrections — that the register flattens.
>
> Build 3.1 and 3.6 in Phase 0; Gate G0 requires ≥7 consecutive days of unattended daily
> tenure snapshots.

Cadence is decision-driven, never uniform:

| Class | Cadence | Rationale |
|---|---|---|
| Tenure (all jurisdictions) | daily, early AM | feeds C1 heat + lapse watch — the business-critical loop |
| CKAN / ArcGIS / WFS geoscience | weekly | slow-changing |
| Market (SEDAR+ filings, news) | daily | C6 buyer/comps freshness |
| Rasters (geophys, EO refresh) | monthly / on-demand per AOI | large, slow-changing |
| Report PDFs | on-demand only (3.4) | per-target, never bulk by default |

Implementation:
- systemd timers (preferred over cron for logging/dependency) in `ops/`, one unit per
  class, each invoking existing `harvest.py --class=...` selections.
- **Health model:** every run writes a heartbeat row (`ops/health.sqlite`: run_id, class,
  started, finished, exit, bytes, changed_sources). A watchdog timer alerts (via 3.6) on:
  missed heartbeat, non-zero exit, zero-change streak beyond expectation (tenure sources
  that change daily going quiet for 3 days = silent breakage), or any
  `verify_harvest.py` format-sniff rejection (C0.1).
- Post-harvest hooks: tenure harvest completion triggers `tenure_events` diff (C0.7) then
  C1 incremental refresh then lapse-watch evaluation — one pipeline, not three timers
  racing.

**Acceptance:** one week of unattended operation with zero silent failures; a
deliberately broken source produces an alert within one cycle.

## 3.2 National geophysics grids via browser automation (~1–2 days, high confidence)

The current on-disk "geophysics" is 39 HTML error pages (C0.1 deletes them). The Federal
Geophysical Data Repository portal requires browser automation.

- Reuse the proven Camoufox module — `/home/vis/projects/sedi-scraper/antibot.py` (1,836
  lines: `ResponseLogger`, `ShieldTracker`, `TrustMetric`, `classify_block_type`,
  `retry_strategy_for`, `exponential_backoff`, Mullvad rotation), already validated against
  harder anti-bot targets (SEDAR+/SEDI). *Location corrected 2026-08-13: it lives in
  `sedi-scraper`, not `mining-scraper` — the latter imports it via `sys.path`. Reuse from
  this repo needs the same path insert or proper packaging; budget half a day.*
  `mining-scraper/src/mining_scraper/browser/` separately offers `BrowserSessionManager` and
  `detect_shield_square`.
- Procedure: drive the portal UI headfully once with the ResponseLogger capturing the
  actual download request pattern (session token / POST handler); codify as a
  `geophysics_gdr` connector that replays with session persistence; polite pacing
  (single-threaded, multi-second jitter — this is a public-good federal service).
- Targets, in priority order: national magnetics (200 m and 1 km grids), Bouguer gravity
  (~2 km), radiometrics (~250 m: K, eTh, eU, ratios).
- Ingest through `rasters.py` → COGs → gridify derivatives onto r7: for magnetics compute
  RTP if not provided, 1VD, analytic signal, tilt; for gravity: residual + horizontal
  gradient magnitude (the ScienceBase pull in C2.2 provides reference versions to sanity-
  check the derivative code against).

**Acceptance:** grids on disk as valid COGs with sane value ranges; derivative rasters
visually match ScienceBase reference products over a common area; provenance sidecars
recorded.

## 3.3 STAC connector for remote sensing + DEM (~2–3 days)

Open APIs, no auth, no scraping. New `src/connectors/stac.py` using `pystac-client`.

- **Collections:** Sentinel-2 L2A (Element84 earth-search or Planetary Computer),
  Landsat Collection 2 L2, Copernicus DEM GLO-30. ASTER L2 where obtainable for legacy
  SWIR (note SWIR detector failure post-2008 — usable archive is pre-2008; treat as
  historical layer).
- **AOI-driven, never wall-to-wall national:** the connector takes geometries — standing
  AOIs = top-decile heat r7 cells + all watched blocks' neighborhoods (from C1) + any C4
  screen result the human pins. National EO wallpaper is explicitly out of scope.
- **Products (per AOI, into `rasters.py`):**
  - Cloud-free median composite (growing-season window per latitude) for S2.
  - Alteration/regolith indices: iron-oxide ratio (B4/B2), clay/hydroxyl proxy
    (B11/B12 family), NDVI (as a masking layer — vegetated Canadian shield limits
    spectral methods; record NDVI so C2 can weight spectral features by exposure).
  - DEM + derivatives: slope, TPI, and drainage lines (drainage supports geochem
    catchment logic in C2.3).
- Gridify all products onto r7 (and r9 within deal AOIs) via `gridify_raster`.

**Acceptance:** one Ontario greenstone AOI processed end-to-end; composites cloud-free by
inspection; indices show plausible contrast over known alteration; DEM derivatives match
provincial LiDAR spot checks where available.

## 3.4 On-demand report fetch — `fetch_reports(geometry, juris)` (~3–4 days for ON+BC)

Per-target retrieval, distinct from corpus-scale harvest (which is C5.3). Contract:

```
fetch_reports(geometry, juris, max_reports=None) ->
  [{report_id, title, year, work_types[], pdf_path, status}]
```

1. **Resolve IDs intersecting the geometry:**
   - ON: **use LIO ArcGIS layer 50, `OMEIS Technical File Area` — 62,436 records with
     spatial footprints**, fields `TECH_ID, SUBMISSION_TYPE, PERFORMED_FOR, PROPERTY,
     PRIMARY_TOWNSHIP, YEAR_FROM, YEAR_TO, COMMODITIES, WORK_TYPE, FILE_IDENTIFIERS,
     INFO_LINK`. *Corrected 2026-08-13 (audit C1): this replaces the OAFD Elasticsearch
     route entirely. It is a plain paged ArcGIS query the existing `arcgis` connector already
     handles, it carries geometry directly (no township-name resolution step), and it needs
     **no API key**. The `es_scroll` dependency in this step is withdrawn.*
   - BC: ARIS index queryable spatially (ARIS layers/API alongside MINFILE) → ARIS numbers.
   - Later jurisdictions follow `SCRAPERS_BLOCKED.md` per-system strategies (SK SMAD
     viewstate two-phase POST; NL GeoFiles devtools-captured handler; NS NovaScan JSF;
     NB PARIS detail pages; NTGS postback) — each is a 1–4 day devtools exercise already
     scoped there; do them **on demand when a deal touches that jurisdiction**, not
     preemptively.
2. **Fetch only those PDFs** into `pdfs/<JURIS>/<CODE>/<id>.pdf` with sidecars + manifest
   entries; Camoufox session manager where the system requires it; strict politeness.
3. **Hand off to C5.1 ingestion** (extract → chunk → embed → Chroma collection per target).

**Acceptance:** for one Ontario target polygon, the resolver returns the same report list
a human finds via the province's own viewer; PDFs on disk are valid (format sniff); C5
ingestion runs green.

## 3.5 Market-data connectors (~3 days)

New registry class `market` (parallel to geo sources) under `src/connectors/market/`.

- **SEDAR+ (reuse `mining-scraper`):** scheduled pulls per tracked issuer (list supplied
  by C6.2): filings index, material change reports, financing documents (prospectuses,
  offering docs), MD&A. Store raw docs under `market/raw/sedar/<issuer>/<date>/`;
  extraction into structured tables is C6's job — C3 only acquires and ledgers.
- **News:** RSS/wire monitoring for tracked issuers + belt keywords; store raw items
  `market/raw/news/`. Keep the acquisition dumb; C6 does entity/deal extraction.
- **Issuer registry:** `market/issuers.parquet` (issuer_id, names/aliases, tracked_since,
  why_tracked ∈ {adjacent_owner, comps_party, watchlist}) — written by C6.2, consumed
  here to scope pulls. Do not crawl the entire exchange.

**Acceptance:** for 3 seed issuers, daily pulls capture a known recent filing within one
cycle; ledgered in manifest with provenance.

## 3.6 Alerting (~1 day)

`src/alerting.py`: single choke point, ntfy topic + email fallback. Event types:
harvest-health (3.1), lapse-watch (C1.6), heat-threshold crossings (C1.3), new filing by
tracked issuer (3.5), Tier-2 negative confirmed on a watched cell (C5.2). Every alert
carries a deep link (C4 viewer route or dossier path). Per-type rate limits and a daily
digest mode — the operating posture is "quietly watch, rarely act"; alert fatigue is a
system failure.

## 3.7 Registry hygiene (~1 day)

- Add connector types `sciencebase`, `stac`, `market`, `geophysics_gdr` to `sources.py`
  following the existing declarative pattern (adding a source = adding a dict entry).
- Keep discover-never-hardcode: every connector re-resolves current URLs each run.
- `SCRAPERS_BLOCKED.md` becomes a living document: each system's status
  (blocked / captured / codified) updated as 3.4 lands jurisdictions.

## Handoff notes for detailed planning

Delegated: ntfy vs alternative transport, systemd unit layout, S2 provider choice
(earth-search vs Planetary Computer — pick by rate limits at build time), exact index
band math per sensor. Settled (do not reopen): decision-driven cadence table, AOI-driven
EO (no national wallpaper), on-demand-only PDF default, market acquisition kept dumb
(structure extraction lives in C6), single alerting choke point.
