"""
sources.py — the national registry of Canadian geoscience & mining data sources.

This is the heart of the system. Every jurisdiction is declared here as data, not code,
so adding a province = adding a dict entry. Three connector TYPES cover ~everything:

    "ckan"    -> a CKAN portal; harvested via package_search/package_show (stable API).
    "arcgis"  -> an ArcGIS REST FeatureServer/MapServer or Hub item; paged GeoJSON.
    "scrape"  -> a legacy assessment-report system needing a bespoke index crawl (PDFs).

Field reference per source:
    portal      : base URL (CKAN root, or ArcGIS service/item, or scrape seed)
    org/fq      : (ckan) organization slug or Solr filter query to scope the harvest
    match       : {CODE: [title substrings]} to label/keep datasets (ckan)
    layers      : (arcgis) {CODE: layer_query_url} list of FeatureServer/N endpoints
    items       : (arcgis hub) {CODE: hub_item_id} for /api/download endpoints
    notes       : caveats (chunking, confidentiality, renames) — READ THESE.

VERIFIED indicates the endpoint pattern was confirmed against live sources as of build.
Where a tenure/scrape endpoint needs a one-time ID lookup, notes say so and the
discover tooling helps you find it.
"""

# ---------------------------------------------------------------------------
# TIER 1 — FEDERAL / PAN-CANADIAN AGGREGATORS  (start here; standardized layers)
# ---------------------------------------------------------------------------
FEDERAL = {
    "OPEN_CANADA": {
        "type": "ckan",
        "portal": "https://open.canada.ca/data/en",
        "fq": 'organization:nrcan-rncan',     # Natural Resources Canada
        "match": {
            "CGMC":        ["geological map compilation"],   # VERIFIED: bedrock, ML-ready rasters+gpkg
            "CDoGS":       ["geochemical surveys", "canadian database of geochemical"],
            "GEOPHYSICS":  ["geophysical data repository", "aeromagnetic", "gravity",
                            "radiometric"],
        },
        "notes": "open.canada.ca is CKAN (GET-only). CGMC dataset id "
                 "d4f80bd3-17e3-a7e8-4bfe-d22a430678d5 ships GeoTIFF rasters + a GPKG "
                 "legend; large. CGMC 610MB GeoTIFF downloaded (2024_CGMC_Lithology_EPSG3978.tif). "
                 "CDoGS = 1300+ regional surveys. "
                 "GEOPHYSICS match finds 5K+ individual aeromagnetic surveys + 4 national "
                 "compilations (Mag 200m/1km, Grav 2km, Rad 250m). National grid FTP dirs "
                 "(Compilations/National_Nationales/) exist but are EMPTY; actual data behind "
                 "GDR web portal at geophysical-data.canada.ca (browser/JS needed for download). "
                 "CGMC GeoTIFF downloaded as 'direct' source. "
                 "Federation means many provincial datasets are ALSO mirrored here under their "
                 "own orgs — a second-pass fq='keywords:geoscience' catches strays.",
    },
}

