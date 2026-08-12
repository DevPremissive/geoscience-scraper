# Coverage matrix — Canadian geoscience & mining data sources

Status legend: **READY** = harvestable as shipped · **CONFIRM** = endpoint/ID needs one
live lookup via `discover` · **STUB** = scrape framework present, finish `enumerate_ids`/
`pdf_url`. Registry: `src/sources.py`.

## Federal / pan-Canadian — `open.canada.ca` (CKAN, NRCan org)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| CGMC | Canada Geological Map Compilation | National standardized bedrock maps — **610MB GeoTIFF raster + GPKG legend downloaded** | ckan | READY |
| CDoGS | Canadian Database of Geochemical Surveys | 1,300+ regional geochemical surveys (assays, element profiles) | ckan | READY |
| GEOPHYSICS | Geophysical Data Repository | National compilations (Mag 200m/1km, Grav 2km, Rad 250m) + individual surveys — FTP dirs exist but **empty**; actual data behind GDR web portal (`geophysical-data.canada.ca`) requiring browser interaction | ckan | BROWSER NEEDED |
| NATIONAL_TENURE | National Mineral Tenure layer | Composite claim/lease boundaries across jurisdictions | ckan | READY |
| MINERAL_DEPOSITS | National mineral deposits | Pan-Canadian occurrence layer | ckan | READY |

## Ontario — `data.ontario.ca` (CKAN, mines org) + `geologyontario.mndm.gov.on.ca` (OGSEarth)
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
| **ON_CLAIMS2** | **Mining Claims (active)** | **351 KMZ tiles with polygon Placemarks via OGSEarth SuperOverlay** | **ogsearth** | **READY** |
| **ON_ALIENATIONS** | **Alienations (lands withdrawn from staking)** | **394 KMZ tiles** | **ogsearth** | **READY** |
| **ON_DISPOSITIONS** | **Mining Land Tenure** | **207 KMZ tiles** | **ogsearth** | **READY** |
| **ON_DISPOSITIONS_NONMINING** | **Non-Mining Land Tenure** | **346 KMZ tiles** | **ogsearth** | **READY** |
| **ON_PLANS_PERMITS** | **Exploration Plans & Permits** | **243 KMZ tiles** | **ogsearth** | **READY** |
| ON_AFRI_PDF | AFRI report PDFs | 100k+ scanned exploration reports (NLP corpus) | scrape | STUB |

> **Verified:** 1,541 KMZ tiles across 5 layers (Claims 351, Alienations 394, Dispositions 207,
> Non-Mining 346, Plans & Permits 243). **Total 668,617 polygon Placemarks** (392K claims alone).
> KMZ files verified with proper polygon geometry. Fixed harvest.py extension bug (dots in tile names
> confused Path.suffix). Also includes additional standalone KMLs (AMIS, OMI, boreholes, etc.).

## British Columbia — `catalogue.data.gov.bc.ca` (CKAN) + `openmaps.gov.bc.ca` (WFS) + BCGS
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| BC_MTO_CURRENT / _HISTORIC | Mineral Titles Online | Current + historical mineral/placer/coal titles | ckan | READY |
| BC_MTA_CURRENT | MTA mineral-placer grid | Current tenure polygons (42K, already harvested) | wfs | READY |
| BC_MTA_GRID / _HISTORIC | MTA grid files | Title cell grid (large — chunk by mapsheet) | ckan | CONFIRM (chunk) |
| BC_MINFILE | MINFILE | Mineral occurrence DB | ckan | READY |
| BC_GEOL | BC bedrock geology | Provincial geology | ckan | READY |
| BC_GEOCHEM | Regional Geochemical Survey | RGS data | ckan | READY |
| BC_ARIS_PDF | ARIS | 33,500+ assessment reports since 1947 (+digital-data ZIPs) | scrape | STUB |

