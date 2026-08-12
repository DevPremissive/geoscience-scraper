# Handoff — Geological Data Lake vs. AI Mineral Exploration Literature

**Reference paper:** Taghipour et al. (2026), *Artificial Intelligence for Mineral Exploration: Methods, Foundation Models, and Future Directions*, Preprints 202607.2303.v1 (UWA/TUM/Murdoch). 36 pp, 205 refs.
**Assessed:** 2026-08-12 · **Bulk:** `/media/vis/Expansion/canada-geo-lake` · **Index:** `/home/vis/infra/canada-geo-lake-data` · **Code:** `/home/vis/projects/canada-geo-data-lake` (git)
**Purpose:** state of the data lake and code measured against the exploration methodologies the literature actually uses, and where the gaps are.

---

## 0. Verdict in one paragraph

The lake is a **strong tenure and occurrence archive, a genuinely excellent geochemical archive, and an empty geophysics/remote-sensing archive.** Against the paper's four evidence modalities (geology · geophysics · geochemistry · remote sensing) you hold 2.5 of 4. The single most-used MPM input in the literature — gridded gravity/magnetics/radiometrics — is **not on disk despite appearances** (see §2 correction). Remote sensing is entirely absent. Text, your highest-latent-value modality (150k+ assessment reports enumerable), is at zero bytes. But the paper's *only demonstrated end-to-end foundation-model pathway* — geological text → prospectivity — is reproducible **today, with no new data acquisition**, and both of its reference studies (Lawley et al. [36], Parsa et al. [125]) are Canadian Zn-Pb MVT work using exactly the datasets you already hold. That is the highest-leverage move available.

---

## 1. What the paper says exploration workflows need

Condensed map of the methodology space, so gaps below are legible.

| § | Workflow | Required inputs | Representative methods |
|---|---|---|---|
| 3.1 | Lithological mapping & 3D geological modelling | Geological polygons, drillhole logs, DEM, remote sensing | Bayesian nets, contrastive GAT, GeoINR, Sub3DNet, flow matching |
| 3.2 | Geophysical inversion | Gravity/magnetic/EM/seismic **grids** | CNN inversion, PINNs, neural operators, Adapted-SRGAN |
| 3.3 | Geochemical anomaly / fertility / thermobarometry | Multi-element assays (tabular, compositional) | DAN-GRF, deviation nets, XGBoost on zircon chemistry |
| 3.4 | Alteration & structure mapping | Multi/hyperspectral imagery | CNN, SpecPool-Transformer, unmixing, SAM/segment-geospatial |
| 4 | **Mineral Prospectivity Mapping (MPM)** | All of the above, co-registered as *evidence layers* on a common grid + deposit labels | WoE → RF/XGBoost → CNN/GNN/Transformer → PU/semi-supervised → Bayesian UQ → 3D |
| 5.1–5.2 | Foundation models | Imagery archives (EO), seismic (geophys) | Clay, Prithvi, DOFA, TerraMind, SpectralGPT / SeismicFM, GEM3D |
| 5.3 | NLP & knowledge extraction | Geological **text** — reports, map legends, logs | GeoVec/GloVe, GeoBERT NER, knowledge graphs, K2/GeoGalactica/JiuZhou |
| 5.3.4 | **Text → prospectivity** | Map-polygon descriptions + deposit labels | Lawley (GloVe+NB), Zhang (LLM+KG+gcForest), Parsa (BERT embeddings, 1.8M hex cells, Canada) |
| 5.5 | Agentic orchestration | Tool registry over all of the above | Earth-Agent (MCP, 104 tools), OpenEarthAgent |
| 7.1 | Cross-cutting hygiene | Spatial CV, informative negatives, ontologies, benchmarks | spatial blocking, barren drillholes as negatives, GeoCore/GSO |

Two framing points the paper hammers that should shape your build:
1. **Positive-Unlabeled, not binary.** Class ratios ~1:10,000. Absence of a known deposit ≠ barren. Random negatives introduce large prediction variance (§7.1); *barren drillholes and tested ground* are the recommended negatives.
2. **Spatial non-stationarity.** Random train/test splits leak through spatial autocorrelation and inflate results. Spatially blocked / leave-one-region-out CV is the reporting standard the field should adopt.

---

## 2. Verified state of the lake (on-disk, 2026-08-12)

