# AUDIT FINDINGS — verification of the C0–C6 plans against actual system state

**Audited:** 2026-08-13 · **Auditor:** Claude Code session · **Subject:** `MASTER_PLAN.md`, `PLAN_C0.md`–`PLAN_C6.md`

The plans were authored without access to this machine. This document records what was verified, with the command and output for each claim, so a reader can re-derive rather than trust. Every finding below was challenged a second time before being recorded; findings that did not survive challenge are marked **DOWNGRADED** or **REFUTED** and kept, not deleted.

Paths assume `src/` as CWD with `../.venv/bin/python`; `L=/media/vis/Expansion/canada-geo-lake`.

---

## 0. What the plans got right

Worth stating first, because it calibrates trust in everything else. Every row count and line count checked was **exact**:

| Claim | Source | Verified |
|---|---|---|
| ON claims 202,407 | C1.1 / MASTER §7 | 202,407 |
| ON alienations 3,480 · dispositions 24,440 · non-mining 231,389 · plans/permits 4,571 | C1.1 | all exact |
| BC MTA tenures 42,285 | C1.1 | 42,285 |
| NU claims 34,411 · leases 893 · permits 2,600 | C1.1 | all exact |
| NB claims 86 · MPS 27,555 · drillholes 17,887 | C1.1 / C2.4 | all exact |
| MRDS 304,632 records | C2.1 | 304,632 (46 cols) |
| BC RGS2020 65,008 samples, 193 columns | C2.3 | 65,008 / 193 |
| QC SIGÉOM 13 packages | C0.2 | exactly 13 |
| `serve.py` 327 lines · `pdf_extract.py` 95 | MASTER §6 / C5.1 | exact |
| `sources.py` 592 · `es_scroll.py` 126 | MASTER §6 / C0.1 | 592 / 126 before this session's credential edits (+1 each) |
| manifest 1,804 harvest records | C0.7 | 1,804 |
| geo.gpkg 77 layers · 40 parquet tables | C0 / guide.py | 77 / 40 |

The errors are concentrated in **interpretation of failures** and **assumptions about data that does not exist**.

---

## A. Blocking findings

### A1 — There is no historical archive to back-fill from

C0.7 states: *"Backfill from every snapshot pair already on disk (`manifest.sqlite` lists 1,804 harvest records — historical diffs are free signal nobody else has)."*

```bash
find "$L/raw" -mindepth 3 -maxdepth 3 -type d -printf '%f\n' | sort | uniq -c
#     107 2026-06-12
#      14 2026-06-13
```

Verified from **disk directories**, not only the ledger, so it is not a manifest artifact. Per-source:

```
ON_CLAIMS2      2 dates: ['2026-06-12','2026-06-13']
ON_ALIENATIONS  2 dates: ['2026-06-12','2026-06-13']
ON_DISPOSITIONS 1 date · ON_PLANS_PERMITS 1 · BC_MTA_CURRENT 1
NU_MINERAL_CLAIMS 1 · YT_QUARTZ_CLAIMS 1 · YT_PLACER_CLAIMS 1 · NB_MINERAL_CLAIMS 1
```

**Consequence.** The lake holds two snapshot dates one day apart, 61 days stale. Backfill yields ~1 day of diffs on two Ontario layers. The following are unachievable as written:

- **Gate G0** — "`tenure_events` populated for ≥2 jurisdictions with a spot-checked diff". Only one jurisdiction has any pair.
- **C1.3 heat** — requires a trailing 8-quarter history per cell.
- **C2.6 staking backtest** — requires year Y features → year Y+1 staking labels.
- **C6.3 momentum** — "applied retroactively over backfilled events".
- **Gate G3** — depends on C2.6.

### A2 — Ontario carries no owner or dates; BC/YT carry attributes but only for surviving tenures

> ⚠️ **SUPERSEDED IN PART BY FINDING F — read that before acting on this.** The Ontario half
> of A2 is true of the OGSEarth KMZ and **false of Ontario**: the MLAS bulk shapefiles publish
> `HOLDER`, `ISSUE_DATE`, `ANNIVERSAR`, `CLAIM_DUE_` on 401,594 claims, plus 431,557 cancelled
> claims with termination dates. The BC/YT survivorship analysis below still stands. Kept
> unedited as the record of what was checked and how the wrong conclusion was reached.

```
                    Ontario ON_CLAIMS2   BC_MTA_CURRENT              YT_QUARTZ_CLAIMS
owner fields        (none)               OWNER_NAME, CLIENT_NUMBER_ID,   (none)
                                         PERCENT_OWNERSHIP, NUMBER_OF_OWNERS
date fields         (none)               ISSUE_DATE, GOOD_TO_DATE,   STAKING_DATE, RECORDED_DATE,
                                         TERMINATION_DATE            EXPIRY_DATE
```

Ontario was checked **at the raw KMZ level**, in case `process.py` had dropped attributes:

```
large tile: -80.5_48.5_-80_49.kmz (217,780 bytes) → 5,387,861 bytes KML, 3,388 placemarks
  ExtendedData: False | SchemaData: False | SimpleData fields: NONE
  'owner' False · 'holder' False · 'client' False · 'recorded' False
  'expiry' False · 'due date' False · 'anniversar' False · 'company' False
```

The processed layer exposes only `Claim Number`, `Cell Claim Type`, `Claim Status` (inside an HTML `description` blob). Ontario genuinely publishes nothing more through OGSEarth.

**Blocked on Ontario as harvested:** C1.4 ownership graph, C1.5 criticality (needs owner blocks), C1.6 lapse watch (needs expiry), C6.2 buyer graph (seeded from C1.4 owners). **C1.6's premise "ON claims carry due dates" is false.**

**DOWNGRADED — the survivorship qualification.** First pass treated BC/YT as giving usable attribute-derived history. Both are *current-registry* layers:

```
BC_MTA_CURRENT  rows=42,285   TERMINATION_DATE non-null: 23 / 42,285
YT_QUARTZ_CLAIMS rows=168,481 TENURE_STATUS: Active 164,985 · Expired 2,603 · Pending 893
```

Dropped tenures are largely purged. Yukon's dates are `int64` epoch-**milliseconds** (`pd.to_datetime(..., unit="ms")`; naive string parsing collapses them to 1970) and do reach 1902:

```
staked per year: 2020:1886 2021:2755 2022:6027 2023:5782 2024:4889 2025:3150 2026:5305
```

But these count *claims staked in year X that survive today*. Older years are progressively under-counted and the 2022→2025 decline is partly artifact. **Descriptive signal, not unbiased history — not a sound basis for the C2.6 backtest.** This strengthens A1's conclusion: forward snapshot accumulation is the only route to unbiased history.

### A3 — QC files are ZIP archives misnamed `.gpkg` / `.shp` / `.fgdb`

C0.2 proposes `pyogrio.list_layers` over `.gpkg`/`.fgdb`/`.shp` trees.

```bash
find "$L/raw/QC" -type f ! -name '_source.json' -print0 | xargs -0 file -b | sed 's/,.*//' | sort | uniq -c
#      38 Zip archive data
#      11 CSV ISO-8859 text
#       4 CSV ASCII text  ·  2 JSON  ·  2 CSV Non-ISO  ·  1 CSV Unicode
```

```
pyogrio.list_layers('.../Sondages - Jeux de données géographiques.gpkg')
→ DataSourceError: not recognized as being in a supported file format
zipfile entries: 'SIGEOM_geopackage.qgz' (1,346,932) · 'sigeom.gpkg' (39,034,880)
inner magic: b'SQLite format 3\x00'          ← genuine GPKG inside
```

GDAL dispatches on extension, so `/vsizip/` also fails on the misnamed archive — while working correctly on the 58 properly-named `.zip` files elsewhere in the lake (verified against `Mineral ClaimsClaims miniers.zip` → 1 layer).

**REFINED remedy.** `process.py:32-49` (`expand()`) **already unzips**, but selects with `work.rglob("*.zip")` — an extension glob that misses these. Content sniffing in that one function resolves it; no mass rename needed. Root cause upstream is `harvest.py:87-90`, which names files from the CKAN `format` field rather than sniffing content.

---

## B. Wrong-diagnosis findings

### B1 — ~1.3 GB of Ontario data was downloaded successfully, then lost

C0.1 asserts the `direct` connector *"never fetched"* the bedrock zip and that `es_scroll.py` *"produced nothing"*. The ledger says otherwise — eight 2026-06-13 rows with correct sizes and sha256, no file on disk:

| Code | Connector | Recorded bytes |
|---|---|---|
| ON_GEOL_SURFICIAL | direct | 503,462,794 |
| ON_GEOPHYS | direct | 245,860,577 |
| ON_OAFD | es_scroll | 227,852,810 |
| ON_GEOCHEM | direct | 137,182,550 |
| ON_GEOL_BEDROCK | direct | 133,479,067 |
| ON_ODHD | direct | 39,238,386 |
| ON_AMIS | es_scroll | 19,264,757 |
| ON_OMI | direct | 6,304,937 |

Snapshot directories exist holding only `_source.json`. Ledger-vs-disk by connector:

```
arcgis_hub 0/1 · arcgis_layer 0/13 · ckan 0/181 · direct 6/63
es_scroll 2/2 · ogsearth 1016/1541 · wfs_layer 0/3      (missing/total)
```

So **`es_scroll` worked** (228 MB of OAFD JSONL, 19 MB AMIS) and **`direct` fetched the full 133 MB bedrock zip**. C0.1's stated causes are false; the repair work is still needed.

**Mechanism.** `harvest.py:128-131`:

```python
if prev and prev[0] == sha and not args.force:
    dest.unlink(missing_ok=True)      # deletes the file in the CURRENT snapshot dir
    skipped += 1
    continue                          # ...without touching the manifest row → orphan
```

On a same-day re-run `dest` equals the path the existing row references, so the row is orphaned.

**STRENGTHENED — the competing explanation is refuted.** First pass allowed "someone manually deleted large files". Sizes at 2026-06-13:

```
survived: 568 rows — largest: 1,607.3MB BC_MTA_GRID · 610.1MB CGMC · 610.1MB CGMC
missing :   8 rows — smallest: 6.3MB ON_OMI
max surviving 1,607.3MB  ≫  min missing 6.3MB
```

A size-based cleanup would have taken the 1.6 GB file first. All eight losses are Ontario and only Ontario — consistent with `harvest.py --jurisdiction ON` re-run the same day. **Not proof:** one small `ON_AMIS` CKAN file from that date survived and is unexplained, and `logs/` is empty. Strong fit, not certainty. **The defect is real either way and must be fixed before any re-harvest.**

### B2 — The ledger's `ogsearth` paths are systematically missing `.kmz`

```
ogsearth rows=1541  missing=1016  of which exist at local_path+'.kmz' = 1016
genuinely absent after .kmz fix: 0
```

**No data is lost.** (This is the residue of the 1,024 flagged during the drive migration: 1,016 path mismatch + the 8 real losses in B1.)

**REFRAMED — mechanism not settled.** First pass called this "a cosmetic manifest bug, one-line fix". There *is* a genuine defect at `harvest.py:89`:

```python
if fmt and not raw.suffix or (raw.suffix and "_" in raw.suffix):
#  parses as (fmt and not raw.suffix) or (raw.suffix and "_" in raw.suffix)
```

Tile names are coordinates, so `Path.suffix` returns nonsense and the extension decision is made by accident:

```
'-95_53_-94.5_53.5'  → suffix '.5'        → no append   → ledger lacks .kmz
'-93.5_53.5_-93_54'  → suffix '.5_-93_54' → appends .kmz → ledger STILL lacks .kmz  ← inconsistent
```

The second case contradicts a single-cause story, and `process.py:expand()` renames on a `shutil.copytree` **temp copy**, so it is not the mutator. **Treat as: fix the precedence bug, reconcile the ledger, do not assert a cause.**

### B3 — C0.1 undercounts broken harvests

`ON_GEOL_SURFICIAL` shows the identical failure to `ON_GEOL_BEDROCK` and is not listed:

```
ON_GEOL_SURFICIAL/2026-06-12/Surficial Geology of Southern Ontario.zip
  49,932 bytes — HTML document (GeologyOntario Angular shell), not a ZIP
ON_GEOL_BEDROCK/2026-06-12/1-250 000 Scale Bedrock Geology of Ontario.zip
  49,932 bytes — HTML document
```