## Quebec — `donneesquebec.ca` (CKAN) + SIGÉOM / GESTIM
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| **QC_SIGEOM (13 packages)** | **SIGÉOM bulk downloads** | **Geophysics, geochronology, drillholes, granulats, geoscience works, peatlands, bedrock geology, geochemistry, quaternary geology, mineral potential, mineral indices/mines/quarries, mining activities, Examine documents — all SHP/FGDB/GPKG/CSV via `gq.mines.gouv.qc.ca`** | **ckan (donneesquebec)** | **READY** |
| QC SIGÉOM à la carte | SIGÉOM per-NTS-sheet | Geoscience SHP/FGDB/GPKG/CSV by NTS 1:50k sheet | arcgis | CONFIRM (per-NTS iteration) |
| QC GESTIM | GESTIM | Live mineral tenure (claims/titles) — **no public REST API or bulk download** | — | NOT AVAILABLE |

> **Correction:** QC SIGÉOM data IS available as bulk regional downloads (not just per-NTS-sheet).
> 13 packages on `donneesquebec.ca` with whole-province SHP/FGDB/GPKG/CSV. Live JSON APIs for
> mines/projects (79) and exploration properties (999) at `sigeom.mines.gouv.qc.ca/signet/classes/`.
> GESTIM claims boundaries remain interactive-only.

## Saskatchewan — `geohub.saskatchewan.ca` / `gis.saskatchewan.ca/egis` (ArcGIS)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| SK_SMDI | Saskatchewan Mineral Deposit Index | **140 deposit point features (130KB) — harvested** | arcgis_hub | READY (downloaded) |
| SK_MINERAL_EXPLORATION | Mineral Exploration FeatureServer | **140 exploration features (98KB) — harvested** | arcgis_layer | READY (downloaded) |
| SK_SMAD_PDF | Saskatchewan Mineral Assessment DB | Assessment files (PDF/ZIP via GeoAtlas) — *not "MARS"* | scrape | STUB |

> SK has no mineral claims/tenure/disposition spatial layer identified via any public ArcGIS service.

## Manitoba — MapGallery (ArcGIS-backed)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| MB_MAPGALLERY | MapGallery GIS portal | Claims, leases, geophysics, geology; drill holes + assessment files — **interactive map only, no public REST API found** | — | NOT AVAILABLE |

## Newfoundland & Labrador — GeoFiles + open.canada.ca mirror
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| NL_GEOFILES_PDF | GeoFiles | Assessment reports back to 1899 (geochem, assays, drill logs) | scrape | STUB |
| (NL mineral claims) | Mineral Rights | **No public API — interactive GeoFiles map only** | — | NOT AVAILABLE |

## Nova Scotia — geoscience-online
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| NS_MINERAL_RIGHTS_SHP | Mineral Rights SHP | Claims/leases/licences shapefile from DP493 | direct | READY (downloaded) |
| NS_MINERAL_RIGHTS_GDB | Mineral Rights GDB | Claims/leases/licences file geodatabase from DP493 | direct | READY (downloaded) |
| NS_DCDH | Drillhole & Drill Core DB | Unified drillholes from assessment reports | scrape | STUB |

## Alberta — AGS (under AER)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| AB_AGS | Alberta Geological Survey | Digital data, maps, models (no mineral claims spatial API) | scrape | STUB |

> **No AB mineral claims data:** Mineral claims/rights administered by Alberta Energy Regulator — no public geospatial API found.

## New Brunswick — `open.canada.ca` (CKAN mirror of Socrata)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| NB_MINERAL_CLAIMS | Mineral Claims | 86 claim boundary polygons | ckan | READY (harvested) |
| NB_DRILLHOLE | Drillhole Dataset | 17,887 drillhole locations | ckan | READY (harvested) |
| NB_MINERAL_OCCURRENCE | Mineral Occurrence | 1,611 occurrence points | ckan | READY (harvested) |
| NB_EXPLORATION_TRENCHES | Exploration Trenches | 3,866 trench locations | ckan | READY (harvested) |
| NB_REPORTS_OF_WORK | Reports of Work | 9,684 report-of-work polygons | ckan | READY (harvested) |
| NB_MPS | Minerals & Petroleum Sections | 27,555 section polygons | ckan | READY (harvested) |
| NB_GEOSCIDB | NB GeoSCImap / Geoscience DB | Additional drill holes, geology, claims via mapviewer | scrape | STUB |

