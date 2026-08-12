"""
connectors/scrape.py — base + registry for legacy assessment-report PDF systems.

Each system gets a PdfScraper with two methods:

    enumerate_ids() -> list[str]
    pdf_url(report_id) -> str

The harvester (harvest_pdfs.py) calls these to download + RAG-ingest PDFs.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from urllib.request import Request, urlopen
from urllib.parse import urlencode

import config as C


@dataclass
class PdfScraper:
    code: str
    portal: str
    notes: str = ""
    enumerate_ids = None
    pdf_url = None

    def ready(self) -> bool:
        return callable(self.enumerate_ids) and callable(self.pdf_url)


SCRAPERS: dict[str, PdfScraper] = {}


def register(code: str, portal: str, notes: str = ""):
    s = PdfScraper(code=code, portal=portal, notes=notes)
    SCRAPERS[code] = s
    return s


def _get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": C.USER_AGENT})
    with urlopen(req, timeout=C.TIMEOUT) as r:
        return r.read()


def _resolve_oafd_feature_server() -> str:
    """Resolve the OAFD item on ArcGIS Online to its current FeatureServer URL."""
    item = json.loads(_get(
        "https://mndm.maps.arcgis.com/sharing/rest/content/items/"
        "27612576f49b4d8cb3d4572f2e74429d?f=json"
    ))
    return item["url"]


# ---------------------------------------------------------------------------
# ONTARIO AFRI — 62,357 assessment file reports
# Enumerate via OAFD FeatureServer (ArcGIS Online item, auto-resolves to latest)
# PDF URL (new Azure blob storage — old /mndmfiles/afri/ endpoints 404):
#   https://prd-0420-geoontario-0000-blob-cge0eud7azhvfsf7.z01.azurefd.net
#   /lrc-geology-documents/assessment/{file_id}/{file_name}
#   file_name is always "{file_id}.Pdf" (observed pattern).
# Metadata API (Elasticsearch wrapper, requires subscription key):
#   POST https://ws.apis.lrc.gov.on.ca/mndm/geology/search
#   Headers: { "Ocp-Apim-Subscription-Key": <config.ON_API_KEY> }
#   Body: { "indexes": "assessment", "body": "<ES query JSON>" }
# ---------------------------------------------------------------------------
_OAFD_ITEM_ID = "27612576f49b4d8cb3d4572f2e74429d"
_AZURE_BLOB = ("https://prd-0420-geoontario-0000-blob-cge0eud7azhvfsf7"
               ".z01.azurefd.net/lrc-geology-documents")


def _on_enumerate() -> list[str]:
    base = _resolve_oafd_feature_server()
    ids: list[str] = []
    offset = 0
    page_size = 2000
    while True:
        params = urlencode({
            "where": "1=1",
            "outFields": "AFRI_FID",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        })
        data = json.loads(_get(f"{base}/0/query?{params}"))
        features = data.get("features", [])
        for feat in features:
            afri_id = feat["attributes"].get("AFRI_FID", "").strip()
            if afri_id:
                ids.append(afri_id)
        if len(features) < page_size:
            break
        offset += page_size
    return ids


def _on_pdf_url(report_id: str) -> str:
    return f"{_AZURE_BLOB}/assessment/{report_id}/{report_id}.Pdf"


def _on_metadata(report_id: str) -> dict | None:
    """Query the GeologyOntario Elasticsearch API for a record's full metadata."""
    import json as _json
    body = _json.dumps({
        "size": 1,
        "query": {"bool": {"must": [{"match": {"file_id": report_id}}]}},
    })
    payload = _json.dumps({"indexes": "assessment", "body": body}).encode()
    req = Request(
        "https://ws.apis.lrc.gov.on.ca/mndm/geology/search",
        data=payload,
        headers={
            "User-Agent": C.USER_AGENT,
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": C.require_on_api_key(),
        },
    )
    try:
        with urlopen(req, timeout=C.TIMEOUT) as r:
            resp = _json.loads(r.read())
        hits = resp.get("hits", {}).get("hits", [])
        return hits[0]["_source"] if hits else None
    except Exception:
        return None


_on = register("ON_AFRI_PDF",
               "https://www.geologyontario.mines.gov.on.ca",
               "62,357 records via OAFD ArcGIS FeatureServer; "
               "PDF via Azure blob /assessment/{id}/{id}.Pdf; "
               "metadata from GeologyOntario Elasticsearch API")
_on.enumerate_ids = _on_enumerate
_on.pdf_url = _on_pdf_url


