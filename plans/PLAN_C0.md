# PLAN C0 — Foundation: Lake Repair, Hex Fabric, Feature Store, Tenure Events

**Read `MASTER_PLAN.md` §2–§4 first.** C0 is the prerequisite for every other component.
Nothing in C1–C6 starts until Gate G0 passes.

**Estimated effort:** 1.5–2 weeks.
**New modules:** `src/fabric.py`, `src/gridify.py`, `src/rasters.py`, `src/tenure_events.py`.
**Modified modules:** `process.py`, `build_index.py`, `requirements.txt`, `sources.py`.

---

## 0.1 Repair the five broken/never-run harvests (~1 day)

Verified on-disk failures to fix, in order of cheapness:

| Item | Failure mode (verified) | Fix |
|---|---|---|
| `ON_GEOL_BEDROCK` | 49,932-byte HTML interstitial on disk instead of the 133 MB MRD126 shapefile; `sources.py` already carries the correct Azure blob URL but the `direct` connector never fetched it | Debug the `direct` connector's handling of that entry (likely a redirect/content-type check swallowing the blob); re-harvest; verify size ≈133 MB and layer loads in `geo.gpkg` |
| ON `es_scroll` family (ODHD 126k drillholes, OMI occurrences, lake-sediment geochem, OAFD assessment index) | Only 6.8 KB KML pointers on disk; `es_scroll.py` (126 lines) produced nothing | Instrument the connector: log the Elasticsearch scroll request/response pair; confirm index names and page size against the live endpoint; fix pagination; harvest all four. OAFD is doubly important — it is the report-ID index that C3.4/C5 need |
| `FED_CDoGS` | Never harvested (1,300+ regional geochemical surveys) | Standard CKAN pull via existing `ckan` connector; registry entry exists |
| `FED_NATIONAL_TENURE`, `FED_MINERAL_DEPOSITS` | Never harvested | Same — CKAN re-runs |
| `FED_GEOPHYSICS` | All 39 files in `raw/FED/GEOPHYSICS/2026-06-13/` are exactly 6,004 bytes — HTML error pages, not ZIPs | Delete the snapshot directory; mark the source `blocked: browser_automation` in `sources.py`; real acquisition is C3.2 |

**Acceptance:** each repaired source has a fresh dated snapshot whose files pass a
format-sniff check (ZIP magic bytes / shapefile sidecars present / row counts > 0), and a
new automated `verify_harvest.py` check rejects any download whose content-type or first
bytes look like HTML when the registry says binary. This check runs after every future
harvest — the geophysics failure must be impossible to silently repeat.

## 0.2 Lift Québec vector geometry into the spatial store (~1 day)

All 13 SIGÉOM packages sit in `raw/QC/` (including a 570 MB bedrock GPKG and a 1.0 GB
geochem GPKG) but `process.py` only lifted CSVs to Parquet; `geo.gpkg` contains zero QC
layers.

- Extend `process.py` to enumerate layers inside container formats (`pyogrio.list_layers`
  over `.gpkg`/`.fgdb`/`.shp` trees) and copy each into `geo.gpkg` as
  `QC__SIGEOM__<layer>`.
- Where geometry is absent but tables carry `Coord_X/Coord_Y` (or `ESTN/NORD`), synthesize
  point layers with the correct source CRS (record the EPSG used per table in the layer
  metadata — do not guess silently; the sidecar or table docs state it).
- **Acceptance:** `geo.gpkg` contains QC bedrock polygons and QC drillhole collars
  (187,321 rows); `build_index.py` re-run picks up the new layers; a sample spatial query
  (drillholes within a bedrock polygon) returns sane results.

## 0.3 CGMC de-duplication and raster registration (~0.5 day)

The 610 MB CGMC GeoTIFF is stored twice (EN + FR, byte-identical). Delete the FR copy
(keep a note in the sidecar), convert the survivor to a Cloud-Optimized GeoTIFF under
`processed/rasters/`, and register it (see 0.4). The CGMC legend GPKG (unit-description
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
- Backfill from every snapshot pair already on disk (`manifest.sqlite` lists 1,804 harvest
  records — historical diffs are free signal nobody else has).
- **Acceptance:** events populated for ON and ≥1 other jurisdiction; 20 randomly sampled
  events manually verified against the raw snapshots; a `staked`-events-per-month time
  series for Ontario plots without absurd spikes (spikes = diff bug or source re-issue —
  handle full-file re-issues by hash comparison before diffing).

## 0.8 Housekeeping (~0.5 day)

- Move the repo to `~/projects/canada-geo-lake` with git history intact; add the project
  to `MEMORY.md`.
- Rewrite `COVERAGE.md` to the verified on-disk truth (it currently overstates holdings —
  geophysics, ON bedrock, ON es_scroll family, FED aggregates).
- Update `RAG_PLAN.md`: Ollama is retired; embeddings are llama.cpp mxbai at `:8083`
  (verify and record the embedding dimension), chat at `:8082`, rag-proxy `:9100`.

## Gate G0 checklist

- [ ] 0.1 all five repairs verified by `verify_harvest.py`
- [ ] 0.2 QC layers spatially queryable
- [ ] 0.4 raster ingest + zonal working on CGMC
- [ ] 0.5 fabric + demonstration Ontario feature matrix, deterministic
- [ ] 0.7 tenure events backfilled and spot-checked for ≥2 jurisdictions
- [ ] 0.8 docs truthful, repo relocated

## Handoff notes for detailed planning

Delegated decisions (choose during build, document in code): exact H3 polyfill strategy at
coastlines (containment vs overlap), exactextract vs rasterstats, geometry-hash function
for ID-less tenure diffs, Parquet partitioning scheme for the feature store (suggest
partition by `feature` prefix). Settled decisions (do not reopen): resolutions r7/r9,
long-format feature store, two-snapshot event windows, no `latest` symlink.