# ---------------------------------------------------------------------------
# TIER 2 — PROVINCIAL / TERRITORIAL  (granular tenure, drill, assessment PDFs)
# Ordered roughly by exploration activity.
# ---------------------------------------------------------------------------
PROVINCES = {

    # ---- ONTARIO (the tricky one — but data IS downloadable) ----------------
    "ON": {
        "ckan": {
            "type": "ckan",
            "portal": "https://data.ontario.ca",
            "fq": "organization:mines",
            "match": {
                "ON_PUB":   ["publication"],
                "ON_NOEGTS": ["engineering geology terrain", "noegts"],
                "ON_OSTR":   ["surficial terrain"],
            },
            "notes": "ONTARIO CKAN — only metadata/KML here for most datasets. "
                     "Actual data downloads for geoscience datasets are on the "
                     "GeoHub Azure blob store (see direct connector) or via the "
                     "Elasticsearch scroll API (see es_scroll connector). "
                     "AMIS, ODHD, OMI, GEOCHEM, GEOPHYS, OAFD matches removed from CKAN -- "
                     "they only have KML previews; structured data is in es_scroll/direct connectors. "
                     "GEOL_BEDROCK/GEOL_SURFICIAL CKAN ZIP resources point to "
                     "HTML interstitial pages, not direct files.",
        },
        "direct": {
            "type": "direct",
            "portal": "https://www.geologyontario.mines.gov.on.ca",
            "resources": {
                "ON_GEOL_BEDROCK": {
                    "url": "https://prd-0420-geoontario-0000-blob-cge0eud7azhvfsf7.z01.azurefd.net/lrc-geology-documents/publication/MRD126-REV1/MRD126-REV1.zip",
                    "format": "zip",
                    "resource_name": "1:250 000 Scale Bedrock Geology of Ontario (shapefile)",
                    "size": 133479424,
                    "notes": "OGS MRD126-REV1. ~130MB ZIP with ESRI shapefile. Bedrock units, faults, dikes, iron formations.",
                },
                "ON_GEOL_SURFICIAL": {
                    "url": "https://prd-0420-geoontario-0000-blob-cge0eud7azhvfsf7.z01.azurefd.net/lrc-geology-documents/publication/MRD128-REV/MRD128-REV.zip",
                    "format": "zip",
                    "resource_name": "Surficial Geology of Southern Ontario (geodatabase)",
                    "size": 503462912,
                    "notes": "OGS MRD128-REV. ~491MB ZIP file geodatabase. Surficial deposits, linear features.",
                },
                "ON_OMI": {
                    "url": "https://prd-0420-geoontario-0000-blob-cge0eud7azhvfsf7.z01.azurefd.net/lrc-geology-documents/publication/MRD395/MRD395.zip",
                    "format": "zip",
                    "resource_name": "Ontario Mineral Inventory (MRD395)",
                    "size": 6304768,
                    "notes": "OGS MRD395. ~6MB ZIP. Mineral deposit index (MDI) spatial data.",
                },
                "ON_GEOCHEM": {
                    "url": "https://prd-0420-geoontario-0000-blob-cge0eud7azhvfsf7.z01.azurefd.net/lrc-geology-documents/publication/MRD283-REV2/MRD283-REV2.zip",
                    "format": "zip",
                    "resource_name": "Ambient Groundwater Geochemical and Isotopic Data (MRD283-REV2)",
                    "size": None,
                    "notes": "OGS MRD283-REV2. Groundwater geochemistry data. Original MRD283 only has a PDF report.",
                },
                "ON_GEOPHYS": {
                    "url": "https://prd-0420-geoontario-0000-blob-cge0eud7azhvfsf7.z01.azurefd.net/lrc-geology-documents/publication/GDS1036/GDS1036.zip",
                    "format": "zip",
                    "resource_name": "Single Master Gravity and Aeromagnetic Data (Geosoft format)",
                    "size": None,
                    "notes": "OGS GDS1036. Alternative ASCII version at GDS1035. These are the master compilations.",
                },
                "ON_ODHD": {
                    "url": "https://files.ontario.ca/opendata/ontario_borehole_database.zip",
                    "format": "zip",
                    "resource_name": "Ontario Borehole Database",
                    "size": 39238386,
                    "notes": "~39MB ZIP from files.ontario.ca. 126K+ percussion, overburden, sonic, diamond drill holes. "
                             "NOTE: mixes water wells and geotechnical holes with exploration holes — filter on hole "
                             "type before deriving barren negatives. The OMEIS Drill Hole ArcGIS layer (172,259 holes "
                             "with HOLE_TYPE and ELEMENTS) is the better source for exploration work.",
                },
                "ON_MLAS_TENURE": {
                    "url": "https://www.geologyontario.mndm.gov.on.ca/mines/documents/claimaps/mlas_operational_gis_data.zip",
                    "format": "zip",
                    "resource_name": "MLAS Operational GIS Data",
                    "size": 208080564,
                    "notes": "AUTHORITATIVE Ontario tenure. ~208MB ZIP of ESRI shapefiles, regenerated daily, no key. "
                             "Operational_Cell_Claims 401,594 (HOLDER, ISSUE_DATE, ANNIVERSAR, CLAIM_DUE_ all 100% "
                             "populated, 1,403 holders); Cancelled_Claim_Polygons 431,557 (ISSUE_DATE + TERMINATIO from "
                             "2018-04 = unbiased staked-and-dropped history); Mining_Land_Tenure 22,940 (EXPIRY_DAT); "
                             "Non_Mining_Land_Tenure 193,757; Operational_Alienations 16,812; Plans_Permits 799; "
                             "MEM_Boundary_Claims 22,074 + 149,955 points. "
                             "Supersedes the ogsearth KMZ superoverlay, which the province labels 'unofficial ... for "
                             "viewing purposes only', carries no attributes and lists only 202,407 claims. "
                             "HOLDER embeds ownership share as a '(NN) ' prefix — parse it out. "
                             "Cancelled STATUS: only 'Cancelled' (303,138) are drops; 'Amalgamated' (102,966) and "
                             "'Merged' (2,379) are administrative. "
                             "LICENCE: bundled MNDM Electronic Information Products terms are NOT open — commercial use "
                             "and substantial reproduction (explicitly including maps and figures) require prior written "
                             "permission from MNDM. See _Terms of Use.htm in the archive.",
                },
                "ON_MLAS_ADMIN": {
                    "url": "https://www.geologyontario.mndm.gov.on.ca/mines/documents/claimaps/endm_administrative_gis_data.zip",
                    "format": "zip",
                    "resource_name": "MENDM Administrative GIS Data",
                    "size": 639596909,
                    "notes": "~640MB ZIP, static since 2021-06. MENDM_Legacy_Claims 33,439 — the claims live at the "
                             "2018-04-10 map-staking conversion, with DATE_COM back to 1980 (1980s 6,086 / 1990s 2,433 / "
                             "2000s 10,641 / 2010s 14,279). Every row is STATUS='Active' and only 1,175 carry DATE_CNCL: "
                             "it is the SURVIVORS at conversion, so it extends the staking-date record but recovers no "
                             "pre-2018 abandonment — flag anything derived from it survivorship_biased. No owner field. "
                             "Also carries the 5.2M-cell provincial tenure grid (north/south), mining divisions, "
                             "exploration regions, lots and concessions, and the provincial boundary. "
                             "Same MNDM licence terms as ON_MLAS_TENURE.",
                },
            },
            "notes": "ONTARIO DIRECT — Azure blob URLs discovered via GeoHub Angular app API key in main.js. "
                     "Base: prd-0420-geoontario-0000-blob-cge0eud7azhvfsf7.z01.azurefd.net/lrc-geology-documents/publication/. "
                     "These URLs are stable (Azure Front Door CDN). "
                     "ON_AMIS and ON_OAFD have no bulk download — their data is only accessible via the "
                     "GeoHub Elasticsearch API (ws.apis.lrc.gov.on.ca/mndm/geology/search) with scroll, "
                     "or through the old MNDM file system for individual records.",
        },
        "es_scroll": {
            "type": "es_scroll",
            "portal": "https://www.geologyontario.mines.gov.on.ca",
            "endpoint": "https://ws.apis.lrc.gov.on.ca/mndm/geology/search",
            "api_key": "",  # from config.ON_API_KEY / .env — never hardcode
            "page_size": 1000,
            "indexes": {
                "ON_AMIS": {
                    "index": "abandoned-mine",
                    "resource_name": "Abandoned Mines Inventory (AMIS) — full scroll export",
                    "query": {"match_all": {}},
                    "notes": "6,204 abandoned mine records via GeoHub Elasticsearch scroll API. "
                             "No bulk ZIP available. Each record has location, commodity, status, "
                             "closure info, and links to the old MNDM file system.",
                },
                "ON_OAFD": {
                    "index": "assessment",
                    "resource_name": "Online Assessment File Database (OAFD) — full scroll export",
                    "query": {"match_all": {}},
                    "notes": "62,357 assessment report records via GeoHub Elasticsearch scroll API. "
                             "No bulk ZIP. Each record has file_id, NTS sheet, commodities, "
                             "authors, claims, work types, drill hole counts, and links to PDF "
                             "(AFRI system). This is the STRUCTURED METADATA layer; the actual PDFs "
                             "are downloaded by ON_AFRI_PDF scrape connector.",
                },
            },
            "notes": "ONTARIO ES_SCROLL — GeoHub Elasticsearch API w/search_after pagination. "
                     "API key lives in .env as CANADA_GEO_ON_API_KEY (Ontario ships it in the public "
                     "Angular SPA main.js — a rate-limit handle, not our credential). "
                     "Both AMIS and OAFD have no bulk ZIP download; all data is only accessible "
                     "via this scroll API or the old MNDM file system for individual records.",
        },
        "ogsearth": {
            "type": "ogsearth",
            "portal": "https://www.geologyontario.mndm.gov.on.ca/mines/data/google/",
            "base_url": "https://www.geologyontario.mndm.gov.on.ca/mines/data/google/",
            "layers": {
                "ON_CLAIMS2": {
                    "root_kml": "claims2/claimmap/doc.kml",
                    "tile_dir": "claims2/claimmap/files/",
                    "description": "Mining Claims (active) — SuperOverlay KMZ tiles",
                },
                "ON_ALIENATIONS": {
                    "root_kml": "claims2/alienations/doc.kml",
                    "tile_dir": "claims2/alienations/files/",
                    "description": "Alienations (lands withdrawn from staking) — KMZ tiles",
                },
                "ON_DISPOSITIONS": {
                    "root_kml": "claims2/dispositions/doc.kml",
                    "tile_dir": "claims2/dispositions/files/",
                    "description": "Mining Land Tenure dispositions — KMZ tiles",
                },
                "ON_DISPOSITIONS_NONMINING": {
                    "root_kml": "claims2/dispositions_nonmining/doc.kml",
                    "tile_dir": "claims2/dispositions_nonmining/files/",
                    "description": "Non-Mining Land Tenure — KMZ tiles",
                },
                "ON_PLANS_PERMITS": {
                    "root_kml": "claims2/planspermits/doc.kml",
                    "tile_dir": "claims2/planspermits/files/",
                    "description": "Exploration Plans & Permits — KMZ tiles",
                },
            },
            "notes": "OGSEarth SuperOverlay KML tiles for mineral tenure. "
                     "Root doc.kml has 207--394 NetworkLink Region tiles, 0.5deg bbox. "
                     "Each tile ~100--400KB KMZ with 1--6000+ Placemarks (polygon). "
                     "Coverage: lon -95.5 to -76.5, lat 44 to 55. "
                     "~351 claims tiles + ~394 alienations tiles (~250MB total). "
                     "Additional standalone KMLs: AMIS at amis/AMIS.kml, "
                     "OMI at ../../mdi/OMI.kml, boreholes at wells/boreholes.kml, etc.",
        },
        "scrape": {
            "type": "scrape",
            "portal": "https://www.geologyontario.mndm.gov.on.ca",
            "code": "ON_AFRI_PDF",
            "notes": "AFRI report PDFs: each record at /mndmfiles/afri/data/records/<id>.html "
                     "links a PDF. Crawl the OGS GeoData Listing per township for the id set.",
        },
    },

    # ---- BRITISH COLUMBIA -------------------------------------------------
    "BC": {
        "ckan": {
            "type": "ckan",
            "portal": "https://catalogue.data.gov.bc.ca",
            "fq": "tags:mineral OR tags:geology",
            "match": {
                "BC_MTO_CURRENT":  ["mineral, placer and coal titles", "current titles"],
                "BC_MTO_HISTORIC": ["historical titles", "historic mineral"],
                "BC_MINFILE":      ["minfile"],         # mineral occurrence DB
                "BC_GEOL":         ["bedrock geology", "geology of british columbia"],
                "BC_GEOCHEM":      ["regional geochemical", "rgs"],
            },
            "notes": "BC Data Catalogue = CKAN. Bulk files served from pub.data.gov.bc.ca; "
                     "KML from openmaps.gov.bc.ca; WMS via openmaps ows. "
                     "BC_MTA_GRID removed from CKAN match -- CKAN only has KML and 'other' format "
                     "resources (the 'other' FGDB URL at pub.data.gov.bc.ca is in the BC direct connector).",
        },
        "direct": {
            "type": "direct",
            "portal": "https://pub.data.gov.bc.ca",
            "resources": {
                "BC_MTA_GRID": {
                    "url": "https://pub.data.gov.bc.ca/datasets/7dd6cf0d-b980-4a20-9db8-3a84e0b2ecaa/MTA_MINERAL_PLACER_GRID_POLY.gdb.zip",
                    "format": "zip",
                    "resource_name": "MTA Mineral Placer Grid (full province FGDB)",
                    "size": 1500000000,
                    "notes": "~1.5GB file geodatabase ZIP. CKAN resource format='other' so it was filtered out. "
                             "Listed in CKAN package 'mta-mineral-placer-grid' at pub.data.gov.bc.ca.",
                },
            },
            "notes": "Direct downloads from BC Data Catalogue's pub.data.gov.bc.ca. "
                     "These resources have format='other' in CKAN so they'd be filtered by core format check.",
        },
        "wfs": {
            "type": "wfs",
            "portal": "https://openmaps.gov.bc.ca/geo/pub/ows",
            "layers": {
                "BC_MTA_CURRENT":
                    {"url": "https://openmaps.gov.bc.ca/geo/pub/ows",
                     "type_name": "pub:WHSE_MINERAL_TENURE.MTA_ACQUIRED_TENURE_SVW",
                     "sort_by": "OBJECTID",
                     "page_size": 10000},
            },
            "notes": "BC MTA current mineral/placer/coal claims via WFS 2.0 "
                     "(42K polygons). sortBy=OBJECTID for paging. "
                     "CRS=EPSG:3005 (BC Albers).",
        },
        "scrape": {
            "type": "scrape",
            "portal": "https://www2.gov.bc.ca/gov/content/industry/mineral-exploration-mining/"
                      "british-columbia-geological-survey/assessmentreports",
            "code": "BC_ARIS_PDF",
            "notes": "ARIS = Assessment Report Indexing System, 33,500+ reports since 1947. "
                     "PDF + (post-confidentiality) digital data ZIPs. Index/search the ARIS "
                     "system to enumerate report numbers, then fetch per-report PDFs/ZIPs.",
        },
    },

    # ---- QUEBEC -----------------------------------------------------------
    "QC": {
        "donneesquebec": {
            "type": "ckan",
            "portal": "https://www.donneesquebec.ca/recherche",
            "fq": "sigeom-",
            "match": {
                "QC_SIGEOM_GEOPHYS":     ["anomalies électromagnétiques", "geophysique"],
                "QC_SIGEOM_GEOCHRON":    ["géochronologie"],
                "QC_SIGEOM_DRILLHOLES":  ["sondages"],
                "QC_SIGEOM_GRANULATS":   ["granulats"],
                "QC_SIGEOM_TRAVAUX":     ["travaux géoscientifiques"],
                "QC_SIGEOM_TOURBE":      ["milieux tourbeux"],
                "QC_SIGEOM_BEDROCK":     ["géologie du socle"],
                "QC_SIGEOM_GEOCHEM":     ["géochimie"],
                "QC_SIGEOM_QUATERNARY":  ["géologie du quaternaire"],
                "QC_SIGEOM_MINPOT":      ["potentiel minéral"],
                "QC_SIGEOM_IGC":         ["indices, gîtes, mines et carrières"],
                "QC_SIGEOM_MINES":       ["activités minières"],
                "QC_SIGEOM_EXAMINE":     ["document examine"],
            },
            "notes": "13 SIGÉOM bulk data packages on donneesquebec.ca. "
                     "Each has SHP/FGDB/GPKG/CSV downloads from "
                     "gq.mines.gouv.qc.ca/documents/SIGEOM/TOUTQC/FRA/. "
                     "Also has live JSON APIs for mines/projects (79) and "
                     "exploration properties (999) at "
                     "sigeom.mines.gouv.qc.ca/signet/classes/I0000_obtnActiMiniere.",
        },
        "arcgis": {
            "type": "arcgis",
            "portal": "https://sigeom.mines.gouv.qc.ca",
            "items": {},
            "notes": "SIGÉOM = geoscience (SHP/FGDB/GPKG/CSV/KML by theme or NTS 1:50k sheet, "
                     "continuously updated). GESTIM = live mineral tenure (claims/titles). "
                     "GESTIM has no public REST API — only accessible through "
                     "the interactive SIGÉOM web app. "
                     "Bulk downloads preferred over à la carte (see donneesquebec connector).",
        },
    },

    # ---- SASKATCHEWAN -----------------------------------------------------
    "SK": {
        "arcgis_hub": {
            "type": "arcgis",
            "portal": "https://geohub.saskatchewan.ca",
            "rest":   "https://gis.saskatchewan.ca/egis/rest/services/Economy",
            "items": {
                # VERIFIED hub item id for the Mineral Deposits Index (SMDI spatial):
                "SK_SMDI": "2ba80b329aad4018b6eacd56220dc10b",
            },
            "layers": {
                "SK_MINERAL_EXPLORATION":
                    "https://gis.saskatchewan.ca/egis/rest/services/Economy/"
                    "Mineral_Exploration/FeatureServer/1",
            },
            "notes": "NOTE: the Perplexity 'MARS' name is wrong. Correct systems: "
                     "SMDI (deposit index, spatial above) + SMAD (assessment files, PDFs/ZIPs "
                     "via the Mining & Petroleum GeoAtlas ZIP REQUESTS). Hub item download: "
                     "geohub.saskatchewan.ca/api/download/v1/items/<id>/geojson?layers=1 "
                     "(or /csv). All SK datasets come from one Enterprise GIS warehouse.",
        },
        "scrape": {
            "type": "scrape",
            "portal": "https://gisappl.saskatchewan.ca/geoatlas",
            "code": "SK_SMAD_PDF",
            "notes": "SMAD assessment files: select on GeoAtlas, queue under ZIP REQUESTS tab. "
                     "Files named e.g. MAOC_74H-0008_All_<date>.zip.",
        },
    },

    # ---- MANITOBA ---------------------------------------------------------
    "MB": {
        "scrape": {
            "type": "scrape",
            "portal": "https://www.gov.mb.ca/iem/geo/gis/databases.html",
            "code": "MB_MAPGALLERY",
            "notes": "MapGallery GIS portal exposes claims, leases, geophysics, bedrock/"
                     "surficial geology AND lets you spatially query drill holes + assessment "
                     "files together (unlike ON's split ODHD/AFRI). Check for a CKAN mirror "
                     "on open.canada.ca under a Manitoba org first; else crawl MapGallery's "
                     "underlying ArcGIS/WMS services (inspect network calls to get layer URLs). "
                     "No open.canada.ca results for MB mineral claims — data appears to be "
                     "served exclusively through the MapGallery ArcGIS portal.",
        },
    },

    # ---- NEWFOUNDLAND & LABRADOR -----------------------------------------
    "NL": {
        "scrape": {
            "type": "scrape",
            "portal": "https://www.gov.nl.ca/iet/mines/geoscience/geofiles/",
            "code": "NL_GEOFILES_PDF",
            "notes": "GeoFiles = master assessment-report repo back to 1899 (PDF geochem, "
                     "assays, drill logs). Active gold-belt province. Mineral-rights / claims-"
                     "in-good-standing + drilling stats also pushed to open.canada.ca — the "
                     "federal CKAN pass will catch those; GeoFiles PDFs need this scraper. "
                     "No dedicated claims/tenure REST API found — mineral rights data only "
                     "via interactive map at https://gis.gov.nl.ca/minesen/geofiles/.",
        },
    },

    # ---- NOVA SCOTIA ------------------------------------------------------
    "NS": {
        "direct": {
            "type": "direct",
            "portal": "https://novascotia.ca/natr/meb/download/",
            "resources": {
                "NS_MINERAL_RIGHTS_SHP": {
                    "url": "https://novascotia.ca/natr/meb/data/exe/dp493v1sh.ZIP",
                    "format": "shp",
                    "resource_name": "Mineral Rights Database (shapefile)",
                    "notes": "NS mineral rights/claims polygons from DP493.",
                },
                "NS_MINERAL_RIGHTS_GDB": {
                    "url": "https://novascotia.ca/natr/meb/data/exe/dp493v1gd.ZIP",
                    "format": "gdb",
                    "resource_name": "Mineral Rights Database (geodatabase)",
                    "notes": "NS mineral rights/claims file geodatabase.",
                },
            },
            "notes": "NS mineral rights database (claims, leases, licences). "
                     "Download ZIPs from MEB DP493 page.",
        },
        "scrape": {
            "type": "scrape",
            "portal": "https://novascotia.ca/natr/meb/geoscience-online/",
            "code": "NS_DCDH",   # Drillhole & Drill Core DB
            "notes": "NovaROC = mineral rights/claims registry. DCDH = unified drillhole & "
                     "drill core DB derived from assessment reports (company hole numbers, "
                     "tracts, cross-ref to open-file reports). GEOSCAN/open-file PDFs too. "
                     "geoscience-online has download links; check open.canada.ca NS org mirror.",
        },
    },

    # ---- ALBERTA (oil-sands/coal/minerals via AGS) ------------------------
    "AB": {
        "scrape": {
            "type": "scrape",
            "portal": "https://ags.aer.ca/data-maps-models/digital-data",
            "code": "AB_AGS",
            "notes": "Alberta Geological Survey (under AER) publishes digital data, maps, "
                     "models. Mineral assessment reporting is lighter than hard-rock provinces. "
                     "AGS digital-data index is crawlable; many items also on open.canada.ca. "
                     "No public mineral claims/tenure API — AB mineral surface rights are "
                     "administered through the Alberta Energy Regulator (not public geospatial).",
        },
    },

    # ---- NEW BRUNSWICK ----------------------------------------------------
    "NB": {
        "ckan": {
            "type": "ckan",
            "portal": "https://open.canada.ca/data/en",
            "fq": "organization:nb",
            "match": {
                "NB_MINERAL_CLAIMS":    ["mineral claims"],
                "NB_REPORTS_OF_WORK":   ["reports of work"],
                "NB_MINERAL_OCCURRENCE": ["mineral occurrence"],
                "NB_DRILLHOLE":         ["drillhole"],
                "NB_EXPLORATION_TRENCHES": ["trenches"],
                "NB_MPS":               ["minerals and petroleum sections"],
            },
            "notes": "NB data on Socrata (gnb.socrata.com), mirrored through "
                     "open.canada.ca. Resources include CSV, GeoJSON, Shapefile, KML. "
                     "GeoJSON download uses gnb.socrata.com geospatial export API.",
        },
        "scrape": {
            "type": "scrape",
            "portal": "https://www2.gnb.ca/content/gnb/en/departments/natural_resources/"
                      "Minerals/content/Geoscience.html",
            "code": "NB_GEOSCIDB",
            "notes": "NB GeoSCImap / Geoscience Database: drill holes, assessment files (DIGHEM "
                     "etc.), bedrock geology, claims via NB Mining mapviewer. ArcGIS-backed; "
                     "inspect mapviewer for layer REST URLs. Also mirrored to open.canada.ca.",
        },
    },

    # ---- YUKON ------------------------------------------------------------
    "YT": {
        "arcgis": {
            "type": "arcgis",
            "portal": "https://data.geology.gov.yk.ca",
            "layers": {
                "YT_QUARTZ_CLAIMS":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/36",
                "YT_PLACER_CLAIMS":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/11",
                "YT_MINERAL_CLAIMS_POLY":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/51",
                "YT_MINERAL_CLAIMS_LINE":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/52",
                "YT_HISTORICAL_CLAIMS":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/77",
                "YT_CROWN_GRANTS":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/67",
                "YT_PLACER_LEASES":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/12",
                "YT_PLACER_GROUPING":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/75",
                "YT_QUARTZ_GROUPING":
                    "https://mapservices.gov.yk.ca/arcgis/rest/services/"
                    "GeoYukon/GY_Mining/MapServer/76",
            },
            "notes": "Yukon Geological Survey: 9 MapServer layers for mineral "
                     "tenure (quartz claims, placer claims, surveyed polygon/line, "
                     "historical, crown grants, leases, groupings). High exploration "
                     "activity — prioritize.",
        },
    },

    # ---- NORTHWEST TERRITORIES & NUNAVUT (NTGS) ---------------------------
    "NT_NU": {
        "direct": {
            "type": "direct",
            "portal": "https://www.geomatics.gov.nt.ca",
            "resources": {
                "NT_MINERAL_CLAIMS": {
                    "url": "https://www.geomatics.gov.nt.ca/Downloads/Vector/Mineral_Tenure/MineralClaims.zip",
                    "format": "zip",
                    "resource_name": "Mineral Claims (NT)",
                    "size": 12658,
                    "notes": "NT mineral claims ZIP from NWT Geomatics (~12KB). Redirect target from "
                             "/en/mineral-claims. Small size is expected (NT is data-sparse).",
                },
            },
            "notes": "NT mineral claims ZIP download from NWT Geomatics. "
                     "Redirect target is the actual ZIP at /Downloads/Vector/Mineral_Tenure/MineralClaims.zip.",
        },
        "arcgis": {
            "type": "arcgis",
            "portal": "https://geo.sac-isc.gc.ca/geomatics/rest/services/"
                      "Donnees_Ouvertes-Open_Data",
            "layers": {
                "NU_MINERAL_CLAIMS":
                    "https://geo.sac-isc.gc.ca/geomatics/rest/services/"
                    "Donnees_Ouvertes-Open_Data/"
                    "Claim_minier_NU_Mineral_Claim/MapServer/0",
                "NU_MINING_LEASES":
                    "https://geo.sac-isc.gc.ca/geomatics/rest/services/"
                    "Donnees_Ouvertes-Open_Data/"
                    "Bail_minier_NU_Mining_Lease/MapServer/0",
                "NU_PROSPECTING_PERMITS":
                    "https://geo.sac-isc.gc.ca/geomatics/rest/services/"
                    "Donnees_Ouvertes-Open_Data/"
                    "Permis_exploration_NU_Prospecting_Permit/MapServer/0",
            },
            "notes": "NU mineral tenure from Crown-Indigenous Relations (CIRNAC). "
                     "34411 claims, 893 leases, 2600 permits. "
                     "Also available as CSV exports from the same server.",
        },
        "scrape": {
            "type": "scrape",
            "portal": "https://www.nwtgeoscience.ca",
            "code": "NTGS",
            "notes": "Northwest Territories Geological Survey hosts the NWT/NU assessment "
                     "report database, drill core, geochem, geophysics. Nunavut mineral data "
                     "also via CanNor / NRCan. Federal CKAN pass catches the national layers.",
        },
    },
}

