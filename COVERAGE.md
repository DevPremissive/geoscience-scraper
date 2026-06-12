# Coverage matrix — Canadian geoscience & mining data sources

Status legend: **READY** = harvestable as shipped · **CONFIRM** = endpoint/ID needs one
live lookup via `discover` · **STUB** = scrape framework present, finish `enumerate_ids`/
`pdf_url`. Registry: `src/sources.py`.

## Federal / pan-Canadian — `open.canada.ca` (CKAN, NRCan org)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| CGMC | Canada Geological Map Compilation | National standardized bedrock maps, ML-ready rasters + GPKG legend | ckan | READY |
| CDoGS | Canadian Database of Geochemical Surveys | 1,300+ regional geochemical surveys (assays, element profiles) | ckan | READY |
| GEOPHYSICS | Geophysical Data Repository | Aeromagnetic / gravity / radiometric / borehole logs | ckan | READY |
| NATIONAL_TENURE | National Mineral Tenure layer | Composite claim/lease boundaries across jurisdictions | ckan | READY |
| MINERAL_DEPOSITS | National mineral deposits | Pan-Canadian occurrence layer | ckan | READY |

## Ontario — `data.ontario.ca` (CKAN, mines org) + GeologyOntario
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| ON_ODHD | Ontario Drill Hole DB | 126k+ holes: coords, orientation, depth, assay flags | ckan | READY |
| ON_OAFD | Assessment File DB | Index to historical exploration reports | ckan | READY |
| ON_OMI | Ontario Mineral Inventory (was MDI) | Known occurrences/deposits | ckan | READY |
| ON_AMIS | Abandoned Mines Info System | Legacy sites + hazards | ckan | READY |
| ON_PUB | OGS Publications | 3000+ reports, 10000+ maps index | ckan | READY |
| ON_GEOL_BEDROCK / _SURFICIAL | Seamless geology | Bedrock + surficial polygons | ckan | READY |
| ON_NOEGTS | N. Ontario Engineering Geology Terrain Study | Terrain/infrastructure | ckan | READY |
| ON_OSTR | Ontario Surficial Terrain Reports | Overburden thickness, bedrock topo | ckan | READY |
| ON_GEOPHYS / ON_GEOCHEM | Geophysics / lake geochemistry | Survey boundaries + data | ckan | READY |
| ON_AFRI_PDF | AFRI report PDFs | 100k+ scanned exploration reports (NLP corpus) | scrape | STUB |

## British Columbia — `catalogue.data.gov.bc.ca` (CKAN) + BCGS
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| BC_MTO_CURRENT / _HISTORIC | Mineral Titles Online | Current + historical mineral/placer/coal titles | ckan | READY |
| BC_MTA_GRID | MTA mineral-placer grid | Title cell grid (large — chunk by mapsheet) | ckan | CONFIRM (chunk) |
| BC_MINFILE | MINFILE | Mineral occurrence DB | ckan | READY |
| BC_GEOL | BC bedrock geology | Provincial geology | ckan | READY |
| BC_GEOCHEM | Regional Geochemical Survey | RGS data | ckan | READY |
| BC_ARIS_PDF | ARIS | 33,500+ assessment reports since 1947 (+digital-data ZIPs) | scrape | STUB |

## Quebec — SIGÉOM (geoscience) + GESTIM (tenure)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| QC SIGÉOM | SIGÉOM à la carte | Geoscience SHP/FGDB/GPKG/CSV/KML by theme or NTS sheet (French) | arcgis | CONFIRM (per-NTS iteration) |
| QC GESTIM | GESTIM | Live mineral tenure (claims/titles) | arcgis | CONFIRM |

## Saskatchewan — `geohub.saskatchewan.ca` / `gis.saskatchewan.ca/egis` (ArcGIS)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| SK_SMDI | Saskatchewan Mineral Deposit Index | All known occurrences (spatial) | arcgis_hub | READY (item id verified) |
| SK_MINERAL_EXPLORATION | Mineral Exploration FeatureServer | Exploration/assessment cross-ref layer | arcgis_layer | READY |
| SK_SMAD_PDF | Saskatchewan Mineral Assessment DB | Assessment files (PDF/ZIP via GeoAtlas) — *not "MARS"* | scrape | STUB |

## Manitoba — MapGallery (ArcGIS-backed)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| MB_MAPGALLERY | MapGallery GIS portal | Claims, leases, geophysics, geology; drill holes + assessment files queryable together | scrape→arcgis | CONFIRM (inspect ArcGIS layers) |

## Newfoundland & Labrador — GeoFiles + open.canada.ca mirror
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| NL_GEOFILES_PDF | GeoFiles | Assessment reports back to 1899 (geochem, assays, drill logs) | scrape | STUB |
| (NL tenure/drilling stats) | Mineral Rights Open Data | Claims, drilling statistics | ckan (federal mirror) | READY |

## Nova Scotia — NovaROC + DCDH + geoscience-online
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| NS_DCDH | Drillhole & Drill Core DB | Unified drillholes from assessment reports | scrape | STUB |
| (NS NovaROC) | NovaROC | Mineral rights/claims registry | scrape | STUB |

## Alberta — AGS (under AER)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| AB_AGS | Alberta Geological Survey | Digital data, maps, models (lighter hard-rock assessment) | scrape | STUB |

## New Brunswick — Geoscience Database + mining mapviewer
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| NB_GEOSCIDB | NB GeoSCImap / Geoscience DB | Drill holes, assessment files, geology, claims | scrape→arcgis | CONFIRM |

## Yukon — Yukon Geological Survey `data.geology.gov.yk.ca` (ArcGIS)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| YT (multiple) | YGS open data | Claims, occurrences, assessment reports, geochem, geophysics | arcgis | CONFIRM (add layer URLs) |

## NWT & Nunavut — NTGS
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| NTGS | NWT Geological Survey | NWT/NU assessment reports, drill core, geochem, geophysics | scrape | STUB |

---

### Priority order for activation
1. **FED CKAN** (CGMC, CDoGS, geophysics, tenure) — national base, zero manual work.
2. **ON + BC CKAN** — richest structured provincial data, zero manual work.
3. **SK ArcGIS** (SMDI verified) + confirm **YT** layers — high exploration activity.
4. **QC SIGÉOM/GESTIM**, **MB**, **NB** ArcGIS — confirm one ID each.
5. **PDF corpora** (ON AFRI → BC ARIS → NL GeoFiles → SK SMAD …) — finish stubs as the
   RAG/document-extraction pipeline needs each region.