`ON_GEOPHYS` and `ON_AMIS` hold only KML previews (3–58 KB). Geophysics is **38** HTML files at 6,004 bytes plus one `_source.json` — not "39 files all exactly 6,004 bytes":

```
find "$L/raw/FED/GEOPHYSICS" -type f -printf '%s\n' | sort | uniq -c
#      1 501       ← _source.json
#     38 6004      ← HTML error pages ("Geophysical Data / Données géophysiques")
```

### B4 — CGMC exists in three byte-identical copies

```
sha256 45b24a59…  610,108,628 bytes ×3
  2026-06-12/2024_CGMC_Lithology_EPSG3978.tif
  2026-06-13/Canada Geological Map Compilation - Dataset download - English.geotif
  2026-06-13/Canada Geological Map Compilation - Dataset download - French.geotif
```

**DOWNGRADED.** First pass proposed keeping one copy and reclaiming 1.22 GB. That would delete across snapshot dates, breaching the immutable-dated-snapshot contract in MASTER §4. Only the intra-snapshot 06-13 EN/FR pair is safely removable. **C0.3's stated remedy ("delete the FR copy") is correct as written and reclaims 610 MB.** Record the count; leave the remedy alone.

---

## C. Opportunity — a cheaper path the plans do not consider

### C1 — Ontario's failed datasets are on public ArcGIS REST, reachable with the existing connector

`https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/GeologyOntario/GeologyOntario_Map/MapServer`

```
id=46 OMEIS Mineral Inventory   id=47 OMEIS Drill Hole   id=50 OMEIS Technical File Area
id=48 AMIS Site                 id=49 AMIS Feature       id=57 Bedrock Geology
id=54 Faultlines                id=55 Iron Formation Lines  id=56 Dikes Lines
id=52/53 Quaternary             id=58 Precambrian
```

**Compatibility verified, not assumed.** `connectors/arcgis.py:5` already documents *"page a FeatureServer/**MapServer** layer .../query with f=geojson"*, and a live request returns valid GeoJSON:

```json
{"type":"FeatureCollection","features":[{"type":"Feature","id":1,
 "geometry":{"type":"Point","coordinates":[-90.0866…,51.4898…]},
 "properties":{"HOLE_IDENT":105744,"TECH_ID":"52O09SE0024",
 "HOLE_TYPE_CODE":"DD","HOLE_TYPE":"Diamond Drill Hole",…}}]}
```

Both layers report `maxRecordCount: 2000`, `supportsPagination: true` — matching `C.ARCGIS_PAGE = 2000`. **Adding these is a `sources.py` dict entry; no new code**, exactly as the plans' own doctrine requires.

**Layer 47 `OMEIS Drill Hole` — 172,259 records.** Fields: `HOLE_IDENT, TECH_ID, HOLE_TYPE_CODE, HOLE_TYPE, COMPANY_HOLE_IDENT, COMPANY_NAME, PROPERTY_NAME, YEAR_DRILLED, LENGTH, LENGTH_UNIT, AZIMUTH, DIP, OVERBURDEN, ELEMENTS`.

> **This is not ODHD.** `ON_ODHD` in the registry is the *Ontario Borehole Database* (`files.ontario.ca/opendata/ontario_borehole_database.zip`), which includes water wells and geotechnical holes. OMEIS is the exploration/assessment drillhole layer. For C2.4 it is the better source on two counts: `ELEMENTS` populates `commodities_tested[]` — which C2.4 assumed would be null — and `HOLE_TYPE` lets non-exploration holes be excluded before deriving barren negatives, a doctrine-level correctness issue the plans miss. Treat as **substitution plus enhancement**, not a repair of ODHD; both may be wanted.

**Layer 50 `OMEIS Technical File Area` — 62,436 records** with spatial footprints. Fields: `TECH_ID, SUBMISSION_TYPE, PERFORMED_FOR, PROPERTY, PRIMARY_TOWNSHIP, RGP_DISTRICT, YEAR_FROM, YEAR_TO, COMMODITIES, FILE_IDENTIFIERS, VALUE_WORK, WORK_TYPE, WORK_TYPE_GROUP, INFO_LINK`. This is the assessment-file index C3.4 needs — with geometry, and requiring **no Elasticsearch scroll and no API key**.

### C2 — MLAS ownership is not public via ArcGIS

```
GET /arcgis1071a/rest/services/MLAS?f=pjson → 403 Forbidden (Microsoft-Azure-Application-Gateway/v2)
GET mlas.mndm.gov.on.ca/mlas/search/searchIndex.html → 200, AngularJS 1.x SPA (bower, jQuery 2.1.1)
GET /mlas/views/js/app.config.js → 200, vendor boilerplate ("SmartAdmin"), no endpoint constants
```

Claim abstracts are at `#/search/searchClaimDetails?claimNumber=NNNNNN`. **Superseded by finding F below** — this route is unnecessary; the bulk shapefiles carry `HOLDER` openly.

---

## D. Sequencing and contract findings

**D1 — Start the tenure archive clock in Phase 0.** MASTER §8 calls the point-in-time archive the defensible edge ("a competitor would need years of daily snapshots to reconstruct"), but no C3 work appears in Phase 1. Given A1 *and* A2's survivorship bias, forward accumulation is the only route to unbiased history. Move C3.1 into Phase 0.

**D2 — C1.6 needs alerting that only Phase 4 mentions.** `grep -n "alerting\|C3\.6\|C3\.1" MASTER_PLAN.md` returns the §5 component table and one Phase-4 line ("alerting maturity"). C3.6 is never explicitly scheduled, yet C1.6 sits in Phase 1 and its acceptance requires two weeks of live alerts. Schedule C3.6 (≈1 day) alongside C1.6.

**D3 — Long format is a poor fit for dense embeddings. DOWNGRADED.** MASTER §4 mandates long format and forbids stored wide pivots; C2.1 adds 1,024 dims/cell.

```
r7 cells over Canada landmass : 1,762,308   (plan's "~2 M" is sound)
LONG rows for embeddings alone: 1,804,602,940
  ≈7 GB compressed · WIDE float32 equivalent 7.2 GB
```

First pass called the pivot infeasible on 23 GB RAM. On challenge that overstates: the wide form fits, chunked pivots via DuckDB are workable, and the 23 GB figure is soft (the LLM holds ~30 GB and can be unloaded). Recommend a **narrow §4 exception** — dense embeddings as a wide float32 array artifact, long format retained for sparse scalar features — on ergonomic grounds, not impossibility.

**D4 — The embedding server hard-fails on long documents.**

```
:8083 = mxbai-embed-large-v1.Q4_K_M · dim 1024 (measured)
accepts ≤ ~2,687 chars (~671 tokens) · HTTP 500 beyond ~2,781   (model context = 512 tokens)
throughput: 289 docs/s on short batches → ~1.7 h for 1.76 M cell documents
```

C2.1's `text_concat` cell documents will routinely exceed this. C2.1 lists chunking as a delegated nicety; it is a **hard requirement**. Check server config first — the limit may be partly raisable.

**D5 — Smaller corrections.**

| Item | Plan says | Actual |
|---|---|---|
| Antibot module | "the SEDAR+/SEDI module from `mining-scraper`" | `/home/vis/projects/sedi-scraper/antibot.py`, 1,836 lines (`ResponseLogger`, `ShieldTracker`, `TrustMetric`, `exponential_backoff`); mining-scraper imports it via `sys.path`. Real and reusable — reuse needs a path insert or packaging, unbudgeted |
| ON AFRI count | "100k+" (C5, twice) | 62,357 (`sources.py`, `scrape.py`) / 62,436 (ArcGIS layer 50). Reconcile — possibly documents vs files |
| `serve.py` tiles | "vector tiles … (existing pattern)" (C4.2) | `/layers /geojson/{layer} /search /coverage /stats /tenure` — GeoJSON only, **no tile endpoint**. Tiles are new work |
| `eis-toolkit` | "preferred" (C2.7) | **Not on PyPI** (may install from source). `uncover-ml` 0.4.0 is available |
| Raster/H3 stack | "add to requirements.txt" (C0.4) | All available for py3.12: h3 4.5.0 (**v4 API differs from v3**), rasterio 1.5.1, rioxarray 0.23.0, exactextract 0.3.0, h3ronpy 0.22.0, pystac-client 0.9.0. Also verified: pdfplumber, camelot-py, ocrmypdf, weasyprint, lark, pydantic, jinja2 |
| `COVERAGE.md` | "overstates holdings" (C0.8) | Correct — marks ON_ODHD, ON_OAFD, ON_GEOL_BEDROCK/SURFICIAL, ON_GEOPHYS/GEOCHEM, CDoGS as READY when all are empty or failed |
| Repo move | "move to `~/projects/canada-geo-lake`" (C0.8) | **Done 2026-08-12**; path is `~/projects/canada-geo-**data**-lake`; MEMORY.md entry exists; `RAG_PLAN.md` Ollama refs annotated |
| `SK__SK_SMDI` | — | **140 rows** — implausibly low for the SK Mineral Deposit Index. Flagged, not diagnosed |
| Chroma collection | "record the embedding dimension" (C0.8) | `geo_canada` is 768-dim (Ollama nomic) vs live 1024-dim — but holds **4 rows**. Footnote, not a migration: drop it, ensure new collections record dimension |

**Environment confirmed as described:** chat `:8082` (`qwen3.6-35b`), embeddings `:8083` (mxbai, 1024), rag-proxy `:9100` — all HTTP 200. GPU RTX PRO 5000 Blackwell 48.9 GB (30 GB in use), 32 cores, 62 GB RAM, Python 3.12.3. Disk headroom ample: 22 TB free on the bulk volume, 2.8 TB on root.

---

## E. Data gap findings (second pass, 2026-08-13)

The gap accounting in `HANDOFF_AI_EXPLORATION.md` §2/§5 was re-verified and promoted into
`MASTER_PLAN.md` §6b so component plans need only one document. Four gaps it did not record,
all surfaced by the Phase-1 switch to BC:

### E1 — 46 misnamed containers, ~7.7 GB (gap #12)

The `.gpkg`/`.shp`/`.fgdb` misnaming is **not QC-only**. Lake-wide scan of 2,317 raw files
for extension-vs-content disagreement:

```
BC   1 file   0.03 GB  {'.shp': 1}     ← Bedrock Geology 2018 Shapefile.shp
NB   6 files  0.01 GB  {'.shp': 6}
NS   2 files  0.00 GB  {'.gdb': 1, '.shp': 1}
QC  37 files  7.66 GB  {'.fgdb': 12, '.gpkg': 12, '.shp': 13}
TOTAL 46 misnamed geo containers
```

`.xlsx` files also test as ZIP — that is normal OOXML and is excluded from the count above
(7 BC spreadsheets were false positives in the first scan).

**Phase-1 significance:** the single BC entry is its bedrock geology, which C2.1 (corpus
text), C1.5 (trend from mapped structures) and C2.7 (geology categoricals) all need. One
content-sniff fix in `process.py:expand()` unblocks all 46.

### E2 — Only 1 of 5 BC raw datasets reached the spatial store (gap #14)

```
raw/BC/  BC_GEOCHEM 56M · BC_GEOL 27M · BC_MINFILE 55M · BC_MTA_CURRENT 69M · BC_MTA_GRID 1.5G
geo.gpkg BC layers: ['BC__BC_MTA_CURRENT']        ← only one
```

Largely downstream of E1. BC geochem and MINFILE did reach Parquet (8 tables), so the loss is
spatial geometry, not the attribute data.

### E3 — British Columbia has no drillhole source registered (gap #13)

```
DRILLHOLE coverage by jurisdiction (gpkg layers + parquet tables):
  BC     — none registered —          ← second jurisdiction (was Phase-1 when this was found)
  ON     — none registered —          (172,259 available via OMEIS ArcGIS, see C1)
  QC     QC_SIGEOM_DRILLHOLES  187,321
  NB     NB_DRILLHOLE           17,887
  NS/YT/SK/NT_NU — none —
grep BC_ sources.py | grep -i 'drill|hole|aris'  →  only BC_ARIS_PDF (a scrape stub)
```