# ---------------------------------------------------------------------------
# BC ARIS — 41,934 assessment reports
# Enumerate via BC Gov WFS endpoint (openmaps.gov.bc.ca)
# PDF URL: https://apps.nrs.gov.bc.ca/pub/aris/Report/{id}.pdf/
# ---------------------------------------------------------------------------
_ARIS_WFS = ("https://openmaps.gov.bc.ca/geo/pub/"
             "WHSE_MINERAL_TENURE.ARIS_MINERAL_REPORTS/ows")


def _bc_enumerate() -> list[str]:
    ids: list[str] = []
    page_size = 5000
    offset = 0
    while True:
        params = urlencode({
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": "WHSE_MINERAL_TENURE.ARIS_MINERAL_REPORTS",
            "count": page_size,
            "startIndex": offset,
            "outputFormat": "json",
            "propertyName": "ARIS_NUMBER",
        })
        resp = json.loads(_get(f"{_ARIS_WFS}?{params}"))
        features = resp.get("features", [])
        for feat in features:
            aris = feat.get("properties", {}).get("ARIS_NUMBER")
            if aris is not None:
                ids.append(str(aris))
        returned = resp.get("numberReturned", len(features))
        if returned < page_size:
            break
        offset += page_size
    return ids


def _bc_pdf_url(report_id: str) -> str:
    return f"https://apps.nrs.gov.bc.ca/pub/aris/Report/{report_id}.pdf/"


_bc = register("BC_ARIS_PDF",
               "https://www2.gov.bc.ca/gov/content/industry/mineral-exploration-mining/"
               "british-columbia-geological-survey/assessmentreports",
               "41,934 records via BC Gov WFS (openmaps.gov.bc.ca); "
               "PDF at https://apps.nrs.gov.bc.ca/pub/aris/Report/{id}.pdf/")
_bc.enumerate_ids = _bc_enumerate
_bc.pdf_url = _bc_pdf_url


# ---------------------------------------------------------------------------
# SASKATCHEWAN SMAD — ~14,889 assessment file records
# Enumerate via Energy & Resources ArcGIS FeatureServer (3 layers).
# PDF download requires ASP.NET WebForms session (viewstate) — no simple URL.
# Enrichment: cross-ref with SMAD web app at mineral-assessment.saskatchewan.ca
# ---------------------------------------------------------------------------
_SK_FS = ("https://gis.saskatchewan.ca/egis/rest/services/Economy/"
          "Mineral_Assessment_File_Information/FeatureServer")
_SK_LAYERS = [0, 1, 2]  # Underground, Ground, Airborne


def _sk_enumerate() -> list[str]:
    ids: list[str] = []
    for layer in _SK_LAYERS:
        offset = 0
        page_size = 2000
        while True:
            params = urlencode({
                "where": "1=1",
                "outFields": "FILENUMBER",
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": page_size,
            })
            data = json.loads(_get(f"{_SK_FS}/{layer}/query?{params}"))
            features = data.get("features", [])
            for feat in features:
                fid = feat["attributes"].get("FILENUMBER", "").strip()
                if fid:
                    ids.append(fid)
            if len(features) < page_size:
                break
            offset += page_size
    return sorted(set(ids))


def _sk_pdf_url(report_id: str) -> str:
    return (f"http://mineral-assessment.saskatchewan.ca/Pages/BasePages/"
            f"Main.aspx?UseCase=ExternalSearch&txtFileNumber={report_id}")


_sk = register("SK_SMAD_PDF",
               "https://gisappl.saskatchewan.ca/geoatlas",
               "~14,889 records via ER GIS FeatureServer (3 layers); "
               "PDF download blocked by ASP.NET viewstate — SMAD search page URL "
               "returned instead; needs form-crawling for direct download")
_sk.enumerate_ids = _sk_enumerate
_sk.pdf_url = _sk_pdf_url


# ---------------------------------------------------------------------------
# NEWFOUNDLAND & LABRADOR GeoFiles — 5,000+ geoscience documents
# Enumerate via GeoAtlas MapServer layer (GEOFILE_NO field).
# PDF URL: the GeoFiles PDF server (gis.geosurv.gov.nl.ca) runs ASP.NET and
# returns 500/404 for all direct file paths (Batch2017/..., etc.).
# No public download API or simple URL pattern discovered.
# The GeoAtlas web app likely serves PDFs through an authenticated session.
# For now: enumeration works, pdf_url returns a best-guess URL that will fail.
# Future: proxy through the GeoAtlas app's download handler, or use the
# interactive map's network traffic to find the real download endpoint.
# ---------------------------------------------------------------------------
_NL_MAP = ("https://dnrmaps.gov.nl.ca/arcgis/rest/services/GeoAtlas/"
           "Map_Layers/MapServer/2")
