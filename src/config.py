"""
Central configuration for the Canada-wide geoscience/mining data lake.

The WHAT (which sources) lives in sources.py as a declarative registry.
This file holds the WHERE (paths) and HOW (politeness, formats) knobs.
"""
from __future__ import annotations
import os
from pathlib import Path

# Where the lake lives. Override with env var CANADA_GEO_LAKE.
LAKE_ROOT = Path(os.environ.get("CANADA_GEO_LAKE", str(Path.home() / "canada-geo-lake")))

RAW_DIR       = LAKE_ROOT / "raw"        # raw/<JURISDICTION>/<CODE>/<YYYY-MM-DD>/
PDF_DIR       = LAKE_ROOT / "pdfs"       # pdfs/<JURISDICTION>/<CODE>/<id>.pdf
PROCESSED_DIR = LAKE_ROOT / "processed"
TABLES_DIR    = PROCESSED_DIR / "tables"
LOG_DIR       = LAKE_ROOT / "logs"
GPKG_PATH     = PROCESSED_DIR / "geo.gpkg"
MANIFEST_DB   = LAKE_ROOT / "manifest.sqlite"
CATALOG_DB    = LAKE_ROOT / "catalog.duckdb"

# Formats kept in the "core" (small) harvest. PDFs handled by the scrape connectors.
CORE_FORMATS = {"shp", "zip", "csv", "tsv", "xlsx", "xls", "kml", "kmz", "geojson",
                "gpkg", "json", "fgdb", "gdb", "tif", "tiff", "gpx"}
PDF_FORMATS  = {"pdf"}

# CKAN action-API path is identical across portals; we append it to each portal base.
CKAN_ACTION_PATH = "/api/3/action"

# ArcGIS REST paging
ARCGIS_PAGE = 2000

# Politeness — be a good citizen of provincial servers.
USER_AGENT  = "canada-geo-lake/1.0 (personal research data mirror; contact: you@example.com)"
REQUEST_GAP = 0.5
TIMEOUT     = 180


def ensure_dirs() -> None:
    for d in (RAW_DIR, PDF_DIR, PROCESSED_DIR, TABLES_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
