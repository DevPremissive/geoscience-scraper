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
            "NATIONAL_TENURE": ["mineral tenure", "mineral claims"],  # composite tenure layer
            "MINERAL_DEPOSITS": ["mineral deposits", "national mineral"],
        },
        "notes": "open.canada.ca is CKAN (GET-only). CGMC dataset id "
                 "d4f80bd3-17e3-a7e8-4bfe-d22a430678d5 ships GeoTIFF rasters + a GPKG "
                 "legend; large. CDoGS = 1300+ regional surveys. Federation means many "
                 "provincial datasets are ALSO mirrored here under their own orgs — a "
                 "second-pass fq='keywords:geoscience' catches strays.",
    },
}

# ---------------------------------------------------------------------------
# TIER 2 — PROVINCIAL / TERRITORIAL  (granular tenure, drill, assessment PDFs)
# Ordered roughly by exploration activity.
# ---------------------------------------------------------------------------
PROVINCES = {

    # ---- ONTARIO (the original target) ------------------------------------
    "ON": {
        "ckan": {
            "type": "ckan",
            "portal": "https://data.ontario.ca",
            "fq": "organization:mines",
            "match": {
                "ON_ODHD": ["drill hole"],
                "ON_OAFD": ["assessment file"],
                "ON_OMI":  ["mineral inventory", "mineral deposit"],
                "ON_AMIS": ["abandoned mines"],
                "ON_PUB":  ["publication"],
                "ON_GEOL_BEDROCK":   ["bedrock geology"],
                "ON_GEOL_SURFICIAL": ["surficial geology"],
                "ON_NOEGTS": ["engineering geology terrain", "noegts"],
                "ON_OSTR":   ["surficial terrain"],
                "ON_GEOPHYS": ["geophysical", "aeromagnetic", "gravity"],
                "ON_GEOCHEM": ["lake geochemistry", "geochemistry"],
            },
            "notes": "OGSEarth direct KML/zip links rotate MONTHLY — always resolve via "
                     "CKAN. AFRI/PUB are the PDF NLP corpus (30-80GB).",
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
                "BC_MTA_GRID":     ["mineral placer grid", "mta - mineral"],
                "BC_MINFILE":      ["minfile"],         # mineral occurrence DB
                "BC_GEOL":         ["bedrock geology", "geology of british columbia"],
                "BC_GEOCHEM":      ["regional geochemical", "rgs"],
            },
            "notes": "BC Data Catalogue = CKAN. Bulk files served from pub.data.gov.bc.ca; "
                     "KML from openmaps.gov.bc.ca; WMS via openmaps ows. MTA grid is too "
                     "large for whole-province download — CHUNK by NTS mapsheet / AOI.",
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
        "arcgis": {
            "type": "arcgis",
            "portal": "https://sigeom.mines.gouv.qc.ca",
            "items": {},   # SIGÉOM à la carte; see notes (themed/NTS download tool)
            "notes": "SIGÉOM = geoscience (SHP/FGDB/GPKG/CSV/KML by theme or NTS 1:50k sheet, "
                     "continuously updated). GESTIM = live mineral tenure (claims/titles). "
                     "SIGÉOM 'à la carte' download is per-NTS-sheet; iterate the QC NTS sheet "
                     "list. Much content is French — translate fields downstream.",
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
                     "underlying ArcGIS/WMS services (inspect network calls to get layer URLs).",
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
                     "federal CKAN pass will catch those; GeoFiles PDFs need this scraper.",
        },
    },

    # ---- NOVA SCOTIA ------------------------------------------------------
    "NS": {
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
                     "AGS digital-data index is crawlable; many items also on open.canada.ca.",
        },
    },

    # ---- NEW BRUNSWICK ----------------------------------------------------
    "NB": {
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
            "layers": {},
            "notes": "Yukon Geological Survey: very rich open data (claims via Yukon mining "
                     "recorder, MINFILE-equivalent occurrences, assessment reports, geochem, "
                     "geophysics). Largely ArcGIS REST + direct file downloads at "
                     "data.geology.gov.yk.ca. High exploration activity — prioritize.",
        },
    },

    # ---- NORTHWEST TERRITORIES & NUNAVUT (NTGS) ---------------------------
    "NT_NU": {
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
# Convenience: flat iterator over every declared connector.
# ---------------------------------------------------------------------------
def iter_connectors():
    """Yield (jurisdiction, connector_key, spec) for everything in the registry."""
    for code, spec in FEDERAL.items():
        yield ("FED", code, spec)
    for prov, conns in PROVINCES.items():
        for key, spec in conns.items():
            yield (prov, key, spec)
