"""
connectors/scrape.py — base + registry for legacy assessment-report PDF systems.

These systems (AFRI/ON, ARIS/BC, SMAD/SK, GeoFiles/NL, DCDH/NS, AGS/AB, NTGS) don't
expose a clean bulk API; each needs a small bespoke crawler to (1) enumerate report
IDs and (2) map each ID to a PDF/ZIP URL. Rather than fake those URLs, this module
provides a uniform PdfScraper interface and per-code stubs you finish once by
inspecting each site's network calls. Each stub's job is just:

    enumerate_ids() -> list[str]
    pdf_url(report_id) -> str

The harvester then downloads + ledgers them exactly like CKAN resources.

Why stubs and not hardcoded URLs: these portals are JS apps / search forms whose exact
query endpoints aren't publicly documented and shift. Finishing a stub is ~15 min with
browser devtools open; the structure here makes that the only manual step.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class PdfScraper:
    code: str
    portal: str
    notes: str = ""
    # Filled in by subclass / configured closures:
    enumerate_ids = None     # callable -> list[str]
    pdf_url = None           # callable(report_id) -> url

    def ready(self) -> bool:
        return callable(self.enumerate_ids) and callable(self.pdf_url)


# Registry keyed by the CODE used in sources.py scrape specs.
SCRAPERS: dict[str, PdfScraper] = {}


def register(code: str, portal: str, notes: str = ""):
    s = PdfScraper(code=code, portal=portal, notes=notes)
    SCRAPERS[code] = s
    return s


# ---------------------------------------------------------------------------
# Stubs — finish enumerate_ids / pdf_url for the jurisdictions you want PDFs from.
# Hints reflect the verified site structure for each system.
# ---------------------------------------------------------------------------

# ONTARIO AFRI — record pages at:
#   /mndmfiles/afri/data/records/<AFRI_ID>.html  (links the PDF)
# Enumerate <AFRI_ID> from the OGS GeoData Listing (per township) or the OAFD CKAN
# index resource (it carries AFRI numbers + a PDF link column).
_on = register("ON_AFRI_PDF",
               "https://www.geologyontario.mndm.gov.on.ca",
               "Enumerate AFRI ids from OAFD CKAN index column; PDF link is on each "
               "/mndmfiles/afri/data/records/<id>.html page.")

# BC ARIS — search system returns report numbers; report pages link PDF + data ZIP.
_bc = register("BC_ARIS_PDF",
               "https://www2.gov.bc.ca/gov/content/industry/mineral-exploration-mining/"
               "british-columbia-geological-survey/assessmentreports",
               "Enumerate report numbers via ARIS search (33,500+ since 1947); fetch per-"
               "report PDF and, post-confidentiality, the digital-data ZIP.")

# SASKATCHEWAN SMAD — GeoAtlas ZIP REQUESTS; files named MAOC_<NTS>-<n>_All_<date>.zip
_sk = register("SK_SMAD_PDF",
               "https://gisappl.saskatchewan.ca/geoatlas",
               "Enumerate assessment-file ids from the SMDI/GeoAtlas mineral-exploration "
               "layer (it cross-refs file numbers); request ZIPs via the ZIP REQUESTS flow.")

# NEWFOUNDLAND GeoFiles — assessment reports back to 1899.
_nl = register("NL_GEOFILES_PDF",
               "https://www.gov.nl.ca/iet/mines/geoscience/geofiles/",
               "GeoFiles search lists report ids -> PDF links. Cross-ref claims/drilling "
               "stats from the open.canada.ca NL mirror caught by the federal CKAN pass.")

# NOVA SCOTIA DCDH / open-file PDFs.
_ns = register("NS_DCDH",
               "https://novascotia.ca/natr/meb/geoscience-online/",
               "DCDH drillhole/core DB + open-file report PDFs. NovaROC for claims. Check "
               "open.canada.ca NS org mirror for structured drillhole CSV before scraping.")

# ALBERTA AGS digital data index.
_ab = register("AB_AGS",
               "https://ags.aer.ca/data-maps-models/digital-data",
               "Crawl the AGS digital-data listing for item pages -> file downloads.")

# NEW BRUNSWICK geoscience database.
_nb = register("NB_GEOSCIDB",
               "https://www2.gnb.ca/content/gnb/en/departments/natural_resources/Minerals/"
               "content/Geoscience.html",
               "NB mining mapviewer is ArcGIS-backed; prefer pulling its REST layers (drill "
               "holes, assessment index) over PDF scraping where possible.")

# NWT/NU geoscience.
_nt = register("NTGS",
               "https://www.nwtgeoscience.ca",
               "NWT/NU assessment report DB, drill core, geochem. Enumerate via the NTGS "
               "online database search.")