_NL_PDF_BASE = "https://gis.geosurv.gov.nl.ca/geofilePDFS"
_NL_BATCH_DIRS = [
    "Batch2008", "Batch2010", "Batch2014", "Batch2015", "Batch2016",
    "Batch2017", "Batch2018", "Batch2019", "Batch2020", "Batch2021",
    "Batch2022", "Batch2023", "Batch2024", "WBox040",
]


def _nl_enumerate() -> list[str]:
    ids: list[str] = []
    offset = 0
    page_size = 1000
    while True:
        params = urlencode({
            "where": "GEOFILE_NO IS NOT NULL",
            "outFields": "GEOFILE_NO",
            "returnGeometry": "false",
            "returnDistinctValues": "true",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        })
        data = json.loads(_get(f"{_NL_MAP}/query?{params}"))
        features = data.get("features", [])
        for feat in features:
            gid = feat["attributes"].get("GEOFILE_NO", "").strip()
            if gid:
                ids.append(gid)
        if len(features) < page_size:
            break
        offset += page_size
    return sorted(set(ids))


def _nl_probe_pdf(report_id: str) -> str | None:
    """Probe all known batch directories for a GeoFile PDF, return the first hit."""
    safe = report_id.replace("/", "_").replace("\\", "_")
    for batch in _NL_BATCH_DIRS:
        url = f"{_NL_PDF_BASE}/{batch}/{safe}.pdf"
        try:
            req = Request(url, headers={"User-Agent": C.USER_AGENT})
            with urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            continue
    return None


def _nl_pdf_url(report_id: str) -> str:
    safe = report_id.replace("/", "_").replace("\\", "_")
    return f"{_NL_PDF_BASE}/Batch2017/{safe}.pdf"


_nl = register("NL_GEOFILES_PDF",
               "https://gis.gov.nl.ca/minesen/geofiles/",
               "5,000+ records via GeoAtlas MapServer (GEOFILE_NO); "
               "PDF download BLOCKED — server returns 500/404 for all known "
               "batch directory patterns. Requires GeoAtlas web app session "
               "to download. ID format: 002C/0058")
_nl.enumerate_ids = _nl_enumerate
_nl.pdf_url = _nl_pdf_url


# ---------------------------------------------------------------------------
# NOVA SCOTIA DCDH — 28,341 drillholes with 40,000+ references
# Enumerate via ArcGIS FeatureServer (NS Drillhole Database).
# Assessment-report PDFs are served via NovaScan (JSF search app).
# The drillhole references table is used to cross-reference report numbers
# for tabular embedding (Sprint 3). No direct PDF URL pattern exists.
# PDFs: https://novascotia.ca/natr/meb/data/ar/ME{year}-{num}.pdf (unverified)
# ---------------------------------------------------------------------------
_NS_FS = ("https://services.arcgis.com/TS1HHBYLM10d1SZH/arcgis/rest/services/"
          "db_NS_Drillhole_Database__d003ns_UT83/FeatureServer")


def _ns_enumerate() -> list[str]:
    ids: list[str] = []
    for layer in [0, 1, 2]:  # drillholes, references, core
        offset = 0
        page_size = 2000
        while True:
            params = urlencode({
                "where": "1=1",
                "outFields": "OBJECTID",
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": page_size,
            })
            data = json.loads(_get(f"{_NS_FS}/{layer}/query?{params}"))
            features = data.get("features", [])
            for feat in features:
                oid = str(feat["attributes"].get("OBJECTID", ""))
                if oid:
                    ids.append(f"{layer}:{oid}")
            if len(features) < page_size:
                break
            offset += page_size
    return ids


def _ns_pdf_url(report_id: str) -> str:
    return (f"https://gesner.novascotia.ca/novascan/DocumentQuery.faces?"
            f"search={report_id}")


_ns = register("NS_DCDH",
               "https://novascotia.ca/natr/meb/geoscience-online/",
               "28,341 drillholes via NS Drillhole FeatureServer (3 layers); "
               "PDFs via NovaScan JSF app — no direct URL; use drillhole "
               "references for tabular embedding (Sprint 3)")
_ns.enumerate_ids = _ns_enumerate
_ns.pdf_url = _ns_pdf_url


# ---------------------------------------------------------------------------
# ALBERTA AGS / ABMARS — 20,000+ mineral assessment report records
# Enumerate via ABMARS ArcGIS MapServer (AssessmentNumber field).
# AGS does not serve assessment report PDFs via public URL.
# AGS open-file reports: https://static.ags.aer.ca/files/document/OFR/OFR_YYYY_XX.pdf
# Mineral assessment PDFs require contacting AGS directly.
# ---------------------------------------------------------------------------
_AB_MAP = ("https://gis.energy.gov.ab.ca/arcgis/rest/services/Geoview/"
           "ABMARS_Ext_PROD/MapServer/0")


