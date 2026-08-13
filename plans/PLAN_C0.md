# PLAN C0 — Foundation: Lake Repair, Hex Fabric, Feature Store, Tenure Events

**Read `MASTER_PLAN.md` §2–§4 first.** C0 is the prerequisite for every other component.
Nothing in C1–C6 starts until Gate G0 passes.

**Estimated effort:** 2–2.5 weeks (was 1.5–2; +0.5 day 0.1 rework, +1 day 0.9 MLAS spike).
**New modules:** `src/fabric.py`, `src/gridify.py`, `src/rasters.py`, `src/tenure_events.py`,
`src/verify_harvest.py`.
**Modified modules:** `process.py`, `harvest.py` (two defects — see 0.1), `build_index.py`,
`requirements.txt`, `sources.py`.
**Also in Phase 0:** C3.1 (daily tenure scheduler) and C3.6 (alerting), pulled forward — see
Master §7. The archive clock cannot be caught up later.

> **Read `AUDIT_FINDINGS.md` before starting.** Sections 0.1, 0.2, 0.3, 0.7 and 0.8 below
> carry corrections from the 2026-08-13 verification pass; the original text was wrong about
> which connectors failed and about what history exists on disk.

**Dependency availability confirmed** for Python 3.12 (0.4): h3 4.5.0 (**v4 API differs
materially from v3**), rasterio 1.5.1, rioxarray 0.23.0, exactextract 0.3.0, h3ronpy 0.22.0,
pystac-client 0.9.0.

---

## 0.1 Repair the broken/never-run harvests (~1.5 days)

> **Corrected 2026-08-13 (audit B1–B3, C1).** The original diagnosis was wrong in a way that
> matters: `es_scroll.py` did **not** "produce nothing" — the ledger records it fetching
> 228 MB of OAFD JSONL and 19 MB of AMIS — and the `direct` connector did **not** "never
> fetch" the bedrock zip; it fetched all 133,479,067 bytes. **Eight Ontario payloads
> totalling ~1.3 GB were downloaded on 2026-06-13 and are now absent from disk**, sidecars
> and ledger rows intact. So the connectors are not the primary problem; persistence is.
> Do not spend time debugging Elasticsearch pagination.

**First — fix the defect that can destroy a snapshot (do this before any re-harvest).**

`harvest.py:128-131` deletes the file in the *current* snapshot directory when a re-run's
sha matches the previous ledger row, then `continue`s without touching that row:

```python
if prev and prev[0] == sha and not args.force:
    dest.unlink(missing_ok=True)   # same path the existing manifest row references
    continue                       # → row now points at nothing
```

A same-day re-run therefore orphans the row and destroys the payload. A manual-cleanup
explanation was tested and refuted: a 1,607 MB `BC_MTA_GRID` and two 610 MB CGMC files from
that same date survived while a 6.3 MB Ontario file did not, and all eight losses are
Ontario-only — consistent with `harvest.py --jurisdiction ON`. Fix: skip *without deleting*
when the destination is inside the snapshot the ledger already references, or write to a
`.part` file and only promote on success.

