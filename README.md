# Geoscience Scraper — Canada Geoscience & Mining Data Lake

A self-hosted, perpetually-updating mirror of Canada's public geological, mining, and
exploration data — federal + every province and territory — structured for metadata
search, visualization, and downstream NI 43-101 / DCF extraction tooling.

**Core idea:** discover (never hardcode) → snapshot → normalize → index.

## Architecture: three connector types cover all of Canada

| Connector | What it talks to | Examples |
|-----------|-----------------|----------|
| **ckan** | CKAN open-data portals (identical `package_search` API) | `open.canada.ca` (NRCan), `data.ontario.ca`, `catalogue.data.gov.bc.ca` |
| **arcgis** | ArcGIS REST FeatureServers + ArcGIS Hub download items | Saskatchewan, Yukon, Quebec SIGÉOM, tenure layers |
| **scrape** | Legacy assessment-report systems (PDF corpora) | AFRI (ON), ARIS (BC), SMAD (SK), GeoFiles (NL) |

Adding a jurisdiction = adding a dict entry to `src/sources.py`. No new code.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python src/discover.py                      # full national inventory (dry run)
python src/discover.py --jurisdiction FED   # just federal aggregators
python src/harvest.py                       # download all changed core data
python src/process.py                       # build processed/geo.gpkg + parquet tables
python src/build_index.py                   # build catalog.duckdb search index

# PDF assessment reports (after finishing scrape stubs):
python src/harvest_pdfs.py --code BC_ARIS_PDF
```

## Coverage

**Federal (ready to harvest):** CGMC (national bedrock), CDoGS (1,300+ geochemical surveys),
Geophysical Data Repository, National Mineral Tenure, Mineral Deposits — all via
`open.canada.ca` CKAN.

**Provinces (ready):** Ontario (ODHD, OMI, geology, geophysics, publications), British Columbia
(MTO titles, MINFILE, geology, geochem), Saskatchewan (SMDI — ArcGIS Hub).

**Provinces (needs one-time confirm):** Quebec (SIGÉOM/GESTIM), Yukon, Manitoba, New Brunswick.

**PDF corpora (scrape stubs):** Ontario AFRI, BC ARIS, Saskatchewan SMAD, NL GeoFiles,
NS DCDH, AB AGS, NTGS.

See `COVERAGE.md` for the full matrix.

## Storage layout

```
canada-geo-lake/
├── raw/<JURISDICTION>/<CODE>/<YYYY-MM-DD>/   # immutable dated snapshots
├── pdfs/<JURISDICTION>/<CODE>/<id>.pdf        # assessment-report PDFs (opt-in)
├── processed/
│   ├── geo.gpkg                               # ALL vector layers, one file
│   └── tables/*.parquet                       # flat attribute tables
├── catalog.duckdb                             # national metadata search index
├── manifest.sqlite                            # harvest ledger
└── logs/
```

## Scheduled updates

```bash
# Daily — fast-moving tenure jurisdictions
python src/harvest.py --jurisdiction BC SK YT

# Weekly — full core harvest + process + index
python src/update_all.py

# Monthly — assessment-report PDF corpora
python src/harvest_pdfs.py
```

See `crontab.example` for full scheduling setup.

## License

Data sources: Open Government Licence (Canada / provincial variants), King's Printer / EIP
terms, Saskatchewan Standard Unrestricted Use Data Licence v2.0. Each raw snapshot carries
a `_source.json` provenance sidecar preserving attribution.