`raw/` **≈13 GB · 9 jurisdictions** · `processed/geo.gpkg` **1.4 GB / 77 vector layers** · `processed/tables/` **206 MB / 41 Parquet** · `catalog.duckdb` **1.4 GB / 125 tables + FTS** · `manifest.sqlite` **1,804 harvest records**.

### ⚠️ Four corrections to `COVERAGE.md` — the docs overstate what is on disk

| Claim in docs | Reality on disk |
|---|---|
| FED `GEOPHYSICS` — 39 national grid ZIPs present (`raw/FED/GEOPHYSICS/2026-06-13/`) | **All 39 files are exactly 6,004 bytes — HTML error pages, not ZIPs.** `unzip` fails on every one. National mag/grav/radiometrics = **0 bytes of real data.** |
| `ON_GEOL_BEDROCK` harvested | 49,932-byte HTML interstitial, not the 133 MB MRD126 shapefile. `sources.py` *has* the correct Azure blob URL; the `direct` connector never fetched it. |
| ON drill holes / occurrences / geochem / geophys / assessment index "READY" | Only 6.8 KB **KML pointers** exist. The 126k-hole ODHD, OMI, lake geochem and OAFD index were never pulled. `es_scroll.py` connector exists (126 lines) and produced nothing. |
| FED `NATIONAL_TENURE`, `MINERAL_DEPOSITS`, `CDoGS` "READY" | Never harvested — `raw/FED/` contains only `CGMC/` and the broken `GEOPHYSICS/`. CDoGS = 1,300+ regional geochemical surveys, still untouched. |

Also: **CGMC's 610 MB GeoTIFF is stored twice** (EN + FR identical, 1.2 GB) and never processed. **QC vector geometry never reached `geo.gpkg`** — all 13 SIGÉOM packages are in `raw/` as `.gpkg`/`.fgdb`/`.shp` (incl. a 570 MB bedrock GPKG and a 1.0 GB geochem GPKG), but `process.py` only lifted their CSVs to Parquet. `geo.gpkg` contains **zero QC layers**. Recoverable — QC tables carry `ESTN`/`NORD`/`Coord_X`/`Coord_Y` — but bedrock *polygons* are absent from the spatial store.

### Asset inventory by paper modality

**Geochemistry — STRONG.** The best thing in the lake.

| Dataset | Rows | Analytes |
|---|---|---|
| QC SIGÉOM stream/lake sediment | **561,232** | 124 cols: majors (SiO2…P2O5) + ~76 elements incl. Au, Ag, Cu, Pb, Zn, Ni, Mo, W, U, Th, full REE suite, PGE |
| BC RGS2020 | **65,008** | 193 cols — multi-method (FA / AAS / ICP / INA) determinations of ~150 analytes |
| BC water geochem 2015 | 4,332 | hydrogeochem |
| QC heavy minerals / erratics / geochron | 3,606 / 1,562 / 2,735 | indicator-mineral + U-Pb ages |

Directly supports §3.3.1 anomaly detection (DAN-GRF-class) and §4 geochemical evidence layers. Note the paper's constraint: these are **compositional** data — CLR/ILR log-ratio transform required before any ML. Nothing in the current code does this.

**Geology — PARTIAL.**
- FED **CGMC** national bedrock lithology raster, 610 MB GeoTIFF, EPSG:3978, + legend GPKG (English/French). The paper explicitly calls this class of product "ML-ready". Untouched by `process.py`.
- QC bedrock **outcrop points**: 778,153 compilation + 385,731 géofiche. Polygons sitting unprocessed in raw.
- BC bedrock geology SHP (26 MB) in raw, not in `geo.gpkg`.
- ON bedrock: **failed download** (see corrections).
- QC quaternary: 120,184 surface-morphology + 18,175 glacial-erosion marks — ice-flow vectors, directly relevant to till/indicator-mineral dispersal-train targeting.

**Drillholes — MODERATE, and better than it looks.**
- QC `Forages au diamant`: **187,321 holes** with *downhole* interval structure — `PROF1..n` / `LITH1..n` / `MINR1..n` (depth, lithology, mineralisation). This is a genuine 3D dataset, and the exact input `dh2loop`/`litho2strat` (§7.1) are built to harmonise.
- NB: 17,887 holes (also in `geo.gpkg` with geometry).
- Missing: ON ODHD 126k (failed), NS DCDH 28,341 (blocked), BC (no drillhole source registered).