**Second — re-point Ontario at public ArcGIS REST instead of the Azure-blob/Elasticsearch
paths.** This is cheaper, keyless and uses code that already exists (`arcgis.py:5` documents
FeatureServer/**MapServer** paging with `f=geojson`; live request verified; both layers
report `maxRecordCount` 2000 with `supportsPagination: true`, matching `C.ARCGIS_PAGE`).
Adding them is a `sources.py` dict entry — **no new code**.

Base: `https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/GeologyOntario/GeologyOntario_Map/MapServer`

| Layer | id | Records | Replaces / supplies |
|---|---|---|---|
| OMEIS Drill Hole | 47 | 172,259 | exploration drillholes; **see note** |
| OMEIS Technical File Area | 50 | 62,436 | the OAFD/AFRI index **with spatial footprints** — what C3.4 needs, no API key |
| OMEIS Mineral Inventory | 46 | — | ON_OMI |
| AMIS Site / Feature | 48 / 49 | — | ON_AMIS |
| Bedrock Geology, Faultlines, Iron Formation, Dikes, Quaternary, Precambrian | 57, 54, 55, 56, 52/53, 58 | — | MRD126 content for C2.1 |

> **Layer 47 is not ODHD.** `ON_ODHD` is the *Ontario Borehole Database* (`files.ontario.ca`),
> which includes water wells and geotechnical holes. OMEIS is the exploration/assessment
> drillhole layer, and for C2.4 it is strictly better: `ELEMENTS` populates
> `commodities_tested[]` (C2.4 assumed nulls) and `HOLE_TYPE` (`"Diamond Drill Hole"`, code
> `DD`) lets non-exploration holes be excluded before deriving barren negatives. Harvest
> both if the borehole database is wanted for overburden/depth-to-bedrock work.

**Remaining items:**

| Item | Failure mode (verified) | Fix |
|---|---|---|
| 8 lost ON payloads | Downloaded 2026-06-13, absent from disk; ledger + sidecars intact | Re-harvest after the `harvest.py` fix. Prefer the ArcGIS route above for OMI/AMIS/OAFD/bedrock; keep the Azure blob URL only for `ON_GEOL_SURFICIAL` (MRD128) and `ON_ODHD` |
| `ON_GEOL_BEDROCK`, **`ON_GEOL_SURFICIAL`** | Both hold a 49,932-byte HTML interstitial from the 2026-06-12 CKAN pull. **SURFICIAL was missing from the original list** | Covered by the ArcGIS route (bedrock) and re-harvest (surficial) |
| `ON_GEOPHYS`, `ON_AMIS` | KML previews only (3–58 KB) | ArcGIS route / C3.2 |
| `FED_CDoGS` | Never harvested (1,300+ regional geochemical surveys) | Standard CKAN pull via existing `ckan` connector; registry entry exists |
| `FED_NATIONAL_TENURE`, `FED_MINERAL_DEPOSITS` | Never harvested | Same — CKAN re-runs |
| `FED_GEOPHYSICS` | **38** files at exactly 6,004 bytes are HTML error pages (the 39th is `_source.json` at 501 bytes) | Delete the snapshot directory; mark the source `blocked: browser_automation` in `sources.py`; real acquisition is C3.2 |
| `ogsearth` ledger rows | 1,016 of 1,541 rows lack the `.kmz` the files carry; **no data lost** | Reconcile the ledger; fix the precedence bug at `harvest.py:89` — `if fmt and not raw.suffix or (...)` binds as `(fmt and not raw.suffix) or (...)`, and coordinate names like `-95_53_-94.5_53.5` yield a bogus `Path.suffix` of `.5`. The bug is real; it is **not** proven to be what produced these rows, so reconcile from disk rather than by recomputation |
| `SK__SK_SMDI` | 140 rows — implausibly low for the SK Mineral Deposit Index | Investigate; not yet diagnosed |

**Acceptance:** each repaired source has a fresh dated snapshot whose files pass a
format-sniff check (ZIP magic bytes / shapefile sidecars present / row counts > 0), and a
new automated `verify_harvest.py` check rejects any download whose content-type or first
bytes look like HTML when the registry says binary — **and records the sniffed container
type rather than trusting the registry `format` field** (see 0.2; this is what let the QC
ZIPs be stored as `.gpkg`). A ledger-vs-disk reconciliation reports zero orphaned rows.
This check runs after every future harvest — the geophysics failure must be impossible to
silently repeat.

## 0.2 Lift trapped vector geometry into the spatial store (~1.5 days)

> **Scope widened 2026-08-13 (audit A3, gap register #12/#14).** This is not a Québec-only
> task. A lake-wide scan found **46 files whose extension claims a geo format but whose
> content is a ZIP** — QC 37, NB 6, NS 2, BC 1 — totalling ~7.7 GB of unreadable geology.
> **The BC one is `Bedrock Geology 2018 Shapefile.shp`, and Phase 1 depends on it** (C2.1
> corpus text, C1.5 trend-from-structures, C2.7 geology categoricals). Only 1 of BC's 5 raw
> datasets (`BC_MTA_CURRENT`) ever reached `geo.gpkg`.
>
> Note `.xlsx` files also test as ZIP — that is normal OOXML, not a defect. The 46 above
> exclude them.

All 13 SIGÉOM packages sit in `raw/QC/` (including a 543 MiB bedrock GPKG and a 954 MiB
geochem GPKG) but `process.py` only lifted CSVs to Parquet; `geo.gpkg` contains zero QC
layers — and no BC bedrock, no BC MINFILE spatial, no MTA grid.

> **Corrected 2026-08-13 (audit A3).** The QC files are **ZIP archives misnamed
> `.gpkg`/`.shp`/`.fgdb`** — all 38 of them (`file` → `Zip archive data`; the inner
> `sigeom.gpkg` is a genuine `SQLite format 3`). `pyogrio.list_layers` on them **fails
> outright** ("not recognized as being in a supported file format"), and `/vsizip/` fails
> too, because GDAL dispatches on extension. Verified working on the 58 correctly-named
> `.zip` files elsewhere in the lake, so this is naming, not corruption.

- Fix the container handling in `process.py:expand()` **first**. It already unzips — but
  selects with `work.rglob("*.zip")`, an extension glob that misses these files. Switch to
  content sniffing (`zipfile.is_zipfile`) over all candidates. That one change unblocks QC;
  no mass rename of the raw snapshots is needed, and none should be done (they are
  immutable). Upstream, `harvest.py:87-90` names files from the CKAN `format` field rather
  than the response — that is the root cause, fixed in 0.1's `verify_harvest.py`.
- Then extend `process.py` to enumerate layers inside the extracted container formats
  (`pyogrio.list_layers` over the unpacked `.gpkg`/`.fgdb`/`.shp` trees) and copy each into
  `geo.gpkg` as `QC__SIGEOM__<layer>`.
- Where geometry is absent but tables carry `Coord_X/Coord_Y` (or `ESTN/NORD`), synthesize
  point layers with the correct source CRS (record the EPSG used per table in the layer
  metadata — do not guess silently; the sidecar or table docs state it).
- **Acceptance:** `geo.gpkg` contains QC bedrock polygons and QC drillhole collars
  (187,321 rows) **and BC bedrock geology polygons** (the Phase-1 dependency);
  all 46 misnamed containers either load or are individually explained; `build_index.py`
  re-run picks up the new layers; a sample spatial query (drillholes within a bedrock
  polygon) returns sane results.

**Also in 0.1/0.2 scope — a discovery task, not a fix (gap #13):** determine whether a bulk
**BC drillhole** dataset exists (BCGS / MTO / MINFILE-linked), or whether BC drill data only
exists inside ARIS assessment reports. BC is the Phase-1 jurisdiction and currently has
**no drillhole source registered at all**, which leaves C2.4 negatives and the C4 dossier
Drilling section empty for BC targets. The answer determines whether C5.2 intercept
extraction must be pulled forward into Phase 1. Time-box to half a day; record the outcome
in `AUDIT_FINDINGS.md`.

## 0.3 CGMC de-duplication and raster registration (~0.5 day)

The 610 MB CGMC GeoTIFF exists in **three** byte-identical copies (sha256 `45b24a59…`), not
two: one under `2026-06-12/` and an EN/FR pair under `2026-06-13/`. Only the intra-snapshot
pair is safely removable — deleting across snapshot dates would breach the immutable-dated-
snapshot contract in Master §4. So the instruction below is unchanged and reclaims 610 MB;
the third copy is a legitimate earlier snapshot and stays.

Delete the FR copy (keep a note in the sidecar), convert the survivor to a
Cloud-Optimized GeoTIFF under `processed/rasters/`, and register it (see 0.4). The CGMC legend GPKG (unit-description
text) is the primary corpus for C2.1 — verify it loads and its description fields are
non-empty.

## 0.4 Raster pipeline (~1 day)

`requirements.txt` currently has no raster stack. Add: `rasterio`, `rioxarray`, `xarray`,
`zarr`, `h3`, `h3ronpy`, `exactextract` (or `rasterstats` as fallback), `pyogrio`.

New module `src/rasters.py`:
- `ingest(path, code) -> COG` — validate CRS, convert to COG with overviews, write to
  `processed/rasters/<JURIS>__<CODE>__<name>.tif`, append an entry to
  `processed/rasters/rasters.json` (path, CRS, resolution, nodata, source snapshot, hash).
- `zonal(cog, cells, stats=["mean","min","max","std"])` — exact-extract zonal statistics
  against fabric cell geometries, returning long-format rows for the feature store.

**Acceptance:** CGMC GeoTIFF ingested; `zonal()` against 1,000 sample r7 cells completes
and the values spot-check against QGIS.

## 0.5 Hex fabric (~2–3 days)

New module `src/fabric.py`. Two resolutions, chosen deliberately:
- **r7 (avg cell ≈5.2 km²)** — regional modelling fabric. Canada-wide this yields on the
  order of ~2 M land cells, matching the scale of published national prospectivity work
  (~1.8 M cells). This is the resolution C2 trains on and C1 computes heat on.
- **r9 (avg cell ≈0.10 km²)** — claim-scale fabric, comparable to a single Ontario claim
  cell (~21 ha ≈ 0.21 km²). Built **lazily per AOI** (a bounding geometry passed in), never
  nationally — the national r9 set would be ~150 M cells for no benefit.

Functions:
- `build_r7(landmass_geom) -> parquet` — polyfill Canada landmass (use the national
  boundary from the federal layers; clip Great Lakes), columns:
  `cell_id (h3 string), geometry (wkb), province, area_km2, terrane_id (join from bedrock
  geological-province polygons — used by C2.5 leave-one-terrane-out CV)`.
- `build_r9(aoi_geom, aoi_id) -> parquet` under `processed/fabric/r9/<aoi_id>.parquet`,
  plus `parents` column linking each r9 cell to its r7 ancestor.
- `fabric_version` — a hash of (resolution set, landmass geometry hash, build date),
  stamped into every downstream feature matrix.

New module `src/gridify.py` — the lake→feature-store bridge:
- `gridify_vector(layer, cells, mode)` where mode ∈ {`area_weighted_category`
  (dominant + fraction per category), `count`, `presence`, `text_concat` (concatenate text
  fields of intersecting polygons with area-share tokens — required by C2.1)}.
- `gridify_points(table, cells, agg)` — count / mean / max / percentile per cell
  (drillholes, occurrences, geochem samples).
- `gridify_raster(cog, cells)` — wraps `rasters.zonal`.
- `dist_to(feature_layer, cells)` — distance from cell centroid to nearest feature
  (faults, contacts, occurrences); implement with a spatial index, not brute force.
- All emit long-format rows `(cell_id, feature, value, snapshot, source_hash)` appended to
  `features/<fabric_ver>/<snapshot>/features.parquet`, with `manifest.json` recording every
  input layer's hash. Wide pivots are produced on demand by consumers, never stored.

**Acceptance:** r7 fabric built with terrane join populated; a demonstration matrix over
Ontario containing ≥1 vector-category feature, ≥1 distance feature, ≥1 point-count
feature, and ≥1 raster zonal feature; re-running gridify with identical inputs is
byte-identical (determinism check).

## 0.6 Feature-store versioning discipline (~0.5 day)

- `features/<fabric_ver>/<snapshot>/manifest.json` schema: fabric version, snapshot date,
  list of `(source_code, layer, sha256, harvest_date)` inputs, gridify code git hash.
- A `features/latest` symlink is forbidden — consumers must name their snapshot. C2 model
  cards and C4 dossiers record it (Master §4 versioning rule).

## 0.7 Tenure event stream (~2 days)

New module `src/tenure_events.py`. This becomes the primary business signal (C1 heat and
lapse watch), so it is built here, hardened, not as a C1 afterthought.

- Input: consecutive dated raw snapshots of every tenure source (ON OGSEarth claim tiles,
  BC MTA WFS, YT quartz/placer, NU claims/leases/permits, SK, NS, NB, FED national tenure
  once repaired).
- Diff per source on the native claim identifier where one exists; where the source lacks
  a stable ID (some KMZ tiles), fall back to geometry-hash matching and flag
  `id_confidence`.
- Output schema (`processed/tenure_events.parquet`):
  `event_id, juris, source_code, claim_id, event_type ∈ {staked, expired, transferred,
  extended, converted, geometry_change}, event_window_start, event_window_end (the two
  snapshot dates bounding the change — daily snapshots give ±1 day precision), owner_before,
  owner_after (nullable — availability varies by source), geometry, area_ha,
  snapshot_prev, snapshot_next, id_confidence`.
> **Corrected 2026-08-13 (audit A1/A2). There is nothing to backfill.** The lake holds
> **two snapshot dates**, 2026-06-12 and 2026-06-13 — verified from disk (107 and 14
> snapshot directories), not just the ledger. Only `ON_CLAIMS2` and `ON_ALIENATIONS` have
> both; every other tenure source has one. "Historical diffs are free signal nobody else
> has" is false: the available diff is one day, two Ontario layers.

- **Do not plan a backfill.** Build the diff engine against the one available consecutive
  pair to prove correctness, then let C3.1 (now Phase 0) accumulate real history forward.
- **Partial substitute, with a caveat that must travel with it.** BC and YT publish dates as
  attributes: BC `ISSUE_DATE`/`GOOD_TO_DATE`/`TERMINATION_DATE`, YT `STAKING_DATE`/
  `RECORDED_DATE`/`EXPIRY_DATE` (int64 epoch-**milliseconds** — parse with `unit="ms"`;
  naive string parsing silently collapses everything to 1970). These reconstruct a partial
  staking series without snapshots. **But both layers are current-registry only** — BC has
  `TERMINATION_DATE` on 23 of 42,285 rows; YT retains 2,603 expired against 164,985 active —
  so dropped ground is invisible and older years are progressively under-counted. Emit any
  such series with an explicit `survivorship_biased = true` column. It is descriptive
  context, never a training label or a backtest ground truth.
- **Acceptance:** the diff engine reproduces hand-checked changes across the 06-12→06-13 pair
  for `ON_CLAIMS2` and `ON_ALIENATIONS` (20 randomly sampled events verified against the raw
  snapshots); full-file re-issues are handled by hash comparison before diffing; and once
  C3.1 has run ≥7 days, events populate for ≥2 jurisdictions from genuinely consecutive
  daily snapshots. The old "backfill ≥2 jurisdictions" criterion is withdrawn as
  unsatisfiable.

## 0.8 Housekeeping (~0.5 day)

- ~~Move the repo~~ **Done 2026-08-12.** The repo is at `~/projects/canada-geo-data-lake`
  (note: `canada-geo-**data**-lake`, not `canada-geo-lake`, which is now only a compat
  symlink shim to the split data roots). Git history intact; `MEMORY.md` entry added.
- Rewrite `COVERAGE.md` to the verified on-disk truth. It currently marks `ON_ODHD`,
  `ON_OAFD`, `ON_GEOL_BEDROCK`/`_SURFICIAL`, `ON_GEOPHYS`/`ON_GEOCHEM` and `CDoGS` as
  **READY** when every one of them is empty or failed on disk.
- ~~Update `RAG_PLAN.md`~~ **Done 2026-08-12** (superseded-notice added). Recorded values:
  chat `:8082` = `qwen3.6-35b`, embeddings `:8083` = `mxbai-embed-large-v1.Q4_K_M`,
  **dimension 1,024**, rag-proxy `:9100`. Note the existing Chroma collection `geo_canada`
  is 768-dim (Ollama nomic) and holds 4 rows — drop it rather than migrate; ensure every new
  collection records its embedding model and dimension in collection metadata.

## 0.9 MLAS ownership spike (~1 day, time-boxed) — *added 2026-08-13*

Ontario publishes **no owner and no expiry** through OGSEarth — verified at the raw KMZ
level (a 217 KB tile with 3,388 placemarks has no `ExtendedData`, no `SchemaData`, and no
owner/holder/expiry text anywhere). Only `Claim Number`, `Cell Claim Type`, `Claim Status`.
This blocks C1.4, C1.5, C1.6 and C6.2 for Ontario. The information is expected to be public
— it is served through the MLAS claim-abstract system — but not through any open endpoint
found so far: the LIO ArcGIS `MLAS` folder returns **403 Forbidden**, and
`mlas.mndm.gov.on.ca` is an AngularJS 1.x SPA whose `app.config.js` is vendor boilerplate
with no endpoint constants.

**The deliverable is a decision, not a scraper.** Open a claim abstract
(`#/search/searchClaimDetails?claimNumber=NNNNNN`) with devtools, capture the XHR the app
issues, and determine: is ownership retrievable in bulk, or only per-claim? Record endpoint,
auth requirement, rate limits, an effort estimate for a full connector, and a go/no-go, as a
memo appended to `AUDIT_FINDINGS.md`. Try plain HTTP first; fall back to
`sedi-scraper/antibot.py` only if blocked. Politeness per `C.REQUEST_GAP`.

**Stop at one day regardless of outcome.** Ontario ownership is no longer on the Phase-1
critical path — Phase 1 is BC.

## Gate G0 checklist

- [ ] 0.1 repairs verified by `verify_harvest.py`; `harvest.py` unlink defect fixed; ledger reconciled to zero orphans
- [ ] 0.2 QC layers spatially queryable (container sniffing in `process.py:expand()`)
- [ ] 0.4 raster ingest + zonal working on CGMC
- [ ] 0.5 fabric + demonstration **BC** feature matrix, deterministic
- [ ] 0.7 diff engine verified on the 06-12→06-13 pair; **C3.1 running ≥7 days**; events for ≥2 jurisdictions from consecutive daily snapshots
- [ ] 0.8 docs truthful (`COVERAGE.md` rewritten)
- [ ] 0.9 MLAS go/no-go recorded
- [ ] C3.1 + C3.6 operational (pulled into Phase 0 — see Master §7)

## Handoff notes for detailed planning

Delegated decisions (choose during build, document in code): exact H3 polyfill strategy at
coastlines (containment vs overlap), exactextract vs rasterstats, geometry-hash function
for ID-less tenure diffs, Parquet partitioning scheme for the feature store (suggest
partition by `feature` prefix). Settled decisions (do not reopen): resolutions r7/r9,
long-format feature store, two-snapshot event windows, no `latest` symlink.