**This was a real cost of the BC switch. With Phase 1 back on Ontario (finding F) the urgency drops — OMEIS supplies 172,259 ON holes — but the BC gap remains real for the second jurisdiction.**
C2.4 Tier-1 negatives and the C4 dossier Drilling section have no BC input. The handoff noted
"BC (no drillhole source registered)" in passing under Drillholes; it did not carry through to
any plan. Recorded now in MASTER §6b, PLAN_C0 §0.1/0.2 (discovery task), PLAN_C2 §2.4
(coverage warning) and PLAN_C5 (possible reprioritisation of 5.2).

### E4 — Corrections to the handoff's own gap table

| Handoff said | Corrected |
|---|---|
| Gap #5 "mostly re-runs and one URL fix; `es_scroll` needs debugging — 1 day, cheapest win" | ~1.3 GB was fetched successfully then lost; `es_scroll` worked; Ontario is better served by public ArcGIS REST. See B1/C1 |
| Gap #4 "ON AFRI 100k+" | 62,357 / 62,436 across three sources. See D5 |
| Gap #1/#4 "mining-scraper's Camoufox session manager" | The anti-bot module is `sedi-scraper/antibot.py`. See D5 |
| "All 39 files are exactly 6,004 bytes" | 38 files at 6,004 bytes + one 501-byte sidecar. See B3 |
| "CGMC stored twice (EN + FR, 1.2 GB)" | Three copies, 1.83 GB — but only the intra-snapshot pair is safely removable. See B4 |
| Modality table did not include a temporal-coverage row | Added as gap #10 — the tenure archive is spatially very strong and temporally absent |

---

## F. MLAS bulk shapefiles — **A2 was wrong about Ontario** (2026-08-13, third pass)

Finding A2 concluded that "Ontario carries no owner and no dates". That is accurate **about
the OGSEarth KMZ superoverlay** — verified at raw-tile level, and the province itself labels
that product "an unofficial version to be used for viewing purposes only". It is **false
about Ontario**. The official bulk product was one URL away and the project was never
harvesting it.

```
https://www.geologyontario.mndm.gov.on.ca/mines/documents/claimaps/mlas_operational_gis_data.zip
HTTP 200 · 208,080,564 bytes · Content-Type: application/x-zip-compressed
Last-Modified: Thu, 13 Aug 2026 12:48:22 GMT     ← same day; regenerated continuously
```

ESRI shapefiles, no auth, no scraping, no API key. Attribute schemas as read:

| Layer | Features | Fields |
|---|---|---|
| `Operational_Cell_Claims` | **401,594** | `TENURE_NUM, TITLE_TYPE, TITLE_TY_1, TENURE_STA, TENURE_S_1, ISSUE_DATE, ANNIVERSAR, EXTENSION_, CLAIM_DUE_, HOLDER` |
| `Cancelled_Claim_Polygons` | **431,557** | `TENURE_HIS, TENURE_NUM, REVISION_N, TITLE_TYPE, UPDATE_TIM, ENTRY_TIME, ISSUE_DATE, ANNIVERSAR, EXTENSION_, TERMINATIO, STATUS, HOLDER` |
| `MEM_Boundary_Claims` (+`_point`) | 22,074 / 149,955 | `LegClmNu, Cell_ID, AREA_HA, TOWNSHIP, TENURE_NUM, ISSUE_DATE, ANNIVERSAR, HOLDER, CLAIM_DUE_, STATUS` |
| `Mining_Land_Tenure` | 22,940 | `TENURE_NUM, TITLE_TYPE, DISPOSITIO, AREA_IN_HE, EXPIRY_DAT, TAX_RENT_E, HOLDER, STATUS` |
| `Non_Mining_Land_Tenure` | 193,757 | `NON_MINING, DISP_LABEL, TITLE_TYPE, EFFECTIVE_, DISPOSITIO, …` |
| `Operational_Alienations` | 16,812 | `ALIENATION, ALIEN_ID, ALIEN_DESC, JUSTIFICAT, …` |
| `Plans_Permits` | 799 | `EARLY_EXPL, PROJECT_NA, TOWNSHIP_N, TENURE_HOL, HOLDER` |

Plus per-layer metadata PDFs and a Terms of Use document.

### F1 — Ownership is complete (closes gap #11)

```
Operational_Cell_Claims: 401,594 rows
  HOLDER      non-null 401,594 (100.0%)   distinct holders: 1,403
  ISSUE_DATE  non-null 401,594 (100.0%)
  ANNIVERSAR  non-null 401,594 (100.0%)
  CLAIM_DUE_  non-null 401,594 (100.0%)
top holders: (100) KENORLAND EXPLORATION LTD 54,020 · (100) Juno Corp. 28,164
             (100) Wyloo Ring of Fire Ltd. 13,796 · (100) AGNICO EAGLE MINES LIMITED 8,912
```

Ownership percentage is embedded in the `HOLDER` string as a `(NN)` prefix and must be
parsed out; multiple holders per claim appear as separate percentage-prefixed entries.
**C0.9's MLAS scraping spike is unnecessary** — the LIO ArcGIS 403 and the AngularJS SPA were
both dead ends around a door that was already open.

### F2 — Eight years of unbiased staked-and-dropped history (reverses gap #10 for Ontario)

The register's #10 asserts that registries publish current holdings only, so dropped ground
is invisible and history can only accrue forward. **Ontario retains its cancellations:**

```
Cancelled_Claim_Polygons: 431,557 rows
  ISSUE_DATE   non-null 431,557   range 2018-04-06 .. 2026-08-12
  TERMINATIO   non-null 311,074   range 2018-05-08 .. 2026-08-13
  HOLDER       non-null 326,212
terminations/yr  2019: 47,891 · 2020: 22,446 · 2021: 6,070 · 2022: 33,336
                 2023: 42,494 · 2024: 51,390 · 2025: 73,400 · 2026: 26,691
STATUS  Cancelled 303,138 · Amalgamated 102,966 · Active 20,038 · Leased 2,674 · Merged 2,379
```

This makes C1.3 heat, C2.6's staking backtest and C6.3's retroactive entry-window test
runnable on Ontario **today** rather than after four quarters of forward snapshots.

**Three caveats that must travel with it:**
1. **History begins 2018-04-06**, when Ontario converted to map staking. Pre-2018 legacy
   claims are in the separate administrative bundle (`endm_administrative_gis_data.zip`),
   not examined here.
2. **`STATUS` must be split before use.** Only the 303,138 `Cancelled` are genuine
   abandonment; `Amalgamated` (102,966) and `Merged` (2,379) are administrative
   reorganisations and would read as false drops.
3. **Terms of Use unread.** The bundled `_Terms of Use.htm` is an "MNDM Electronic
   Information Products" agreement whose substantive clauses did not extract cleanly. Read
   it before any dossier carrying this data leaves the machine (C4.1 sales render).

### F3 — The current Ontario harvest is the wrong product and undercounts by half

```
currently harvested (OGSEarth KMZ)  202,407 claims · no attributes beyond number/type/status
MLAS Operational_Cell_Claims        401,594 claims · HOLDER + 3 date fields
MLAS Cancelled_Claim_Polygons       431,557 historical records
```

`sources.py` should register the MLAS bundle as the authoritative Ontario tenure source and
demote the OGSEarth KMZ to a fallback. Note the 202,407 figure — which the plans, `COVERAGE.md`
and this audit's §0 all report as verified — is *correct for what was harvested* and *wrong
as a count of Ontario mining claims*. Exactness is not the same as completeness.

### F4 — Access difficulty, as scoped

| Route | Verdict |
|---|---|
| **MLAS operational bulk ZIP** | **Open. One `sources.py` entry.** No auth, daily refresh |
| `data.ontario.ca` "Mining Claims Information Database" | **Restricted** — "reviewing the data to determine if it can be made open"; last validated 2016 |
| LIO ArcGIS `MLAS` folder | **403 Forbidden** (Azure Application Gateway) |
| MLAS Map Viewer SPA | AngularJS 1.x; `app.config.js` is vendor boilerplate. Moot |
| ONLAND (`onland.ca`) | For PINs/title documents on mining **patents and leases** only |

---

## G. Why digital tenure records start when they do — and what exists before (2026-08-13, fourth pass)

**The split has a single cause: each jurisdiction's conversion from ground staking to online
map staking.** At conversion, live claims were converted onto a grid and the new system's
record begins; claims already dropped were not carried across. The date differs by
jurisdiction by more than two decades, which is why "recent digital start" looks like a data
problem but is really a regulatory timeline.

### G1 — Conversion dates across Canada

| Juris | Online/map staking from | System | Conversion mechanics |
|---|---|---|---|
| **QC** | **2000** | GESTIM (built 1995–2001, $2.9 M) | Map designation (*claim désigné sur carte*) becomes the principal acquisition method |
| **BC** | **2005-01-12** | Mineral Titles Online (MTO) | Ground-staked claims continued as "legacy claims"; no new ground staking. 1 M ha acquired in week 1 |
| **NL** | **2005-02-28** | MIRIAD | First online staking system in Canada; all claims must be staked electronically |
| **NB** | **2010-04-14** | NB e-CLAIMS (truePERMIT) | Physical stakes replaced by online registry |
| **SK** | **2012-12-06** | MARS | Disposition parcels replace physical staking; staking rate rose 5× |
| **NS** | **2013-08** | NovaROC | Previously in-person registration only, at Halifax |
| **ON** | **2018-04-10** | MLAS | Paper staking ended 2018-01-08; 90-day hiatus; legacy claims **converted, not cancelled**, onto a 5.2 M-cell grid (17.7 ha north → 24 ha south) |
| **NU** | **2021-01-30** | Map Selection (CIRNAC) | Regulations in force 2020-11-01; mandatory one-time conversion of ground-staked claims to grid "unit claims" on day 91 |
| **MB** | — | ground staking | Physical posts and tags still required |
| **YT** | — | physical staking | Posts in the ground; online viewer is research-only |
| **NT** | — | physical staking | Claim posts, registration within 60 days |
| **AB** | n/a | permits via AER | Permit/lease system, not staking; no public claims API |

**The corollary that matters commercially:** a jurisdiction's unbiased staked-and-dropped
history can only start at its conversion date. Ontario's begins 2018-04; Nunavut's 2021-01.
BC's, NL's and QC's are old enough (2000–2005) to be genuinely deep — **if** those registries
retain their cancellations, which is the question G3 answers per jurisdiction.

### G2 — Ontario before 2018: what actually exists

The user recalled using an online Ontario claims system as early as 2014. That is consistent —
**CLAIMaps** (the claim-map viewer) launched April 2016, with earlier viewers before it, and
Ontario had some map-staked claims pre-2018. But viewing ≠ a retained event record. Three
pre-2018 products exist, and only the first is structured:

**1. `MENDM_Legacy_Claims` — the conversion snapshot.** In
`endm_administrative_gis_data.zip` (640 MB, `Last-Modified` 2021-06-29, i.e. static):

```
33,439 features
fields: OBJECTID, OGF_ID, RECON, CLAIM_NUM, DATE_COM, DATE_CNCL, GROUPID,
        STATUS, GEOMETRY_U, EFFECTIVE_, SYSTEM_DAT, Shape_Leng, Shape_Area
DATE_COM  range 1980-01-01 .. 2018-07-28   (100% populated)
  per decade: 1980s 6,086 · 1990s 2,433 · 2000s 10,641 · 2010s 14,279
DATE_CNCL populated on 1,175 / 33,439 (3.5%)
STATUS    single value: 'Active'
OWNER     — no owner field at all
```

**Read it correctly:** every row is `Active`, so this is the set of legacy claims *live at the
2018-04-10 conversion*, carrying their original recording dates. It extends Ontario's
**staking-date** record back to 1980 — genuinely useful for `ever_staked_count` and for
dating long-held ground. It does **not** contain pre-2018 dropped ground: claims cancelled
before conversion were not carried across. So it is survivorship-biased for the pre-2018
window in exactly the way `Cancelled_Claim_Polygons` is not for the post-2018 window.

**2. Historical Mining Claim Maps** — every pre-digital claim map, hand-drawn on linen, paper
and later Mylar, scanned to PDF and indexed alphabetically by township. Hundreds of files, some
to 100 MB, **not georeferenced, no bulk download, no API**. Useful for deep diligence on a
named township; not a structured dataset. Low priority.

**3. `data.ontario.ca` "Mining Claims Information Database"** — a subset of the Computerized
Land Information Management System holding "all publicly held information associated with
mining lands". **Access-restricted**: "We are reviewing the data to determine if it can be made
open to the public", last validated 2016. This is the one worth an email — it is the pre-MLAS
system of record.