**Deposit labels (MPM positives) — GOOD but unharmonised.**

| Source | Count | Label richness |
|---|---|---|
| USGS MRDS | **304,632** | `dep_type`, `model`, `alteration`, `ore_ctrl`, `hrock_type`, `arock_type`, `structure`, `tectonic`, `dev_stat`, `prod_size` — free-text, ideal for §5.3.4 |
| USMIN (50 states) | ~750k features | point + polygon mineral sites |
| BC MINFILE | 15,142 (+1,696 products, 13,731 reserves) | deposit type/class/character codes, status incl. past-producer |
| QC SIGÉOM métalliques / mines / activités | 9,291 / 79 / 1,078 | substance, status |
| QC MINPOT `Cibles d'exploration minérale` | **5,622** | pre-computed exploration targets — usable as a benchmark comparator |
| NB / SK SMDI | 1,611 / 140 | occurrence points |

**Tenure — VERY STRONG, and strategically underrated.** ~1.0 M polygons: ON claims 202,407 + non-mining dispositions 231,389 + dispositions 24,440 + alienations 3,480 + plans & permits 4,571; YT historical claims **244,703** + quartz 168,481 + placer 33,934 + 5 more layers; NU 34,411 + leases/permits; BC MTA 42,285; NB, NS, NT. See §4-E — this is your unfair advantage on the PU-learning problem.

**Geophysics — EMPTY.** Only QC EM-anomaly SHP survives. No gravity, no magnetics, no radiometrics, no DEM.

**Remote sensing — ZERO.** No Sentinel-2, Landsat, ASTER, hyperspectral, or DEM of any kind.

**Text — ZERO bytes.** `pdfs/` empty; `chroma_db/` is 772 KB (effectively empty). All 7 legacy PDF systems are stubs. Enumerable but unharvested: ON AFRI 100k+, BC ARIS 33,500+, SK SMAD ~14,889, NL GeoFiles 5,000+, NS DCDH, NTGS, NB PARIS.

---

## 3. Code state

```
src/  3,702 lines  ·  connectors/  956 lines
  sources.py (592)   declarative national registry — the genuinely good part
  harvest.py (150)   URL re-resolution + sha256 + dated snapshots + manifest ledger
  process.py (135)   raw → geo.gpkg + Parquet          [vector/tabular only]
  build_index.py     → catalog.duckdb + cross-jurisdiction FTS
  serve.py (327)     FastAPI feature/tile endpoints
  vector_store.py (224) / pdf_extract.py (95)          [written, never run at scale]
  connectors/  ckan · arcgis · wfs · ogsearth · es_scroll · scrape(500, all stubs)
```