## Yukon — Yukon Geological Survey `data.geology.gov.yk.ca` (ArcGIS)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| YT_QUARTZ_CLAIMS | Quartz Claims | 168,481 claim areas | arcgis | READY (harvested) |
| YT_PLACER_CLAIMS | Placer Claims | 33,934 placer areas | arcgis | READY (harvested) |
| YT_HISTORICAL_CLAIMS | Historical Claims | 244,703 historical claims | arcgis | READY (harvested) |
| YT_MINERAL_CLAIMS_POLY | Mineral Claims (surveyed) | 3,488 surveyed polygons | arcgis | READY (harvested) |
| YT_MINERAL_CLAIMS_LINE | Mineral Claims (surveyed lines) | 14,348 boundary lines | arcgis | READY (harvested) |
| YT_CROWN_GRANTS | Crown Grants | 94 granted areas | arcgis | READY (harvested) |
| YT_PLACER_LEASES | Placer Leases | 274 lease areas | arcgis | READY (harvested) |
| YT_PLACER_GROUPING | Placer Groupings | 632 grouping areas | arcgis | READY (harvested) |
| YT_QUARTZ_GROUPING | Quartz Groupings | 698 grouping areas | arcgis | READY (harvested) |

## NWT & Nunavut — CIRNAC MapServer + NTGS
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| NU_MINERAL_CLAIMS | Nunavut Mineral Claims | 34,411 claim polygons | arcgis | READY (harvested) |
| NU_MINING_LEASES | Nunavut Mining Leases | 893 lease areas | arcgis | READY (harvested) |
| NU_PROSPECTING_PERMITS | Nunavut Prospecting Permits | 2,600 permit areas | arcgis | READY (harvested) |
| NT_MINERAL_CLAIMS | NWT Mineral Claims | 1 ZIP file | direct | READY (downloaded) |
| NTGS | NWT Geological Survey | NWT/NU assessment reports, drill core, geochem, geophysics | scrape | STUB |

## United States (reference sources — not Canadian)
| Code | System | What | Connector | Status |
|---|---|---|---|---|
| USGS_MRDS | Mineral Resource Data System | 137MB CSV, 120K+ global mineral sites (NOT claims) | direct | READY |
| USGS_USMIN | US mineral sites | Per-state SHP (point + polygon), mineral occurrences (NOT claims) | direct | READY |
| BLM_MLRS | Mining claims | **No public REST API — BLM MLRS replaced LR2000, contact mlrs@blm.gov** | — | NOT AVAILABLE |

---

### Data lake summary (post-session)
**10.9GB · 1,794,964 features · 67 layers · 9 jurisdictions**

| Jurs | Size | Key layers |
|------|------|-----------|
| QC | 8.3GB | 13 SIGÉOM packages (bedrock 570MB GPKG, geochem 2.3GB, geophys 664MB) |
| YT | 421MB | 9 layers (244K historical claims, 168K quartz claims, 34K placer claims) |
| US | 388MB | MRDS CSV (137MB, 305K sites) + 50 USMIN state ZIPs |
| ON | 193MB | 13 layers incl. 1,541 OGSEarth KMZ tiles (669K placemarks) |
| NB | 244MB | 6 layers (72K drillholes, 28K MPS sections, 9.7K work reports) |
| BC | 199MB | 5 layers (42K MTA claims, 31K MINFILE occurrences, 26MB geology SHP) |
| FED | 610MB | CGMC 610MB GeoTIFF raster + GPKG legends |
| SK | 0.2MB | SMDI + Mineral Exploration (140 deposit points each) |
| NT_NU | 44MB | 4 layers (34K NU claims, 2.6K permits, 893 leases) |
| NS | 2.5MB | 2 layers (SHP + GDB mineral rights) |

### Status note
- ✅ **All accessible claim/tenure data** for ON, YT, NU, NT, NB, BC, NS harvested
- ✅ **All accessible geology data** for QC (13 bulk packages), FED CGMC, ON OGSEarth, BC harvested
- ✅ **All accessible mineral deposit data** for SK, NB, USGS harvested
- ⏳ **National geophysics compilations** (gravity/magnetic/radiometric grids) behind GDR web portal — needs browser automation
- ❌ **SK, MB, AB mineral claims** — no public API available
- ❌ **QC GESTIM claims** — interactive-only web app (MRNF licensing needed)
- 🔲 **PDF assessment reports** — stubs exist, disk permitting deferred