# ---------------------------------------------------------------------------
# US FEDERAL DATA SOURCES (non-Canadian, included for reference)
# ---------------------------------------------------------------------------
US_SOURCES = {
    "MRDS": {
        "type": "direct",
        "portal": "https://mrdata.usgs.gov",
        "resources": {
            "MRDS_CSV": {
                "url": "https://mrdata.usgs.gov/mrds/mrds.csv",
                "format": "csv",
                "resource_name": "MRDS Global Mineral Sites CSV",
                "notes": "~120K mineral deposit/occurrence records worldwide. "
                         "Point locations. NOT mining claims boundaries.",
            },
        },
    },
    "USMIN": {},
}
_usmin_states = ["AK","AL","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI",
                  "IA","ID","IL","IN","KS","KY","LA","MA","MD","ME","MI","MN",
                  "MO","MS","MT","NC","ND","NE","NH","NJ","NM","NV","NY","OH",
                  "OK","OR","PA","RI","SC","SD","TN","TX","UT","VA","VT","WA",
                  "WI","WV","WY"]
US_SOURCES["USMIN"] = {
    "type": "direct",
    "portal": "https://mrdata.usgs.gov/usmin",
    "resources": {
        f"USMIN_{st}": {
            "url": f"https://mrdata.usgs.gov/usmin/state/usmin-{st}.zip",
            "format": "zip",
            "resource_name": f"USMIN {st} mineral site shapefile",
        }
        for st in _usmin_states
    },
    "notes": "Per-state ZIPs with point + polygon shapefiles. "
             "POLYGON = mine/site outlines. POINT = occurrences. "
             "NOT mining claims boundaries. BLM Hub at "
             "gbp-blm-egis.hub.arcgis.com has no mining claim datasets.",
}

# ---------------------------------------------------------------------------
# Convenience: flat iterator over every declared connector.
# ---------------------------------------------------------------------------
def iter_connectors():
    """Yield (jurisdiction, connector_key, spec) for everything in the registry."""
    for code, spec in FEDERAL.items():
        yield ("FED", code, spec)
    for prov, conns in PROVINCES.items():
        for key, spec in conns.items():
            yield (prov, key, spec)
    for code, spec in US_SOURCES.items():
        yield ("US", code, spec)