**Net answer:** Ontario's unbiased drop history starts 2018-04. Before that you can recover
*staking dates for survivors* back to 1980, and nothing systematic about what was abandoned.

### G3 — Yukon already has what Ontario's register gives, and it is already on disk

**Second correction to A2.** A2 judged Yukon survivor-only from `YT_QUARTZ_CLAIMS`
(164,985 Active / 2,603 Expired). There is a **separate layer already harvested in June and
never examined**:

```
YT__YT_HISTORICAL_CLAIMS — 244,703 features (already in geo.gpkg)
fields: TENURE_HISTORICAL_ID, DRAFTING_TENURE_TYPE, REGULATION_TYPE, GRANT_NUMBER,
        LEASE_NUMBER, CLAIM_NAME, OWNER_NAME, STAKING_DATE, EXPIRY_DATE, TENURE_STATUS
TENURE_STATUS  Expired 238,398 · Active 5,033 · Pending 1,177 · Refused 50 · Lapsed 10
TENURE_TYPE    Quartz 226,253 · Placer 15,068 · Placer prospecting lease 3,202 · Coal 175
OWNER_NAME     100% populated, 4,045 distinct (e.g. '40419 Yukon Inc. - 100%')
STAKING_DATE   1899-12-30 .. 2026-04-03   (epoch-ms int64)
EXPIRY_DATE    1992-11-29 .. 2052-03-25
staked-then-expired per decade: 1990s 22,319 · 2000s 53,857 · 2010s 158,683 · 2020s 3,883
```

Yukon therefore has **~35 years of dense, owner-attributed, unbiased staked-and-dropped
history**, requiring no new acquisition — only that C0 promote the layer and parse the dates.
Note the same `- NN%` ownership-share convention as Ontario's `HOLDER`.

Yukon never converted (still physical staking), so its register was never truncated by a
conversion event. **The jurisdictions with the deepest history are the ones that never
modernised** — the inverse of the intuition.

### G4 — New datasets identified, by jurisdiction

Everything below is public and machine-accessible unless noted. None is currently registered
in `sources.py`.

| Juris | Dataset | Size / content | Access |
|---|---|---|---|
| **ON** | `endm_administrative_gis_data.zip` → `MENDM_Legacy_Claims` | 33,439 legacy claims, `DATE_COM` to 1980 | direct ZIP (640 MB; also carries cell grid, mining divisions, lots/cons, exploration regions) |
| ON | Historical Mining Claim Maps | scanned PDFs by township, not georeferenced | per-file HTTP; low priority |
| ON | Mining Claims Information Database | pre-MLAS system of record | **access-restricted — worth an email** |
| **BC** | `MTA_ACQUIRED_TENURE_HISTORY_SP` | **303,235** historical tenure geometries (revisions; no dates/owner in-layer) | WFS — existing `wfs` connector |
| BC | MTA Mineral Titles **Client Tenure XREF** | client↔title, one title→many owners | BCGW download — **the ownership join for the above** |
| BC | MTA Mineral Titles **Person Organization MVW** | client identity records | BCGW — feeds C1.4 entity resolution |
| BC | MTA **Application / Application Event / Application Status Code** | application event stream | BCGW — candidate true event log |
| BC | MTA **Mineral Reserve Sites Business View** + **Mineral & Coal Land Reserve History SP** | reserve/no-registration sites, current + historical | WFS/BCGW — C1.1 lists these as a needed "new source" |
| BC | MTA **Crown Granted Mineral Claims** | crown grants | WFS |
| **SK** | `Mineral_Tenure_Crown_Dispositions` FeatureServer | layer 0 **Mineral Dispositions 7,456** with `OWNERS`, `EFFECTIVED`, `GOODSTANDI`; layer 3 **Lapsed 357**; layer 2 **Re-opening Lands 74** with `POSTEDON` | ArcGIS REST — existing `arcgis` connector |
| **NS** | Mineral Rights Database (DP493, from NovaROC, nightly) | exploration licences, leases, special licences | **already harvested** — trapped behind the misnamed-container bug (E1); NS has 0 layers in `geo.gpkg` |
| **NU** | Mineral Tenure in Nunavut — Mineral Claims | daily-updated claim extents | `open.canada.ca` |
| NU | Nunavut map-selection grid | grid shapefile | NRCan |
| **QC** | GESTIM bulk FTP (`gestim.mines.gouv.qc.ca/ftp/cartes/`) | mining titles, MapInfo + Shapefile, **updated every Monday** | FTP/HTTP — not currently used; we harvest SIGÉOM geoscience but not GESTIM tenure |
| **YT** | `YT_HISTORICAL_CLAIMS` | 244,703 with owner + dates | **already on disk** — promote, don't acquire |

Two observations worth carrying into planning. First, **three of the highest-value items cost
nothing to acquire** — Yukon's history is harvested, Nova Scotia's is harvested-but-trapped,
and Ontario's legacy claims are one ZIP. Second, **SK layer 2 "Re-opening Lands"** is a direct
staking-opportunity feed with a `POSTEDON` date; nothing in C1.6 anticipated that a
jurisdiction would publish reopening ground as a layer.

---

## H. The Ontario tenure licence is not open — and the dossier is the thing it restricts (2026-08-13)

C0.9 left one task: read the Terms of Use bundled with the MLAS download before any dossier
carrying that data leaves the machine. Done. It is the **MNDM Electronic Information Products**
agreement, not the Open Government Licence, and it is restrictive in exactly the direction this
business points.

Verbatim from `_Terms of Use.htm` in `mlas_operational_gis_data.zip`:

> "Noncommercial use of unsubstantial excerpts of the Content is permitted provided that
> appropriate credit is given and Crown copyright is acknowledged. **Any substantial
> reproduction of the Content or any commercial use of all or part of the Content is prohibited
> without the prior written permission of MNDM.** Substantial reproduction includes the
> reproduction of any illustration or figure, such as, but not limited to graphs, charts and
> **maps**. **Commercial use includes commercial distribution of the Content**, the reproduction
> of multiple copies of the Content for any purpose whether or not commercial, use of the
> Content in commercial publications, and **the creation of value-added products using the
> Content**."

**What this does and does not block.**

| Activity | Status |
|---|---|
| Harvesting, storing, querying, modelling on this machine | Fine — no reproduction or distribution |
| Internal dossiers used to make a staking decision | Fine — internal, non-distributed |
| **Dossier sent to a prospective buyer as a sales package** | **Requires prior written permission.** It is commercial distribution, it is a value-added product, and C4.1 renders maps — all three triggers |
| Published maps, marketing material, a public viewer | Same — permission first |

Master §1 defines the dossier as "the internal decision document and, later, **the majority of
the sales package handed to a buyer**." That terminal work product is precisely what the licence
reserves. This is not a blocker on building the system; it is a **lead-time item on the business
model**, and it should be started early because permission correspondence is slow and the answer
shapes what C4.1's sales render may contain.

**Action:** request written permission for commercial use and redistribution of MLAS tenure
content in client-facing property packages. Contacts given in the terms — MNDM Publication
Services `Pubsales.ndm@ontario.ca` (705-670-5691 / 1-888-415-9845 ext. 5691) and Crown Copyright
`Copyright@gov.on.ca` (416-326-2678). Worth asking in the same letter whether the tenure layers
are separately available under the Open Government Licence – Ontario, since many provincial
datasets are and that would remove the question entirely.

Until answered, C4.1's `render(profile="sales")` should carry an attribution block and the
**geometry-only** posture: derived analysis and our own figures, not reproduced MNDM maps. The
same question applies to every other jurisdiction's terms as they are registered — Saskatchewan
already requires layer+date citation, which the plans note.

---

## I. Execution findings from C0.1 (2026-08-14)

Found while executing the re-harvest, not by re-auditing. All four were verified against
live systems or the running code.

### I1 — The staging fix stopped the destruction but not the recovery

622eb08 fixed B1's defect: downloads stage to `<dest>.part` and every early exit unlinks
the staged copy, never `dest`. Confirmed empirically — a same-day re-run of a harvested
source leaves the payload byte-identical and the ledger row valid.

**But a re-harvest still would not have restored any of B1's eight lost payloads.** Both
skip paths treated an orphaned ledger row as proof the data was held:

```
pre-download   skip when ckan_modified is unchanged
post-download  skip when sha256 is unchanged
```

The servers still serve identical bytes, so for all eight the sha matched, the freshly
downloaded copy was staged, compared, and deleted — leaving the row pointing at nothing,
exactly as before. Observed directly: `ON_GEOL_BEDROCK`, `ON_GEOL_SURFICIAL`, `ON_OMI` and
`ON_GEOCHEM` each created an empty snapshot directory and printed **no output at all**; the
run counted them as `skipped`, which reads as success.

Both skips are now conditional on the referenced file existing (`payload_present()`). Where
the bytes match but the payload is gone, the staged copy is promoted and the row heals onto
it. Verified on `ON_OMI`: recovered at 6,304,937 bytes, matching B1's recorded size exactly.

**Doctrine:** "unchanged" is a statement about two things — the remote bytes *and* the local
copy. Checking only the first is how a repair silently becomes a no-op.

### I2 — PLAN_C0's LIO layer table lists two group layers as harvestable

`GeologyOntario_Map/MapServer` layer ids **53 and 58 are ArcGIS Group Layers**, not feature
layers, so querying either returns no features:

```
58 Precambrian  type=Group Layer  subLayers=[54,55,56,57]
53 Quaternary   type=Group Layer  subLayers=[52]
```

C1's layer list and PLAN_C0 0.1 "Third" both list them alongside real layers. The ten
genuine feature layers are registered instead; counts probed live and the two the audit
stated are exact:

```
47 OMEIS Drill Hole 172,259   50 OMEIS Technical File Area 62,436   46 OMI 18,713
48 AMIS Site 6,208   49 AMIS Feature 20,794   57 Bedrock Geology 20,956
54 Faultlines 27,042   55 Iron Formation 2,566   56 Dikes 17,038   52 Quaternary 17,906
```

### I3 — `SK__SK_SMDI` 140 rows: diagnosed (gap #15)

D5 flagged the count as implausible and left it undiagnosed. The cause is in our code, not
the source. `connectors/arcgis.py:54` builds every Hub download as:

```python
url = f"{portal}/api/download/v1/items/{item_id}/geojson?layers=1"   # layers=1 hardcoded
```

For the SMDI item, sub-layer 1 is the **mines-only** subset. The evidence is in the payload:
all 140 records carry a producing/past-producing `STATUS` — no occurrences, showings or
prospects, which are the bulk of a deposit index — over a contiguous `OBJECTID` range
779–918.

```
STATUS  Producing Mine 37 · Past Producing without Reserves/Resources 35
        Past-Producing without Resources 32 · Past-Producing with Resources 28
        Past Producing with Reserves/Resources 8          (140 total, no non-mine class)
```

**Not fixed here.** The correct endpoint could not be resolved: `gis.saskatchewan.ca/egis`
returns `500 9017$SITE_NOT_INITIALIZED` service-wide, including for the already-registered
`SK_MINERAL_EXPLORATION` layer. Retry before N4; if the outage persists, the hardcoded
`layers=1` needs to become a per-item registry field either way.

### I4 — One HTML payload the audit did not record

`verify_harvest.py` sniffed all 780 payloads then on disk. Beyond B3's known set (38 FED
geophysics + ON bedrock + ON surficial) there is one more: `BC_GEOCHEM`'s *"RGS Regional
Geochemical Database.zip"* is a GeoFiles landing page, not a ZIP.

**This does not touch the audit's BC figures.** RGS2020's 65,008 × 193 comes from
`RGS2020_data.xlsx` (48 MB) in the same snapshot, which harvested correctly — `BC_GEOCHEM`
is a multi-resource dataset and only this one sibling resource failed.

### I5 — Ontario geophysics is real, and it is not "KML previews only"

PLAN_C0 0.1's remaining-items table dismisses `ON_GEOPHYS` as *"KML previews only (3–58 KB)"*
and routes it to C3.2. That describes the **2026-06-12** snapshot. The 2026-06-13 ledger row
recorded 245,860,577 bytes, and re-fetching recovered exactly that, passing the HTML sniff:

```
Single Master Gravity and Aeromagnetic Data (Geosoft format).zip   245,860,577 bytes
  ONDIGMAG.gdb  ONDTZMAG.gdb  ONGRAVTY.gdb          ← Geosoft databases
  ONMAGONL.GRD  ONMAG1VD.GRD  ONGRV1VD.GRD (+ .gi)  ← gridded mag + gravity, 1VD
  GDS1036 Readme.txt / .doc
```

The modality table in MASTER §6b records **Geophysics: EMPTY** nationally and gap #1 calls it
"the most-used MPM evidence layers". Ontario — the Phase 1 jurisdiction — now holds provincial
gravity and magnetics on disk, including first-vertical-derivative grids. This does not close
gap #1 (that is national coverage via the GDR portal) but it removes the Phase-1 blocker.

**Caveat:** Geosoft `.GRD`/`.gdb` are proprietary and are *not* read by rasterio or GDAL's
common drivers. Converting them to COGs is real work and belongs in C0.4, not in C3.2's
browser-automation scope. Budget for it before assuming these grids are usable.

### I6 — The CGMC "legend GPKG" is a QGIS style file, and C2.1's corpus premise fails on it

C0.3 states: *"The CGMC legend GPKG (unit-description text) is the primary corpus for C2.1 —
verify it loads and its description fields are non-empty."* It does not load, because it is
not a GeoPackage:

```
Canada Geological Map Compilation - Legend file (gpkg) - English.gpkg   10,613 bytes
file → QGIS XML document        <!DOCTYPE qgis ...> version 3.28.2-Firenze
pyogrio.list_layers → DataSourceError: not recognized as being in a supported file format
```

It is a QGIS `.qml` layer-style document named `.gpkg` — the same extension-lies pattern as
E1, but a style sheet rather than a container, so the 0.2 ZIP sniff does not touch it. Parsed
as XML it yields **34 `paletteEntry` value→label pairs**:

```
1 mixed volcanic · 2 alkalic volcanic · 3 felsic volcanic · 5 mafic volcanic
8 anorthosite · 10 pegmatite · 14 amphibolite · 15 charnockite  … 34 total
label length: min 5, max 22, mean 12 characters
```

**Two consequences.** (1) This is the **value→lithology decoder for the 610 MB raster** — 34
classes — and without it the raster's pixel values are meaningless. C0.4 must ingest it
alongside the GeoTIFF. (2) It is **not a text corpus**: all 34 labels together are roughly 400
characters. C2.1's "primary corpus" must come from somewhere with real descriptive prose —
QC bedrock polygons, BC bedrock, or MRD126 unit attributes — and C2.1 should be re-scoped
accordingly before it is built.

### I7 — 47 misnamed containers, not 46

Re-scanning by magic bytes finds **47** files whose extension claims a geo format while the
content is a ZIP, totalling **8.34 GB** (E1 recorded 46 / ~7.7 GB):

```
QC 38  (.fgdb 13, .gpkg 12, .shp 13)      BC 1 (.shp)   NB 6 (.shp)   NS 2 (.gdb, .shp)
```

The extra file is `QC_SIGEOM_EXAMINE/… Document Examine - Jeux de données géographiques .fgdb`
— note the **space before the extension**, the likely reason it fell out of E1's scan
pipeline. The corrected count is self-consistent with the audit's own verified "QC SIGÉOM 13
packages": there are exactly 13 `.fgdb`, one per package.

### I8 — C0.2 acceptance evidence: all 47 containers load

Every misnamed container was expanded and read back. **22 source codes, 47 containers,
zero unexplained.** Feature totals are the sum of all layers found at the winning priority,
so they count downhole interval rows as well as collars.

| Source | Via | Features | Source | Via | Features |
|---|---|---:|---|---|---:|
| QC_SIGEOM_GEOCHEM | .gpkg | 45,088,611 | QC_SIGEOM_IGC | .gpkg | 583,690 |
| QC_SIGEOM_BEDROCK | .gpkg | 6,788,120 | QC_SIGEOM_GEOPHYS | .shp | 324,793 |
| QC_SIGEOM_QUATERNARY | .gpkg | 1,730,157 | QC_SIGEOM_GRANULATS | .gpkg | 105,255 |
| QC_SIGEOM_DRILLHOLES | .gpkg | 1,690,542 | QC_SIGEOM_MINPOT | .gpkg | 103,645 |
| QC_SIGEOM_EXAMINE | .gpkg | 941,159 | QC_SIGEOM_TOURBE | .gpkg | 82,656 |
| QC_SIGEOM_MINES | .gpkg | 31,364 | QC_SIGEOM_GEOCHRON | .gpkg | 24,464 |
| QC_SIGEOM_TRAVAUX | .gpkg | 16,064 | **BC_GEOL** | .shp | **33,409** |
| NB_MPS | .geojson | 27,555 | NB_DRILLHOLE | .geojson | 17,887 |
| NB_REPORTS_OF_WORK | .geojson | 9,684 | NB_EXPLORATION_TRENCHES | .geojson | 3,866 |
| NB_MINERAL_OCCURRENCE | .geojson | 1,611 | NB_MINERAL_CLAIMS | .geojson | 86 |
| NS_MINERAL_RIGHTS_GDB | .gdb | 2,227 | NS_MINERAL_RIGHTS_SHP | .shp | 2,227 |

Notes that matter for reading the table:

- **QC's three-format packaging.** Each SIGÉOM code ships the same data three times —
  `.gpkg`, `.shp` and `.fgdb`, all ZIPs. `.gpkg` wins on priority, so the `.fgdb` and `.shp`
  copies are never read. That is why QC loaded at all before the `.gdb` fix (I2's sibling):
  it was never depending on geodatabase support. **Nothing else in the lake had that luck.**
- **NS is 2,227, not 4,454.** Both NS codes hold the layer twice; the raw per-layer sum
  double-counts and `process_one`'s exact-duplicate drop resolves it. The two NS codes are
  themselves the same dataset in two formats — a registry-level duplication, not a bug.
- **QC drillhole collars are 187,321** (`F5E02_FORAGE_DIAMANT`), the audit's figure exactly.
  The rest of that code's 1.69 M is downhole structure: 1,150,636 `UNITE_LITHOLOGIQUE`
  intervals and 337,966 `SEQUENCE_MINERALISATION` rows — the interval data C2.4 needs.
- **BC bedrock 33,409 polygons** — the Phase-1 dependency in gap #12/#14, confirmed readable.

### I9 — MLAS harvested and verified; finding F reproduces, with expected daily drift

The bundle is now harvested rather than merely inspected. All eight shapefiles present,
read straight from the archive via `/vsizip/`. Finding F reproduces on every axis; the small
differences are the file regenerating daily, exactly as F recorded:

| Layer | 2026-08-14 | audit F (08-13) |
|---|---:|---:|
| `Operational_Cell_Claims` | **401,704** | 401,594 |
| `Cancelled_Claim_Polygons` | **431,584** | 431,557 |
| `Non_Mining_Land_Tenure` | 193,755 | 193,757 |
| `Mining_Land_Tenure` | 22,940 | 22,940 |
| `Operational_Alienations` | 16,812 | 16,812 |
| `Plans_Permits` | 800 | 799 |

`HOLDER`, `ISSUE_DATE`, `ANNIVERSAR`, `CLAIM_DUE_` are each **100% populated on all 401,704
rows**, with **1,403 distinct holders** — F's figure exactly. The top holders are unchanged:
`(100) KENORLAND EXPLORATION LTD` 54,020 · `(100) Juno Corp.` 28,164 · `(100) Wyloo Ring of
Fire Ltd.` 13,796 · `(100) AGNICO EAGLE MINES LIMITED` 8,912.

**Gap #16 is now measured rather than asserted:** 401,704 real claims against the 202,407 the
OGSEarth KMZ carries. The harvested product was missing **half the province** and all of its
ownership and expiry.

Two carry-forwards. The `(NN)` share prefix on `HOLDER` still needs parsing into
`owner_name` + `percent` — that is a derived transformation and belongs with C0.7 /C1.4, not
with `process.py`'s raw lift. And `STATUS` on the cancelled register must be split before any
row is treated as a drop (F2's caveat), which is likewise C0.7's job.

### I10 — All eight lost payloads recovered; two further defects found doing it

Every payload B1 recorded as lost is back, each matching B1's recorded byte count exactly.
`es_scroll` in particular returned 90,962 OAFD records and 6,205 AMIS — the connector the
original C0.1 text said "produced nothing", confirming B1's correction.

Two defects surfaced only because the run was watched rather than trusted:

**Truncated downloads were stored as successes.** The 640 MB `endm_administrative_gis_data.zip`
arrived as a **291,209,216-byte fragment**, was hashed, written into the snapshot and recorded
in the ledger as a successful fetch. `zipfile.is_zipfile` → **False**; no central directory.
`r.read()` returning `b""` means the *connection* ended, not that the file is complete, and
urllib does not enforce `Content-Length`. Now a hard failure at download, plus a
central-directory check in `verify_harvest` — which found this file and nothing else across
1,562 Ontario payloads.

**Transient DNS failure silently cost 7 of 10 ArcGIS layers.** Mid-run, seven layers failed
with `Temporary failure in name resolution` and `Remote end closed connection`; the network
then recovered and the remaining layers succeeded. Nothing retries, so a network blip
quietly leaves a jurisdiction half-harvested. **`harvest.py` has no retry logic at all** —
worth adding before C3.1 runs this unattended daily, which is the whole point of C3.1.

### I11 — The AFRI count reconciled; D5 corrected it in the wrong direction

D5 recorded the plans' "ON AFRI 100k+" as wrong and `62,357` as right. A full scroll export
settles it — the plans were closer:

| Figure | Value | What it counts |
|---|---:|---|
| `sources.py` note (pre-existing) | 62,357 | stale, source unknown |
| `ON_OMEIS_TECHFILE`, ArcGIS layer 50 | 62,436 | technical file **areas** — spatial footprints |
| **OAFD Elasticsearch, harvested 2026-08-14** | **90,962** | assessment **files**, 90,962 distinct `file_id` |

The two live numbers are both correct and measure different things — roughly 28,500 assessment
files have no distinct spatial footprint. That is exactly the "documents vs files" possibility
D5 raised and left open. **C3.4/C5 should budget a 90,962-document corpus, not 62,357** — 46%
more text than planned. Use the ES index for text, `ON_OMEIS_TECHFILE` where geometry matters.

### I12 — **F3 is wrong: the OGSEarth KMZ does not undercount. Our harvest did.**

This one changes a conclusion the plans lean on, so it is stated plainly.

F3 records the harvested KMZ at **202,407** claims against MLAS's 401,594 and concludes the
province's KMZ product *"undercounts claims by half"*. Gap #16 is built on that sentence, and
§0 lists 202,407 among the counts that prove the plans were reliable.

Re-harvesting Ontario completely and rebuilding the layer gives:

```
ON__ON_CLAIMS2  (OGSEarth KMZ, 355 tiles)          401,705
ON__ON_MLAS_TENURE__Operational_Cell_Claims        401,704
```

**A difference of one feature.** The KMZ was never undercounting. The cause is B1's own
destruction bug, and the mechanism is `process.py:latest()`, which takes the newest dated
directory without checking that it is complete:

| Source | tiles on 2026-06-13 | tiles on 2026-08-14 |
|---|---:|---:|
| `ON_CLAIMS2` | **164** | 355 |
| `ON_ALIENATIONS` | **106** | 434 |

The same-day re-run deleted files from the 06-13 snapshot, `latest()` picked that damaged
snapshot over the intact 06-12 one, and every Ontario tenure count in the lake has been
derived from **46% of the claim tiles and 24% of the alienation tiles** ever since.

**Every "verified exact" Ontario tenure count in §0 is therefore exact about a damaged
snapshot and wrong about Ontario.** Corrected, and cross-validated against MLAS, which is an
independently produced product:

| Layer | §0 / plans | corrected | MLAS equivalent |
|---|---:|---:|---:|
| `ON_CLAIMS2` | 202,407 | **401,705** | 401,704 |
| `ON_ALIENATIONS` | 3,480 | **23,418** | 16,812 |
| `ON_DISPOSITIONS` | 24,440 | **24,450** | 22,940 |
| `ON_DISPOSITIONS_NONMINING` | 231,389 | **230,843** | 193,755 |
| `ON_PLANS_PERMITS` | 4,571 | **934** | 800 |

The two products now agree to within a few percent everywhere, which is the expected shape:
they are different renderings of one registry. (`ON_PLANS_PERMITS` fell because the old figure
double-counted polygons repeated across tile boundaries; 934 against MLAS's 800 is the sane
answer, 4,571 was not.)

**What survives of F3 and gap #16.** The recommendation is unchanged and still correct, but
for one reason instead of two: MLAS carries `HOLDER`, `ISSUE_DATE`, `ANNIVERSAR`, `CLAIM_DUE_`
and a 431,584-row cancellation register, and the KMZ carries none of that. *That* is why MLAS
is authoritative. The claim-count argument must be withdrawn — and gap #16's headline,
"undercounts by ~50%", deleted rather than softened.

**The lesson is the one §8 already names, turned inward.** "Verify the product, not the
portal" was recorded after measuring a real product and generalising wrongly. Here the
measurement itself was of our own damaged copy. A count taken from the lake describes the
lake; only a count reconciled against the publisher describes the world. `latest()` needs a
completeness check before C3.1 automates this — a partial snapshot currently outranks a
complete one purely by being newer.

### I13 — State of the lake at the end of the C0.1/0.2/0.3/0.10 execution pass

Measured, not asserted. `verify_harvest.py` over the whole ledger:

```
rows on disk at recorded path : 3,292      (was 780 resolving of 1,804)
orphaned rows (need re-fetch) : 0          (was 8)
superseded snapshots          : 2          (ON_OAFD / ON_AMIS June exports, unrecoverable)
HTML served as binary         : 3          (was 41)
truncated archives            : 0
container/format disagreement : 0
```

`geo.gpkg`: **319 layers, was 77.**

| Juris | before | after | note |
|---|---:|---:|---|
| QC | 0 | **174** | gap #12/#14 closed — drillholes 187,321, bedrock, quaternary, geochem |
| ON | 5 | **69** | complete tenure, MLAS both bundles, OMEIS, surficial coverages |
| NS | 0 | **2** | N2 — whole jurisdiction unblocked |
| BC | 1 | **3** | bedrock 33,409, the Phase-1 dependency |
| YT/NB/SK/NT_NU/US | 71 | 71 | unchanged |

The three remaining HTML payloads are all in **superseded** June snapshots
(`ON_GEOL_SURFICIAL`, `ON_GEOL_BEDROCK` at 2026-06-12, both since re-harvested correctly, plus
one broken sibling resource inside an otherwise-good `BC_GEOCHEM` snapshot). They are retained
under the immutable-dated-snapshot contract rather than deleted.

**FED `GEOPHYSICS` is now `blocked: browser_automation`** and no longer matched. Its 38 payloads
were 6,004-byte HTML pages recorded as successful ZIPs — the mechanism by which the geophysics
modality read as populated while holding nothing. Payloads and ledger rows deleted, `_source.json`
kept as the provenance record. Real acquisition is C3.2's browser work. Note this does **not**
block Ontario, which has its own 246 MB of Geosoft gravity/magnetics (I5).

**Not done, and why.** `QC_SIGEOM_GEOCHEM`'s *spatial* pass is still OOM-killed: `process_one`
accumulates every frame for a source before grouping, and that source's wide sample layers
exceed available RAM alongside the LLM. **No data is lost** — the 561,232 sediment and 582,192
rock samples are in `geo.gpkg` via the shapefile path and all 11 tables are in Parquet — but a
source that shipped *only* a wide GPKG would lose its spatial layers this way. The fix is to
write frames incrementally instead of accumulating, using a content fingerprint for the
duplicate check rather than holding frames for comparison. Sized at roughly half a day.

Gate G0 items still open after this pass: 0.4 rasters, 0.5 fabric, 0.6 feature store,
0.7 `tenure_events`, 0.8 `COVERAGE.md` rewrite, and C3.1/C3.6.

## J. Phase 1 / C1 build state (2026-08-17)

C1 is built end to end on Ontario. What follows is the state a next session needs,
including the things that are deliberately not finished.

| Component | State | Artifact |
|---|---|---|
| 1.1 open ground | **acceptance PASSED** (15/15 hand-verified) | `processed/land_state/` |
| 1.2 rules table | built; ON drafted from primary sources, **awaiting signature** | `rules/*.yaml` |
| 1.3 heat | **acceptance PASSED** (rushes confirmed against company updates) | `processed/heat.parquet` |
| 1.4 ownership graph | built; review queue 135 (target <200) | `processed/ownership.duckdb` |
| 1.5 criticality | built; 12,056 pairs, every score self-explaining | `processed/criticality.parquet` |
| 1.6 lapse watch | built; 83 alerts, refuses to say "stakeable" | `processed/lapse_watch.parquet` |

### J1 — Bugs found by building C1, all in earlier components

Each was found by looking at output rather than by re-reading code, and each had
already produced a wrong number that nobody would have questioned.

1. **The Ontario staking series was inverted.** C0.7 took `staked` events only from the
   cancellation register — claims that have *ended* — so every recently staked, still-live
   claim was missing. The series reported ~2,000 claims staked across 2025 against an actual
   **89,466**. Ontario's biggest staking year read as its quietest. Fixed by reading both
   registers; events 1.21 M → 1.59 M.
2. **Holder parsing dropped every name containing a comma.** `parse_holder` split
   multi-holder strings on any comma, so `"(100) VCC RESOURCES, INC."` and surname-first
   people fell through unparsed and entered the ownership graph with the percentage prefix
   baked into the name. Split now happens on a comma *followed by* a parenthesised number.
3. **`heat_self` ranged to 2×10¹⁵** — robust z-scores dividing by a MAD of ~1e-15 from float
   noise, and scoring cells with one quarter of "history". Guarded by a minimum observation
   count and a relative scale floor.
4. **`tenure_events` had no location**, so no event could be assigned to a cell. Every event
   now carries `cell_r7`.

### J2 — Two structural mistakes worth remembering

**Scoring on the wrong population.** C1.6's first run returned zero alerts. Criticality is
scored on *open* ground; an expiring claim sits on *claimed* ground, so its own cell could
never match — 100% of scored cells were open cells. Zero was a real signal, not a quiet
success. The fix checks r9 neighbours, because what matters is whether the ground a claim
would *release* adjoins ground a neighbour is already reaching for.

**Using the newest window because it is newest.** Heat's top decile was computed from the
latest quarter, which is *partial* — the harvest lands mid-quarter — making it the least
representative window available. It produced 142 hot cells overlapping none of the 882 cells
holding claims due within 30 days. Now a trailing 4-quarter maximum.

### J3 — Entity resolution: precision was never the risk

Of the first 19 near-matches, most were **false**: `MICHAEL JAMES GOODMAN` vs `MICHAEL JAMES
GORDON` at 0.960 are different people, and numbered companies differ only by digits so
`1001061522` vs `1001291605` scores 0.954 by construction. Auto-merging on similarity would
have produced buyer analysis about companies that do not exist.

But recall was the real gap. String similarity **missed** the pair known to be real:
`KENORLAND EXPLORATION` vs `KENORLAND MINERALS NORTH AMERICA` scores **0.851**, because the
strings diverge completely after the first word — the ordinary shape of a corporate family.
Candidates now also come from a shared rare leading token, restricted to non-individuals
after the first attempt surfaced dozens of `AARON`/`ALLAN` pairs.

### J4 — What is deliberately unfinished

- **`rules/ON.yaml` is unsigned**, so `stakeable_now("ON")` is False and
  `holding_schedule()` refuses. Four fees are sourced and quoted; the transfer fee and the
  **grace period / forfeiture timing / reopening interval** are not, and the latter is a hard
  dependency for C1.6 rather than paperwork.
- **`unit_area_ha` measured 21.35 ha** from our own CellGrid layer, contradicting §6c's
  "17.7 ha north / 24 ha south". §6c may describe pre-2018 legacy claims. Unsettled.
- **C1.4 and C1.5 acceptances need a human** — property outlines against corporate
  presentations, and top-5 critical cells against a landman's judgement.
- **C1.6 acceptance needs two weeks** of daily runs, which began 2026-08-17.

## K. C2 build findings (2026-08-17)

### K1 — The C2.1 corpus is not made of what the plan says

PLAN_C2 2.1 names two corpus pillars. Neither survived checking, making this the **third**
corpus premise in this project to fail on contact with the data.

| Plan says | Reality |
|---|---|
| "CGMC legend GPKG unit descriptions" | A QGIS style file. 34 entries, class NAMES averaging 12 characters (audit I6) |
| "MRDS free-text fields (304,632 records)" | 87% United States. **1,551 Canadian records**, `dep_type` populated on 125, `ore_ctrl` on 89 |

What does exist is provincial bedrock attribute text, chosen by measuring average populated
length per column rather than by reading field names:

```
BC  unit_desc            ~137 chars/feature
ON  ROCKTYPE_P            ~84   "Kaolinitic clay, clay, sand, lignite"
QC  NOM_ETQT_LITH         ~62   (plus an English variant)
ON  UNITNAME_P            ~34   "Foliated tonalite suite"
```

That is the corpus: 164,577 Ontario cells, 100% fabric coverage, median 179 characters.
Thinner than the plan assumed and genuinely usable. **QC label fields carry font markup**
(`<FNT name='SIGEOM2010_STRA_10p5'>`) which must be stripped — embedding presentation markup
would spend the model's 512-token budget on font names.

### K2 — The embedding limit is tokens, so a fixed chunk size cannot be safe

D4 measured "≤ ~2,687 chars, HTTP 500 beyond ~2,781". Measured again with different text,
2,000 characters embed and **2,600 fail**. Both are right: the limit is ~512 *tokens*, and
prose tokenizes differently from comma-separated lithology lists. Chunking therefore uses a
conservative 1,200 characters *and* halves any chunk the server still rejects — the same
adaptive shape the ArcGIS pager and the resumable download needed. 6% of Ontario documents
exceed one chunk; the longest is 10,336 characters and splits into 9.

### K3 — Ontario is the wrong jurisdiction for the Zn-Pb MVT answer key

2.1 says to start with Zn-Pb MVT "solely because two published Canadian studies used it —
giving an external answer key". The reasoning is sound; the jurisdiction is not.

```
Ontario occurrences   gold 7,011   zinc AND lead 765
past/producing        gold   415   zinc AND lead  95
```

and Ontario is not an MVT province — Canadian MVT is Pine Point (NWT), Nanisivik and Polaris
(NU), Gays River (NS). Training MVT here would fit a few hundred labels of a deposit type
the province does not host, and the published answer key would not transfer to the result.
Phase 1 runs **orogenic Au**, which 2.1 itself names as the second system and calls
"business-relevant, label-rich": 6,775 occurrences → 3,749 positive cells (2.28%). Zn-Pb MVT
is retained in `labels.SYSTEMS` for when an MVT jurisdiction is in scope.

### K4 — Hole-type filtering removes 17% of the drillhole population

C2.4 calls filtering by hole type a correctness requirement rather than a refinement, and
the numbers justify the wording. Ontario's OMEIS layer holds 135,239 diamond drill holes
alongside 12,792 overburden auger, 11,264 sonic, 5,745 percussion and 6,332 underground —
**29,801 holes, 17%, that never tested bedrock as an exploration hole would**. An auger hole
that found no gold is not evidence that no gold is there.

After the full chain — hole type, no occurrence within 500 m, no follow-up hole, no restaking
within 7 years — 47,450 tier-1 negatives remain. Only **20% carry known commodities tested**;
the rest are (location, depth) with commodities unknown, which is a materially weaker
negative and is recorded as such rather than treated as "tested for everything".

## L. C6.2 build findings (2026-08-18)

### L1. The SEDAR+ corpus C3.5 plans to fetch already exists on this machine

PLAN_C3 3.5 schedules SEDAR+ pulls "reuse `mining-scraper`", and C6.2 depends on
them. Before writing a connector: `mining-scraper/Downloads/` holds **1,414 filing
index CSVs covering 1,401 distinct issuers, 658,812 filings, 1997-02-03 to
2026-08-06**, plus a 4,629-row exchange/ticker universe.

C6.2 therefore makes **no network call at all**. That is not only convenience —
this repo hitting SEDAR+ would burn the IP reputation and anti-bot posture the
scraper project maintains, for data already on disk. `config.SEDAR_CORPUS`
points at it read-only.

**What the index does and does not carry.** Document *type* and *date*, never
content and never a headline: every news release is titled `News release -
English.pdf`. So `financings_24mo` is real (a Report of exempt distribution is a
closed placement, filed and dated) while `treasury_estimate`, `burn_estimate`
and `drill_program_status` are not obtainable from it, and `buyer_capacity` and
`buyer_timing` derive from those. All five are emitted null with
`missing_because` naming the document that would fill each. Filling them is the
A1/A2 PDF campaign, not a new scrape.

### L2. Ontario registers carry no transfers — but they do carry pickups

PLAN_C6 6.2 sources `consolidator_flag` from acquisition history. Checked:
the cancelled register's `REVISION_N` tracks boundary and administrative changes,
and **across 431,735 records not one tenure shows a holder change between
revisions**. Ontario does not publish claim transfers.

What the registers do support is the behaviour that actually matters to this
business — taking over ground someone else dropped. A current claim whose polygon
sits on a cancelled claim that terminated before the current one was issued, held
by a different party, is a pickup. Ontario has **110,206**, by 739 distinct
parties, median 17 claims each.

Two traps on the way there, both of which produced confident wrong numbers:

- **Matching on the r7 cell gave 1,641,049 "pickups" from 533,210 expiries.** An
  r7 hex is 5.2 km²; two claims inside one are not the same ground. The join has
  to be spatial on claim geometry (a 400k × 430k sjoin runs in 3 seconds).
- **Comparing `HOLDER` strings raw reported all 129,291 pairs as holder changes**,
  including the 16,174 that are one company re-staking its own ground, because
  the operational register writes `(100) NAME` and the cancelled register
  `(408864) NAME (100%)`. Parse before comparing — the same two-format trap as I10.

### L3. A threshold set from a prior flagged 96% of the population

`consolidator_flag` at a flat "≥10 pickups and ≥2 counterparties" was true of 26
of 27 seeded buyers. The seed set is by construction the large holders, and the
province-wide *median* picker has taken 17 claims — the threshold sat below the
median of the thing it was supposed to select. It is now the p90 of the observed
739-picker distribution (225 claims), recomputed each run and written to the
output. Applies generally: a flag defined against a prior rather than against the
population it will be applied to ranks nothing.

### L4. 23 of 1,401 captured SEDAR+ profiles are stubs

The first dossier named **Harfang Exploration Inc.** as its most likely buyer and
reported zero material change reports and zero financings in 24 months. Harfang is
an active TSXV junior; its captured profile (issuer `000054915`) holds **one
document, a 2022 early warning report**. Puma Exploration, Eastern Platinum and
NuLegacy Gold are the same shape. SEDAR+ carries duplicate and stub profiles under
similar names and a name search can land on one.

A reporting issuer files financials at least annually, so any profile with fewer
than 10 filings or no financial statements ever is now marked
`resolved_but_profile_thin`, queued for re-capture, and carries a warning into the
dossier. **Reporting an active company as silent is worse than reporting it as
unknown** — the first is a wrong fact, the second is an honest gap.

### L5. Resolution ceiling is corpus coverage, not the matcher

8 of 20 seeded corporate owners resolved on the first pass, and the misses were
not matcher failures: Agnico Eagle, VCC Resources, East Timmins Nickel and Epica
Gold are simply **absent from the 1,401-issuer corpus**, which covers 30% of the
4,629-company universe. Unresolved owners are therefore labelled by *reason* —
`absent_from_local_corpus`, `listed_but_filings_not_captured`, `not_an_issuer`,
`near_match_queued`, `resolved_but_profile_thin` — and the capture gaps written to
`market/corpus_gaps.parquet` as a work list for the scraper project.

Two corporate families needed a second proposal channel. `KENORLAND EXPLORATION
LTD` (54,020 claims, the largest holder in Ontario) against `KENORLAND MINERALS`
scores 0.67 on sequence similarity — far below the 0.92 review threshold — and
`BARRICK GOLD INC.` against `BARRICK MINING` scores 0.69, the issuer having
renamed in 2025. Both share a distinctive first token, which is now a second
route into the review queue, with generic heads (`GOLD`, `CANADA`, `RESOURCES`, …)
excluded so the queue does not fill with coincidences. Queue: 4 rows. Still never
auto-merged.

---

## M. C4.2 build findings, and a sequencing correction (2026-08-18)

### M0. C6.2 was built two items ahead of itself, and the handoff proposed going further

MASTER_PLAN §7 lists the Phase-1 build as: *"C1.1–C1.6 for ON; C2.1 + C2.2 +
C2.4 + C2.5 + **C4.1 dossier generator v1 + C4.2 minimal map**; C6.2 buyer
identification; C6.1 comps collection begins."* C6.2 shipped with **C2.2 and
C4.2 unbuilt**. PLAN_C4 is explicit about the order inside its own component:
*"Build order within C4: dossier first, map second, DSL third, LLM front-end
last."*

This was not a harmless reordering, because two things downstream were already
depending on the map:

- **C4.1 shipped without its figures.** Section 1 specifies "cells (r9 ids +
  map inset)" and the implementation notes say figures are "inline map figures
  as static PNGs generated via the map service's render endpoint, 4.2". The
  dossier had no figures at all — not a NOT AVAILABLE block, just absence, which
  is the one thing the C4.1 doctrine forbids.
- **The handoff's recommended next step could not have passed its own
  acceptance.** It proposed C6.3 momentum. C6.3's acceptance criteria include
  "(a) the momentum surface renders in the viewer". There was no viewer. C6.3 is
  also Phase 2 work (§7 puts C6.1–C6.5 there), so taking it would have been a
  second jump forward from a Phase 1 that was not finished.

The general lesson is not "follow the plan". It is that **a component's
acceptance criteria name its real dependencies more reliably than its dependency
list does.** PLAN_C6's header lists C6.3 as depending on C1 and C2; only the
acceptance text mentions the viewer.

Phase 1 now has **C2.2 (ScienceBase benchmark layers) outstanding** and nothing
else. C2.2 is scoped at half a day and is the last item before Gate G1.

### M1. `ownership.duckdb:blocks.geometry_wkt` is EPSG:3978, not lon/lat

`ownership_graph.py` dissolves claims in a metric CRS (`to_crs("EPSG:3978")`)
and writes `blocks.geometry.to_wkt()` from the projected frame. Nothing had
noticed because every existing consumer joins on `block_id` or on
`frontier.open_cell_id` — C1.4, C1.5 and C6.2 never intersect that column
against anything.

The first spatial query against it (blocks in the map viewport, and "blocks
within 5 km" for the evidence panel) returned **zero rows** for ground that has
698 blocks in it. A lon/lat envelope against projected geometry does not error;
it silently matches nothing, and "no neighbours near this cell" is a plausible
enough answer to publish. Both the envelope and the returned geometry are now
transformed explicitly, and a test asserts the returned coordinates are Ontario
lon/lat.

**Anything else that reaches for `geometry_wkt` must transform it.** The column
is not labelled with its CRS and the store has no CRS metadata.

### M2. A cache keyed on less than it stores fails only in production

`mapapi._read_parquet` cached on `(path, mtime)` and ignored the column
projection. `/api/catalog` reads three columns of `tenure_events.parquet` for
the scrubber; `events_layer` reads six. Whichever ran first won the entry.

Every direct call passed — a fresh process, one caller, correct frame. In the
live server the catalog always loads first, so the events layer always got a
frame with no `cell_r7` and always 500'd. The smoke test passed the whole time,
because it only ever exercised one caller per process.

### M3. The event scrubber and the event layer described different series

`event_window()` reported the extent of `tenure_events.parquet` across all
jurisdictions: **1899-12-30 → 2027-11-02**. The layer it drives draws Ontario
only, which starts at map staking in 2018-04. A scrubber spanning 128 years to
control an 8-year layer puts every Ontario event in the last 6% of its travel.

The extreme dates are mostly real rather than corrupt, and the distinction
matters: 28,208 pre-2000 `staked` events are Yukon's `YT_HISTORICAL_CLAIMS`,
which genuinely reach to 1899 because Yukon never converted to map staking
(audit G3). Two artifacts do hide in there — 3 rows at `1899-12-30`, the OLE
epoch-zero a spreadsheet export writes for a null date, and 2 Yukon `expired`
rows dated in the future, which are scheduled rather than observed expiries.
`event_window()` is now jurisdiction-scoped and clipped to today, and reports
how many rows it excluded.

### M4. `update_all.py` ran a national harvest when imported

The pipeline was written as top-level statements with no `if __name__ ==
"__main__"` guard, so `import update_all` launched `harvest.py`. An import check
— the ordinary "do all 54 modules load" sweep — triggered a live Ontario
harvest, which reached CKAN discovery and took an HTTP 429 before it was killed.

Nothing was lost, and the reason is worth recording as a vindication of audit
I1's fix: staged downloads meant the aborted run left exactly **one `.part`
file, six empty dated directories, and zero ledger rows**. `verify_harvest.py`
reconciled clean afterwards (0 orphans, 0 truncated archives). The residue was
removed by hand.

The module is now guarded, and a static check confirms no module in `src/` does
work at import time. **A module that acts when imported cannot be inspected
safely** — not by a linter, not by a docs tool, not by a person checking whether
the code still loads.

### M5. The viewer makes no outbound request, and that is a data-security choice

`serve.py`'s inline UI loaded MapLibre from unpkg and basemap tiles from
`basemaps.cartocdn.com`. A tile fetch is one request per tile per pan, each
carrying the bounding box on screen.

Master §8 records that heat is public and that the defensible edge is the joined
signal plus the point-in-time archive. The viewport is the compact expression of
that joined signal — it is *where we have decided to look* — and streaming it to
a tile host for a prettier background trades the one thing the archive is for.
MapLibre is vendored (`viewer/vendor/`, BSD-3, hashes in NOTICE.md), the map's
ground is the provincial boundary and major lakes out of `geo.gpkg`, and the
style declares no `glyphs` or `sprite` origin — a style with a glyph URL fetches
from it the moment any text is drawn, so the viewer draws no text on the map and
labels in the DOM instead. `test_mapapi.py` asserts the rule rather than
trusting it, over the viewer sources and `serve.py` both.

### M6. H3 removes the need for a vector-tile pipeline at this scale

Audit D5 corrected the plan's premise that `serve.py` already had tiles, and
budgeted MVT as new work. It is avoidable for now. An H3 cell id *is* a spatial
index: `cell_to_parent` aggregates a viewport's cells to r6/r5/r4 for free, so
the resolver picks the finest resolution whose count fits a feature budget and
stamps the choice on the response. Ontario's 164,577 r7 cells come back as 3,701
r5 cells at province zoom, 2,785 r6 at regional, native r7 below that.

Two details make it honest rather than merely small: the aggregate reports
`n_source_cells` and its `agg` function, so it cannot be mistaken for the
underlying cells; and the aggregation is `max`, not `mean`, because these are
sparse intensity surfaces — 36,299 of 164,577 cells carry any heat — and
averaging a hot cell against eleven empty neighbours hides the thing the
overview exists to find.

Land state is the exception that proves it: a downsampled r9 state map would
show one arbitrary child's state as the hex's, so over budget it switches to a
*different quantity* — open fraction per r7 — and says so.


### M7. Nunavut is not dead — the daily timer already re-took it

Found while regenerating `COVERAGE.md`, not by looking for it. The handoff and
the 2026-08-17 memory both record `geo.sac-isc.gc.ca` as retired ("URL invalide"
on every endpoint including the service root), with NU accruing no new history.

The ledger says otherwise. All three NU codes fetched cleanly on **2026-08-18 at
05:41** — `NU_MINERAL_CLAIMS` 31.97 MB with a new sha256 against June's,
`NU_MINING_LEASES` 1.79 MB, `NU_PROSPECTING_PERMITS` 10.13 MB — because they are
in `TENURE_CODES` and the daily timer simply kept trying. Feature count moved
34,411 → **34,519**.

The payload is real, and better than expected: `CLAIM_NUM`, `CLAIM_STAT`
(including `CANCELLED`), `ISSUE_DATE`, `CANCEL_DT`, `STAKING_DT`, `ANNIV_DT`,
`AREA_HA`, `OWNERS`. That is owner, expiry *and* retained cancelled records —
the same shape that made Ontario the Phase-1 slice under audit F, on a register
that has only run since NU's 2021 conversion, so the history is short but
unbiased.

Two things follow. **A dead-upstream finding has a shelf life**, and this one
lasted a day; anything recorded as retired should be re-checked against the
ledger before it is planned around. And this is the first concrete return on
pulling C3.1 into Phase 0 (Master §7): nobody re-tested Nunavut, the scheduler
did, and the recovery was captured the morning it happened rather than whenever
someone next looked.

Saskatchewan has *not* recovered on the same clock, and not for the same reason:
`SK_MINERAL_EXPLORATION` and `SK_SMDI` are classed `geoscience`, which runs
Sundays, so their next attempt is 2026-08-23. That is cadence, not an outage.


---

## N. C2.2 build findings (2026-08-20)

C2.2 was the last Phase-1 item. The plan scoped it at half a day as a benchmark
pull; it is closer to a day, and it is not mainly a benchmark.

### N1. For Ontario, CMMI is not a benchmark — it is the geophysics modality

PLAN_C2 2.2 lists three uses and puts "benchmark comparators" first. On this
machine the second use dominates. Ontario's geophysics is empty in practice:
FED `GEOPHYSICS` is blocked behind the GDR portal and needs a browser (C3.2,
unbuilt, audit B3), and `ON_GEOPHYS` is 246 MB of proprietary Geosoft `.GRD`
that no common driver reads. The CMMI grids are ordinary GeoTIFFs that cover the
whole province.

Fifteen rasters now grid onto the Ontario r7 fabric at **96–100% cell coverage**,
contributing **60 features over all 164,577 cells**: gravity and its HGM, the
30 km upward continuation and its HGM, magnetic anomaly, RTP, RTP-HGM, RTP-VD,
deep-source separations, depth to LAB, depth to Moho, and satellite-gravity shape
index. That is the modality gap in Master §6b's register closing without C3.2.

The release also ships worm (multiscale edge) shapefiles and a continent-wide
fault layer, harvested but not yet gridded — they are vector, so they need
`gridify.dist_to`, not `rasters.zonal`.

### N2. Two of the plan's premises about the release are wrong

- **There is no sediment-thickness layer for US/Canada.** PLAN_C2 2.2 lists
  "sediment-thickness and proximity layers". The release's depth products for
  this region are LAB and Moho only; sediment thickness does not exist here under
  any name. Third C2 plan premise to fail against the actual product, after CGMC's
  legend (I6) and the corpus composition (K1).
- **The prospectivity surfaces are Zn-Pb, and we do not ship a Zn-Pb model.**
  "Published surfaces as benchmark comparators for every model this component
  ships" cannot be satisfied as written: the published surfaces are
  clastic-dominated and Mississippi-Valley-type Zn-Pb, our shipped model is
  orogenic Au, and audit K3 already established Ontario is not an MVT province.
  See N5 for what the comparison is actually good for.

### N3. The CD prospectivity GeoTIFF ships with no CRS, and asserting one is not guessing

`USCanada_Lawleyetal_CDModel.tif` carries no CRS. `rasters.ingest` refused it,
correctly — inventing a CRS silently misplaces every value a raster holds.

But it is determinable rather than unknown. Its MVT sibling, same release folder
and same team, declares EPSG:4326 on a grid identical to full float precision:
23005×10368, transform `(-180.002198652943, 0.005650762016448, 83.12553464295827,
-0.005650762016448)`, bounds `(-180.0022, 24.5384, -50.0064, 83.1255)` — and
those bounds are decimal degrees on their face.

`ingest()` now takes `assume_crs=` **and requires `crs_evidence=`**, storing both
in the registry so the assertion travels with the layer and can be argued with.
An asserted CRS without a recorded reason is a guess with extra steps, and is
refused.

### N4. The registry recorded a CRS the file did not have

Getting N3's assertion onto disk took three attempts, and the first two failed
*silently*:

- `rio_copy(ds, dst, crs=crs)` — the kwarg is ignored outright.
- `rasterio.open(dst, "r+").crs = crs` — the COG driver does not take it.

Both left the registry recording `crs: EPSG:4326` for a COG carrying no CRS at
all. This is audit B3's shape exactly — the ledger said ZIP and the disk held
HTML — reproduced by our own code inside a single function.

What works is a `WarpedVRT` with `src_crs == crs`, which attaches the CRS and
warps nothing. Three defences were added, because the failure mode is silence:
the write is **read back** and the ingest fails if the assertion did not survive;
the registry stores `crs_asserted` and the evidence; and the "source unchanged,
skip" fast path now **re-checks an asserted CRS against the file** instead of
trusting the record — audit I1's lesson, that a skip resting on a record rather
than on the file turns a repair into a no-op, applied to a second function.

### N5. Cross-system rank agreement is not a benchmark, but it is not noise

Against the published surfaces, our orogenic-Au model scores:

| published surface | Spearman ρ | top-decile overlap (chance 0.10) |
|---|---:|---:|
| CD Zn-Pb | +0.147 | 0.144 |
| MVT Zn-Pb | **−0.502** | 0.024 |

The MVT result is the informative one and it is **strongly negative, as it should
be**. Orogenic Au is an Archean greenstone-belt system in the Superior Province;
MVT Zn-Pb is a Phanerozoic carbonate-platform system in the Hudson Bay Lowland
and southern Ontario. In Ontario those are disjoint terranes. A model that scored
both the same ground highly would have learned something generic; ρ = −0.50 is
evidence ours has learned terrane.

That is a sanity check, not an accuracy measurement, and `cmmi.py` will not
report it as one: `PUBLISHED_SYSTEMS` labels each surface with its deposit type,
any cross-type pairing is printed as `cross-system reference` rather than
`comparator`, and each carries a stated prior expectation so the number cannot be
quoted as a score. A real benchmark for C2 needs a published *orogenic-Au*
surface, which this release does not contain.

### N6. The release grids geology onto H3 r7 — the same resolution as our fabric

`Geology_H3Grid_Canada` is 1,820,346 cells of bedrock geology aggregated onto
**H3 resolution 7**, which is our modelling fabric's resolution exactly. PLAN_C2
2.2 asked for "a reference feature matrix to validate `gridify.py` aggregation
choices"; what exists is far better — an independent implementation of the same
operation on the same grid, cell for cell, with no resampling in between.

**Cell sets — the fabric is validated.** Of our 164,577 Ontario r7 cells,
164,220 (99.8%) are in their Ontario set. They carry 20,702 we do not; **20,280
of those (98.0%) have centroids inside the major lakes `fabric.py` deliberately
clips**. That is not a disagreement, it is two defensible answers: we exclude
open water because every feature this system computes is undefined there, and
CMMI carries a lithology under the lake. Setting the water aside, cell-set
agreement is **99.53%**, with 422 of theirs and 357 of ours unexplained —
borderline cells where two centroid tests fall either side of a boundary.

**Majority lithology — inconclusive, and reported as such.** Coarse-class
agreement (intrusive / extrusive / sedimentary / metamorphic) is 55.9% over
101,944 cells. That number does not measure our aggregation:

- **46% of all disagreement is one cell of the confusion matrix** — ours
  `metamorphic` against theirs `intrusive`, 20,509 cells. A systematic one-way
  swap is a taxonomy boundary, not an aggregation error: Superior Province
  granitoids are filed as orthogneiss and migmatite by CGMC and as intrusive by
  CMMI. The crosswalk is doing more work than the aggregation is.
- Agreement is **66.1% where the reference reports no lithological contact in the
  cell and 24.0% where it reports one**. Majority is ill-defined exactly where
  two units compete for a cell, so disagreement concentrating there is what two
  *correct* implementations would do.

`validate_gridify()` prints both diagnostics and records
`verdict: inconclusive` rather than emitting a bare 55.9% for someone to quote as
a pass or a failure.

### N7. `write_features` said "append" and overwrote — a second producer erased the first

`gridify.write_features` was written when one build pass produced the entire
feature matrix. C2.2 is the second producer, and gridding the CMMI rasters into a
new snapshot left it holding **60 geophysics features and none of the 239 geology
ones**. Nothing errored. Every consumer that reads the newest snapshot — the
dossier's evidence table, the viewer's popover, `_feature_percentiles` — would
have reported bedrock, faults and drillhole density as simply absent.

It was caught because C2.2's own lithology validation went looking for
`bedrock__dominant__*` and found none.

Two changes: frames are merged into any matrix already in the snapshot, and
`carry_forward=<prior snapshot>` copies features the run did not recompute,
recording `carried_from`, `carried_features` and `computed_features` in the
manifest. Carried values are stale by construction, so they are **named** rather
than blended invisibly. The 2026-08-20 snapshot now holds 299 features: 60
computed, 239 carried from 2026-08-17.

The general point: **a docstring is not an invariant.** This one had promised
"append" since it was written, and the first caller that depended on the promise
found it false.

### N8. The publisher's checksums are worth using

Every ScienceBase file object carries an MD5. The harvest ledger records our own
sha256, which answers "the same bytes as last time" but not "the bytes USGS meant
to serve" — a truncated download has a perfectly consistent sha256 of its own.
`cmmi.py --verify` checks the harvest against the publisher's digests; all 24
files pass. Any source that publishes checksums should be verified against them
rather than only against ourselves.


---

## Change log

- **2026-08-13** — initial audit; all findings above recorded after a second challenge pass. Six first-pass conclusions were corrected: B1 strengthened, B2 reframed, B4/D3/D5-Chroma downgraded, A2 qualified.
- **2026-08-13 (third pass)** — finding F: the MLAS operational bulk shapefiles overturn A2. Ontario ownership and an unbiased 2018-onward staked/dropped history are openly published. Phase 1 reverted to Ontario; C0.9 withdrawn; gap #11 closed and #16 opened.
- **2026-08-14 (execution pass)** — section I, found while running C0.1 rather than by auditing: the harvest skip logic would have made the re-harvest a silent no-op (I1); two of the LIO layer ids the plan lists are group layers (I2); gap #15 `SK_SMDI` diagnosed as our own hardcoded `layers=1`, fix blocked on an SK service outage (I3); one further HTML payload found by `verify_harvest.py` (I4). B2's 1,016 ogsearth rows reconciled from disk, sha-verified — no data lost, as predicted.
- **2026-08-18 (C6.2 pass)** — section L: the SEDAR+ corpus C3.5 planned to fetch is already on disk (1,401 issuers, 658,812 filings), so C6.2 makes no network call; Ontario publishes no claim transfers but 110,206 geometric pickups; two measurement traps (r7 co-occurrence, unparsed HOLDER) and one threshold-from-a-prior corrected; 23 captured issuer profiles found to be stubs.
- **2026-08-18 (C4.2 pass)** — section M: C6.2 had been built ahead of C2.2 and C4.2, and the handoff's C6.3 recommendation could not have met its own acceptance without a viewer (M0); `blocks.geometry_wkt` is EPSG:3978 and silently matched nothing against a lon/lat envelope (M1); a parquet cache keyed on less than it stored failed only under the live server's call order (M2); the event scrubber spanned Yukon's 1899 paper record while its layer drew Ontario (M3); `update_all.py` ran a national harvest on import (M4); the viewer and its figures now make no outbound request at all (M5); H3 parent aggregation defers the MVT pipeline (M6); and Nunavut, recorded dead upstream on 2026-08-17, was re-harvested by the daily timer on 2026-08-18 with owners, staking dates and retained cancellations (M7).
- **2026-08-20 (C2.2 pass)** — section N: for Ontario the CMMI release is the geophysics modality rather than a benchmark, closing that gap without C3.2 (N1); the plan's sediment-thickness layer does not exist and the published surfaces are Zn-Pb against our orogenic-Au model (N2); the CD GeoTIFF ships with no CRS, so `ingest()` now takes an evidenced assertion (N3) after two silent failures left the registry describing a file that did not exist (N4); cross-system rank agreement is reported as a sanity check, not a score, and ρ=−0.50 against MVT is the geologically correct sign (N5); the release's own H3 r7 geology grid validates our fabric at 99.53% once deliberate lake clipping is set aside, while the lithology comparison is inconclusive because the taxonomy crosswalk dominates (N6); `write_features` said append and overwrote, and C2.2 as the second producer erased the 239 geology features until it was fixed (N7); publisher MD5s are now verified (N8).
