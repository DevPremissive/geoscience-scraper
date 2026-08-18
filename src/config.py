"""
Central configuration for the Canada-wide geoscience/mining data lake.

The WHAT (which sources) lives in sources.py as a declarative registry.
This file holds the WHERE (paths) and HOW (politeness, formats) knobs.
"""
from __future__ import annotations
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Read REPO_ROOT/.env into os.environ. Real env vars always win.

    Deliberately dependency-free — this runs before anything else is imported.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("'\""))


_load_dotenv()

# The lake is deliberately split across two volumes:
#   BULK  (raw/, pdfs/, processed/) — external drive. Large, append-mostly,
#         re-downloadable. Override with env var CANADA_GEO_LAKE.
#   INDEX (duckdb / sqlite / chroma) — local NVMe. Small, random-IO heavy, wants
#         a journalling filesystem. Rebuildable from BULK. Override with
#         env var CANADA_GEO_INDEX.
LAKE_ROOT  = Path(os.environ.get("CANADA_GEO_LAKE",  "/media/vis/Expansion/canada-geo-lake"))
INDEX_ROOT = Path(os.environ.get("CANADA_GEO_INDEX", str(Path.home() / "infra" / "canada-geo-lake-data")))

RAW_DIR       = LAKE_ROOT / "raw"        # raw/<JURISDICTION>/<CODE>/<YYYY-MM-DD>/
PDF_DIR       = LAKE_ROOT / "pdfs"       # pdfs/<JURISDICTION>/<CODE>/<id>.pdf
PROCESSED_DIR = LAKE_ROOT / "processed"
TABLES_DIR    = PROCESSED_DIR / "tables"
GPKG_PATH     = PROCESSED_DIR / "geo.gpkg"
MARKET_DIR    = PROCESSED_DIR / "market"  # C6 economics: issuers, buyers, comps

# The SEDAR+ filing corpus already captured by the sibling `mining-scraper`
# project. C6 reads it READ-ONLY and over the local filesystem only — no network
# call is made to SEDAR+ from this repo, so this repo cannot trip the anti-bot
# posture that project maintains. Override with CANADA_GEO_SEDAR_CORPUS.
SEDAR_CORPUS = Path(os.environ.get(
    "CANADA_GEO_SEDAR_CORPUS",
    str(Path.home() / "projects" / "mining-scraper")))
SEDAR_INDEX_DIR = SEDAR_CORPUS / "Downloads"          # one filing-index CSV per issuer
SEDAR_UNIVERSE  = SEDAR_CORPUS / "data" / "mining_universe_companies.csv"

LOG_DIR       = INDEX_ROOT / "logs"
MANIFEST_DB   = INDEX_ROOT / "manifest.sqlite"
CATALOG_DB    = INDEX_ROOT / "catalog.duckdb"
CHROMA_DB     = INDEX_ROOT / "chroma_db"

# BULK sits on exFAT, which rejects these characters in filenames. Harvesters
# must run downloaded names through safe_filename() before writing.
EXFAT_ILLEGAL = '"*:<>?\\|'

# Presence of this file at LAKE_ROOT proves the drive is actually mounted.
LAKE_MARKER = ".canada-geo-lake"

# Formats kept in the "core" (small) harvest. PDFs handled by the scrape connectors.
CORE_FORMATS = {"shp", "zip", "csv", "tsv", "xlsx", "xls", "kml", "kmz", "geojson",
                "gpkg", "json", "jsonl", "fgdb", "gdb", "tif", "tiff", "geotif", "gpx"}
PDF_FORMATS  = {"pdf"}

# ---------------------------------------------------------------------------
# Credentials. Never hardcode these — set them in .env (gitignored).
# ---------------------------------------------------------------------------
# Ocp-Apim-Subscription-Key for the GeologyOntario Elasticsearch API
# (ws.apis.lrc.gov.on.ca). Ontario ships this key in the public Angular SPA's
# main.js, so it is a rate-limit handle rather than a private credential — but
# it belongs to Ontario, not to us, so it stays out of the repo.
ON_API_KEY = os.environ.get("CANADA_GEO_ON_API_KEY", "")


def require_on_api_key() -> str:
    """Return the Ontario API key, or explain how to set it."""
    if not ON_API_KEY:
        raise RuntimeError(
            "CANADA_GEO_ON_API_KEY is not set — the Ontario AMIS/OAFD connectors "
            "need it.\nAdd it to .env (see .env.example); recover the current value "
            "from main.js on https://www.geologyontario.mines.gov.on.ca"
        )
    return ON_API_KEY


# CKAN action-API path is identical across portals; we append it to each portal base.
CKAN_ACTION_PATH = "/api/3/action"

# ArcGIS REST paging
ARCGIS_PAGE = 2000

# Politeness — be a good citizen of provincial servers.
USER_AGENT  = "canada-geo-lake/1.0 (personal research data mirror; contact: devpremissive@users.noreply.github.com)"
REQUEST_GAP = 0.5
TIMEOUT     = 180


def safe_filename(name: str) -> str:
    """Rewrite a filename so it can be written to the exFAT bulk volume."""
    for ch in EXFAT_ILLEGAL:
        name = name.replace(ch, "-")
    return name


def require_lake() -> None:
    """Fail loudly when the bulk volume is not mounted.

    An unmounted drive otherwise looks exactly like an empty lake, and the
    harvesters would cheerfully re-download ~15 GB onto the root filesystem.
    """
    if not (LAKE_ROOT / LAKE_MARKER).exists():
        raise RuntimeError(
            f"Bulk volume not mounted (or not initialised): {LAKE_ROOT}\n"
            f"Expected marker file {LAKE_ROOT / LAKE_MARKER}.\n"
            f"Plug the drive in, or point CANADA_GEO_LAKE elsewhere and create "
            f"the marker there."
        )


def ensure_dirs() -> None:
    require_lake()
    for d in (RAW_DIR, PDF_DIR, PROCESSED_DIR, TABLES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for d in (INDEX_ROOT, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
