# Canada Geoscience & Mining Data Lake — Architecture & Plan

A self-hosted, perpetually-updating mirror of Canada's public geological, mining, and
exploration data — federal + every province and territory — structured for metadata
search, visualization, and downstream NI 43-101 / DCF extraction tooling.

This started as an Ontario-only build and now generalizes nationally. The same four
ideas carry over: **discover (never hardcode) → snapshot → normalize → index.**

---

## 1. The key architectural insight: three connector types cover all of Canada

You do **not** need 13 bespoke scrapers. Almost every source falls into one of three
patterns, so the system is a *declarative registry* (`src/sources.py`) feeding three
generic connectors (`src/connectors/`):

| Connector | What it talks to | Examples (verified) |
|---|---|---|
| **ckan** | CKAN open-data portals (identical `package_search` API) | `open.canada.ca` (NRCan — CGMC, CDoGS, geophysics, national tenure), `data.ontario.ca` (mines org), `catalogue.data.gov.bc.ca` |
| **arcgis** | ArcGIS REST FeatureServers + ArcGIS Hub download items | Saskatchewan (`gis.saskatchewan.ca/egis`, `geohub.saskatchewan.ca`), Yukon, QC SIGÉOM, tenure layers |
| **scrape** | Legacy assessment-report systems (PDF corpora) | AFRI (ON), ARIS (BC), SMAD (SK), GeoFiles (NL), DCDH (NS), AGS (AB), NTGS (NWT/NU) |

Adding a jurisdiction = adding a dict entry to `sources.py`. No new code.

> **Corrections from the source notes:** Saskatchewan's assessment system is **SMAD**
> (+ **SMDI** deposit index), *not* "MARS". Quebec is **GESTIM** (tenure) + **SIGÉOM**
> (geoscience). These are fixed in the registry.

---

## 2. Coverage (see COVERAGE.md for the full matrix)

- **Federal/pan-Canadian (start here):** CGMC (national bedrock, ML-ready), CDoGS
  (1,300+ geochemical surveys), Geophysical Data Repository (aeromag/gravity/radiometric),
  National Mineral Tenure layer, national mineral deposits — all via `open.canada.ca` CKAN.
- **Provinces/territories:** ON, BC, QC, SK, MB, NL, NS, AB, NB, YT, NT/NU — each with its
  tenure, drill-hole/occurrence, and assessment-report systems mapped.

Why federal-first: the CGMC already stitches provincial bedrock maps into one standardized
layer, and `open.canada.ca` federates many provincial datasets, so one CKAN pass yields a
clean national base before you touch any province-specific system.

---

## 3. Storage layout (national)

```
canada-geo-lake/
├── raw/<JURISDICTION>/<CODE>/<YYYY-MM-DD>/   # immutable dated snapshots + _source.json
│     e.g. raw/ON/ON_ODHD/2026-06-12/ , raw/FED/CGMC/... , raw/BC/BC_MINFILE/...
├── pdfs/<JURISDICTION>/<CODE>/<id>.pdf       # assessment-report corpora (opt-in)
├── processed/
│   ├── geo.gpkg                              # ALL vector layers, one file (QGIS/GDAL)
│   └── tables/*.parquet                      # flat attribute tables
├── catalog.duckdb                            # national metadata search index
├── manifest.sqlite                           # harvest ledger (url/hash/date/version)
└── logs/
```

Layer naming in the GeoPackage: `<JURIS>__<CODE>__<sourcefile>` so a single map can
overlay, say, `ON__ON_ODHD__drillholes` with `BC__BC_MINFILE__occurrences`.

**Sizing:** national vectors + tabular ≈ low tens of GB. The full assessment-PDF corpora
across provinces is the big tail (ON AFRI alone 30–80 GB; BC ARIS 33,500+ reports). Raw
geophysics rasters / CGMC GeoTIFFs add more. Plan: **2–4 TB** if you want all PDFs +
rasters nationally; a 1 TB SSD covers all structured data + Ontario PDFs comfortably.

---

## 4. Perpetual access & updates

Identical to the Ontario design, now national: `harvest.py` resolves *current* URLs each
run (CKAN links rotate; we never hardcode), snapshots only what changed by
`last_modified` + sha256, and ledgers everything in `manifest.sqlite`. Dated snapshots +
never overwriting `raw/` give you perpetual access and point-in-time history (diff this
month's claims against last). Cadence in `crontab.example`: tenure daily, CKAN weekly,
PDFs monthly.

---

## 5. Metadata search

`build_index.py` loads every Parquet table + GPKG layer + the manifest into
`catalog.duckdb` and builds one **cross-jurisdiction full-text index** over text columns
(company, commodity, deposit, township, status, work type…). You then query all of Canada
in one SQL surface — e.g. every gold occurrence within 5 km of a past-producing mine,
nationwide. See `QUERIES.md`.

---

## 6. The tools (unchanged targets, now national inputs)

1. **Spatial viewer** — deck.gl/MapLibre over `geo.gpkg` via a small FastAPI feature/tile
   endpoint; layer toggles per jurisdiction; click → metadata + assessment-PDF link.
   Reuses your existing self-hosted 3D-viewer stack.
2. **Metadata search UI** — faceted filters (jurisdiction, commodity, company, year,
   status) over the DuckDB FTS → results on the map.
3. **Document/RAG extractor** — the AFRI/ARIS/SMAD/GeoFiles PDF corpora feed your local-LLM
   RAG stack to pull intercepts, grades, and economic assumptions, joined back to
   drill-hole geometry — the bridge to NI 43-101 structuring and DCF modelling.

---

## 7. Run order

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python src/discover.py                      # full national inventory (dry run)
python src/discover.py --jurisdiction FED   # just the federal aggregators
python src/harvest.py                       # download all changed core data
python src/process.py                        # build processed/geo.gpkg + parquet
python src/build_index.py                    # build catalog.duckdb

# later, after finishing scrape stubs you care about:
python src/harvest_pdfs.py --code BC_ARIS_PDF
```

---

## 8. What still needs a one-time manual step (honest status)

- **scrape connectors are stubs.** `connectors/scrape.py` has a finished *framework* and
  per-system hints, but the actual `enumerate_ids()` / `pdf_url()` for each legacy system
  must be filled in by inspecting that site's network calls (~15 min each with devtools).
  This is deliberate: those endpoints are undocumented JS-app calls that would be wrong if
  guessed. The structured-data harvest (CKAN + ArcGIS) needs no such step.
- **A few ArcGIS layer URLs** (QC SIGÉOM à-la-carte, MB MapGallery, NB mapviewer, NT/NU)
  need their FeatureServer/item IDs confirmed once via `discover` against the live service;
  the registry notes say exactly where to look.
- **BC MTA grid and a few tenure layers are too large for whole-province pulls** — chunk by
  NTS mapsheet / area of interest (noted in the registry).

The CKAN sources (federal CGMC/CDoGS/geophysics/tenure, Ontario's full set, BC MINFILE/
geology, etc.) and the verified ArcGIS items (SK SMDI) are ready to harvest as shipped.

---

## 9. Licensing

Mostly **Open Government Licence (Canada / provincial variants)** + King's Printer / EIP
terms — free to use & redistribute with attribution. `_source.json` provenance sidecars
preserve the attribution you'll need when redistributing inside a commercial product.
Saskatchewan uses its Standard Unrestricted Use Data Licence v2.0 (requires layer+date
citation). Check each `_source.json` before redistribution.