**Architecture is sound** — declarative registry + four generic connectors, dated immutable snapshots, provenance sidecars, hash-based change detection. Adding a jurisdiction is a dict entry. Point-in-time diffing (this month's claims vs. last) is a real capability few people have.

**What is missing for the paper's workflows, structurally:**

| Missing | Why it matters |
|---|---|
| **Any raster handling** | `requirements.txt` has no `rasterio`/`xarray`/`rioxarray`/`zarr`. `process.py` handles vector + tabular only. Every §3.2/§3.4/§4 method operates on rasters. The CGMC GeoTIFF and (eventually) geophysics grids have nowhere to go. |
| **A grid/cell fabric** | No common CRS grid or H3 hex tessellation. MPM *is* "co-register N evidence layers onto one cell index." Parsa et al. used 1.8 M hex cells over Canada. Nothing here can produce a feature matrix. |
| **Label harmonisation / ontology** | MINFILE codes, MRDS free text, SIGÉOM French substance codes, NB — four schemas, no crosswalk. Paper §7.1 names GeoCore and GSO (Loop3D) as the semantic backbone. |
| **Any ML code** | No sklearn, no torch, no UNCOVER-ML, EIS Toolkit, TorchGeo, SimPEG, GemPy, map2loop, SHAP. The lake is a data lake with no modelling layer attached. |
| **Spatial CV utilities** | Nothing enforces spatially blocked splits — the paper's #1 "addressable today" hygiene item. |
| **RAG plan is stale** | `RAG_PLAN.md` specifies Ollama `nomic-embed-text` at :11434. Your stack retired Ollama (2026-07-13); embeddings are now llama.cpp `mxbai` at **:8083**, chat at **:8082**. Dimensions and endpoint both change. |

---

## 4. What your data enables *today*, ranked

### A. Text → prospectivity — the paper's only demonstrated FM pathway. **Do this first.**
§5.3.4 · Lawley et al. [36] and Parsa et al. [125], both **Canadian Zn-Pb MVT**, both on datasets you hold.
- **Have:** CGMC legend GPKG (map-polygon lithological descriptions — Lawley's exact input); MRDS 304,632 records with `dep_type`/`model`/`alteration`/`ore_ctrl`/`hrock_type`/`structure`/`tectonic` free text; MINFILE deposit-type descriptions; DuckDB FTS already built over all text columns.
- **Have (infra):** llama.cpp chat :8082, mxbai embeddings :8083, ChromaDB, rag-proxy :9100 — the whole NLP stack is already running for other projects.
- **Need:** a hex-cell fabric to attach embeddings to, and CGMC raster→polygon-description join.
- **Why first:** zero new data acquisition, zero blocked scrapers, reuses infrastructure you've already debugged, and reproduces published Canadian results you can validate against.

### B. Geochemical anomaly detection — §3.3.1
626k multi-element samples (QC + BC) is a serious corpus. Deliverable: a national geochemical anomaly evidence layer.
- **Need:** CLR/ILR compositional transform; spatially-adaptive thresholding (the DAN-GRF idea — background varies regionally); detection-limit and multi-method reconciliation (BC reports the same element by FA/AAS/ICP/INA).

### C. Tabular deposit-type & fertility classification — §3.3.2
MRDS 304k rows with host rock, alteration, ore control, tectonic setting → XGBoost/LightGBM deposit-type and size classification. Pure tabular, runs on CPU, immediately useful as a screening layer and as a label-enrichment step for A.

### D. Drillhole log harmonisation & 3D — §3.1.2 / §7.1
QC's 187,321 holes with `LITH`/`MINR` intervals → run `dh2loop`-style fuzzy lithology standardisation, then `litho2strat`. Feeds directly into **mining-viz** (your 3D viewer, live at app.premissive.ca) and supersedes **drill-database**'s 320 news-extracted holes by three orders of magnitude.

### E. Exploration-effort layer — a genuinely novel angle, §7.1 negative sampling
The paper's central complaint: nobody knows how to pick negatives, and random negatives wreck predictions. You hold ~1.0 M tenure polygons **including 244,703 Yukon historical claims, ON alienations/dispositions/plans-and-permits, and NB's 9,684 reports-of-work polygons**. Ground that was staked, worked, and dropped is the best available public proxy for "tested and found wanting" — far more informative than random points, and the closest public analogue to the barren-drillhole negatives the paper recommends. Nobody in the reviewed literature uses tenure history this way. This is a defensible methodological contribution, not just a data asset.

### F. Baseline MPM (WoE / RF) — §4.2
Blocked until geophysics grids + a grid fabric exist. Currently you could only build a geochem+geology+tenure model, which is a legitimate but partial baseline.

---

## 5. Gaps, ranked by impact on exploration workflows

| # | Gap | Impact | Path to close | Effort |
|---|---|---|---|---|
| 1 | **National geophysics grids** (mag 200m/1km, grav 2km, radiometrics 250m) | Blocks §3.2 entirely and cripples §4 — gravity/magnetics are the most-used MPM layers in the literature | GDR portal `geophysical-data.canada.ca` needs browser automation. **You already own the tooling**: camoufox + the SEDAR+/SEDI anti-bot module (ResponseLogger, ShieldTracker, TrustMetric, backoff, Mullvad rotation) | 1–2 days, high confidence |
| 2 | **Remote sensing + DEM: nothing at all** | Blocks §3.4 and every EO foundation model in §5.1 (Clay, Prithvi, DOFA, SpectralGPT, TerraMind) — the most mature FM building block in the paper | Sentinel-2 / Landsat / ASTER / Copernicus DEM via **STAC APIs** — open, no auth, no scraping, no blocking. Add a `stac` connector alongside ckan/arcgis/wfs | 2–3 days |
| 3 | **No raster pipeline or grid fabric** | Without this you cannot build a feature matrix, so no MPM of any kind | Add rasterio/rioxarray/xarray/zarr; define a national H3 or EPSG:3978 grid; write `gridify.py` (layer → cell aggregation) | 3–5 days |
| 4 | **Text corpus at zero** | Blocks §5.3 NER/KG work and the richest Canadian exploration knowledge that exists | 7 blocked systems, ~14 days per `SCRAPERS_BLOCKED.md`. Prioritise **BC ARIS** (33.5k, richest) and **ON AFRI** (100k+, best-documented stub). Reuse mining-scraper's Camoufox session manager | 4–5 days for the first two |
| 5 | **Broken/never-run harvests** | CGMC bedrock, ON bedrock, ON ODHD 126k holes, FED CDoGS 1,300 surveys, FED national deposits + tenure — all *supposed* to be present | Mostly re-runs and one URL fix; `es_scroll` needs debugging. Cheapest win on the list | 1 day |
| 6 | **No label harmonisation** | MPM positives are unusable across jurisdictions without a crosswalk; blocks cross-terrane transfer (§7.2) | Commodity + deposit-type crosswalk table; adopt GSO/GeoCore vocabulary | 2–3 days |
| 7 | **No modelling layer** | Data lake with nothing attached to it | Adopt EIS Toolkit (EU, prospectivity-specific) or UNCOVER-ML; TorchGeo/TerraTorch for the EO side; SHAP for §4.3.3 | ongoing |
| 8 | **No spatial CV / PU protocol** | Results will be silently inflated and unpublishable | Small utility module — spatially blocked splits, leave-one-terrane-out, PU bagging. Do it *before* the first model, not after | 1–2 days |
| 9 | **No 3D/voxel or uncertainty story** | §4.5 and §7.2 — the frontier the paper says is most open | Deferred until 1–3 land; QC drillholes + mining-viz are the natural substrate |

---

## 6. How this connects to your other projects

| Project | Relationship |
|---|---|
| **mining-scraper** / SEDAR+ / SEDI | Camoufox + anti-bot module is the unlock for gap #1 (GDR portal) and gap #4 (7 blocked PDF systems). Already proven against harder targets. |
| **local LLM stack** (:8082 chat, :8083 mxbai, Chroma, rag-proxy :9100) | The §5.3 NLP layer, already running. `RAG_PLAN.md` must be updated off Ollama. |
| **mining-viz** (live, app.premissive.ca) | The §4.5 3D visualisation surface for prospectivity volumes and QC drillholes. |
| **drill-database** (PostgreSQL, 320 holes) | Should treat the lake as upstream — QC alone has 187k holes vs. its 320 news-extracted. |
| **pmx** (NI 43-101 → auditable DCF) | Downstream of target testing; assessment-report PDFs (gap #4) are shared feedstock. |
| **hermes-rag** | Precedent architecture for the geo RAG layer. |

---

## 7. Recommended sequence

**Phase 0 — repair (≈1 day).** Fix the five broken/never-run harvests (#5). Re-point ON bedrock at the Azure blob URL already in `sources.py`. De-duplicate the 1.2 GB CGMC EN/FR copy. Load QC vector geometry into `geo.gpkg`. Update `COVERAGE.md` — it currently overstates the lake, which will mislead the next session.

**Phase 1 — text→prospectivity spike (≈1 week).** §4-A. No new data. Reproduce Lawley/Parsa on CGMC legend text + MRDS/MINFILE labels over a hex fabric. Ships a real prospectivity map and forces the grid fabric (#3) into existence as a side effect.

**Phase 2 — close the modality gaps in parallel.** GDR geophysics via camoufox (#1) and a STAC connector for Sentinel-2/ASTER/DEM (#2). These two turn 2.5-of-4 modalities into 4-of-4 and unlock every EO foundation model in the paper.

**Phase 3 — modelling layer.** Spatial CV + PU protocol first (#8), then EIS Toolkit/UNCOVER-ML baselines (WoE, RF), then the tenure-derived exploration-effort negatives (§4-E) as the differentiated contribution.

**Phase 4 — text corpus at scale.** BC ARIS then ON AFRI, into NER/KG, feeding both prospectivity features and pmx.

---

## 8. Housekeeping

- **~~The project lives in `~/Downloads/`~~** — done 2026-08-12. Code moved to `~/projects/canada-geo-data-lake/` (git history intact), bulk data to the external drive, indexes to `~/infra/canada-geo-lake-data/`. See README "Storage layout".
- **Not in `MEMORY.md`.** The largest geoscience asset on this machine has no memory entry — add one.
- **Uncommitted since 2026-06-12.** The repo has been idle two months; nothing in flight, safe to resume.