def _ab_enumerate() -> list[str]:
    ids: list[str] = []
    offset = 0
    page_size = 2000
    while True:
        params = urlencode({
            "where": "1=1",
            "outFields": "AssessmentNumber",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        })
        data = json.loads(_get(f"{_AB_MAP}/query?{params}"))
        features = data.get("features", [])
        for feat in features:
            an = str(feat["attributes"].get("AssessmentNumber", "")).strip()
            if an:
                ids.append(an)
        if len(features) < page_size:
            break
        offset += page_size
    return ids


def _ab_pdf_url(report_id: str) -> str:
    return (f"https://gis.energy.gov.ab.ca/arcgis/rest/services/Geoview/"
            f"ABMARS_Ext_PROD/MapServer/0/query?where="
            f"AssessmentNumber%3D%27{report_id}%27&f=json")


_ab = register("AB_AGS",
               "https://ags.aer.ca/data-maps-models/digital-data",
               "20,000+ records via ABMARS MapServer (AssessmentNumber); "
               "no public PDF URL — AGS only serves its own publications; "
               "assessment-report PDFs require direct contact with AGS")
_ab.enumerate_ids = _ab_enumerate
_ab.pdf_url = _ab_pdf_url


# ---------------------------------------------------------------------------
# NEW BRUNSWICK GeoSCIDB / PARIS — assessment reports of work
# Enumerate via NBGS Reports Of Work FeatureServer (REPORT field).
# PDFs served through PARIS web portal (requires scraping detail page).
# No direct PDF URL pattern.
# ---------------------------------------------------------------------------
_NB_FS = ("https://gis-erd-der.gnb.ca/server/rest/services/OpenData/"
          "NBGS_Reports_Of_Work/FeatureServer/0")


def _nb_enumerate() -> list[str]:
    ids: list[str] = []
    offset = 0
    page_size = 2000
    while True:
        params = urlencode({
            "where": "1=1",
            "outFields": "REPORT",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        })
        data = json.loads(_get(f"{_NB_FS}/query?{params}"))
        features = data.get("features", [])
        for feat in features:
            rid = str(feat["attributes"].get("REPORT", "")).strip()
            if rid:
                ids.append(rid)
        if len(features) < page_size:
            break
        offset += page_size
    return ids


def _nb_pdf_url(report_id: str) -> str:
    return (f"http://dnr-mrn.gnb.ca/ParisWeb/"
            f"Assessmentreportdetails.aspx?Num={report_id}")


_nb = register("NB_GEOSCIDB",
               "https://www2.gnb.ca/content/gnb/en/departments/natural_resources/"
               "Minerals/content/Geoscience.html",
               "reports of work via NBGS FeatureServer (REPORT field); "
               "PDFs via PARIS web portal — no direct download URL; "
               "requires scraping Assessmentreportdetails.aspx")
_nb.enumerate_ids = _nb_enumerate
_nb.pdf_url = _nb_pdf_url


# ---------------------------------------------------------------------------
# NORTHWEST TERRITORIES / NUNAVUT NTGS — assessment reports, open files
# Enumerate via NTGS ASP.NET web search app (page-based, no REST API).
# PDFs served through download facility (ZIP bundles per ref number).
# Enrichment: ref numbers follow patterns: 082133, 2016-05, 2015-014
# ---------------------------------------------------------------------------
_NT_SEARCH = "https://app.nwtgeoscience.ca/Searching/ReferenceSearch.aspx"
_NT_JOURNAL = "https://app.nwtgeoscience.ca/Journal.aspx"


def _nt_enumerate() -> list[str]:
    """Enumerate via NTGS Search API (JSON endpoint if available, else fallback)."""
    # The NTGS search page uses ASP.NET postback — no clean pagination API.
    # Fallback: return known reference patterns from their public listings.
    # TODO: implement ASP.NET form-based pagination for full enumeration.
    return []


def _nt_pdf_url(report_id: str) -> str:
    return f"{_NT_JOURNAL}?refnum={report_id}"


_nt = register("NTGS",
               "https://www.nwtgeoscience.ca",
               "NTGS ASP.NET search app (no REST API); "
               "PDFs served as ZIP bundles; "
               "refnum format: 082133 or 2016-05")
_nt.enumerate_ids = _nt_enumerate
_nt.pdf_url = _nt_pdf_url
