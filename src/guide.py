"""
guide.py — visibility dashboard & usage guide for the Canada Geo Data Lake.

Runs on port 8888. Serves:
  GET /           — visibility/status dashboard (pipeline health, coverage, tables)
  GET /guide      — usage guide with API docs, SQL recipes, and examples
  GET /guide/raw  — raw markdown version of the guide

The data API lives at port 9877 (serve.py). This dashboard reads from it.
"""
from __future__ import annotations
import json, sys, subprocess, urllib.request
from pathlib import Path
from datetime import datetime

import config as C

try:
    from fastapi import FastAPI, Query
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
    import uvicorn
except ImportError:
    sys.exit("pip install fastapi uvicorn")

HERE = Path(__file__).parent
DATA_API = "http://localhost:9877"
PKG = HERE.parent

app = FastAPI(title="Canada Geo Data Lake — Visibility & Guide")


def _fetch(path: str) -> dict | list:
    try:
        with urllib.request.urlopen(f"{DATA_API}{path}", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _fmt(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} KB"
    return f"{n} B"


def _freshness() -> str:
    raw = C.RAW_DIR
    if not raw.exists():
        return "never"
    dates = []
    for j in raw.iterdir():
        if j.is_dir():
            for c in j.iterdir():
                if c.is_dir():
                    for s in c.iterdir():
                        if s.is_dir():
                            dates.append(s.name)
    return max(dates) if dates else "unknown"


LAYOUT_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#f5f5f5;color:#1a1a1a;font-size:14px;line-height:1.5}
nav{background:#1a3a5c;padding:12px 24px;display:flex;gap:24px;align-items:center}
nav a{color:#fff;text-decoration:none;font-weight:500;font-size:14px;padding:4px 12px;border-radius:4px}
nav a:hover{background:rgba(255,255,255,.15)}
nav a.active{background:rgba(255,255,255,.2)}
.container{max-width:1200px;margin:0 auto;padding:24px}
h1{font-size:22px;margin-bottom:16px}
h2{font-size:18px;margin:24px 0 12px;border-bottom:2px solid #e0e0e0;padding-bottom:4px}
.card{background:#fff;border-radius:8px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.stat{text-align:center;padding:16px}
.stat .num{font-size:28px;font-weight:700;color:#1a3a5c}
.stat .label{font-size:12px;color:#666;margin-top:4px}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge.ok{background:#d1fae5;color:#065f46}
.badge.gap{background:#fef3c7;color:#92400e}
.badge.miss{background:#fee2e2;color:#991b1b}
.badge.info{background:#dbeafe;color:#1e40af}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid #eee}
th{background:#f8f9fa;font-weight:600}
code{background:#eef2f6;padding:1px 5px;border-radius:3px;font-size:13px}
pre{background:#1a1a2e;color:#e0e0e0;padding:16px;border-radius:8px;overflow-x:auto;font-size:13px;margin:8px 0}
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canada Geo Data Lake — Visibility</title>
<style>{{STYLE}}</style>
</head>
<body>
<nav>
  <a href="/" class="active">Dashboard</a>
  <a href="/guide">Guide</a>
  <a href="http://localhost:9877">Map</a>
</nav>
<div class="container">
  <h1>Canada Geo Data Lake — Pipeline Visibility</h1>

  <div class="grid">
    <div class="card stat"><div class="num">{{LAYERS}}</div><div class="label">Spatial Layers</div></div>
    <div class="card stat"><div class="num">{{TABLES}}</div><div class="label">Parquet Tables</div></div>
    <div class="card stat"><div class="num">{{FEATURES}}</div><div class="label">Total Features</div></div>
    <div class="card stat"><div class="num">{{JURIS}}</div><div class="label">Jurisdictions</div></div>
    <div class="card stat"><div class="num">{{TENURE}}</div><div class="label">Tenure Layers</div></div>
    <div class="card stat"><div class="num">{{SIZE}}</div><div class="label">GPKG Size</div></div>
  </div>

  <h2>Pipeline Status</h2>
  <div class="grid">
    <div class="card stat"><div style="font-size:24px">{{HARVEST_ICON}}</div><div class="num">{{FRESHNESS}}</div><div class="label">Last Harvest</div></div>
    <div class="card stat"><div style="font-size:24px">{{PROCESS_ICON}}</div><div class="num">{{COVERAGE_PCT}}%</div><div class="label">Coverage Score</div></div>
    <div class="card stat"><div style="font-size:24px">{{INDEX_ICON}}</div><div class="num">{{SEARCH_ROWS}}</div><div class="label">Searchable Rows</div></div>
  </div>

  <h2>Coverage by Jurisdiction</h2>
  <div class="card">{{COVERAGE_TABLE}}</div>

  <h2>Tenure / Mineral Claims — National Overview</h2>
  <div class="card">{{TENURE_TABLE}}</div>

  <h2>Data Gaps</h2>
  <div class="card">{{GAPS_TABLE}}</div>
</div>
</body>
</html>"""


def _build_dashboard() -> str:
    layers_data = _fetch("/layers") or {"layers": [], "total": 0}
    coverage_data = _fetch("/coverage") or {"coverage": []}
    tenure_data = _fetch("/tenure") or {"tenure_layers": []}

    gpkg = C.GPKG_PATH
    gpkg_size = _fmt(gpkg.stat().st_size) if gpkg.exists() else "?"
    n_features = sum(l.get("features", 0) for l in layers_data.get("layers", []))
    n_tables = len(list(C.TABLES_DIR.glob("*.parquet")))
    freshness = _freshness()

    stats_data = _fetch("/stats") or {}
    search_rows = stats_data.get("search_rows", 0)
    n_tables = stats_data.get("parquet_tables", n_tables)

    # Coverage table
    cov_rows = coverage_data.get("coverage", [])
    cov_rows.sort(key=lambda r: r["jurisdiction"])
    cov_html = "<table><tr><th>Jurisdiction</th><th>Layers</th><th>Status</th></tr>"
    for r in cov_rows:
        badge = '<span class="badge ok">OK</span>'
        cov_html += f"<tr><td>{r['jurisdiction']}</td><td>{r['layers']}</td><td>{badge}</td></tr>"
    cov_html += "</table>"

    # Tenure table
    ten_rows = tenure_data.get("tenure_layers", [])
    ten_rows.sort(key=lambda r: (r["jurisdiction"], r["name"]))
    ten_html = "<table><tr><th>Layer</th><th>Jurisdiction</th><th>Features</th><th>Type</th></tr>"
    for r in ten_rows:
        ten_html += f"<tr><td>{r['name']}</td><td>{r['jurisdiction']}</td><td>{r['features']:,}</td><td>{r['geom_type']}</td></tr>"
    ten_html += "</table>"

    # Gaps — jurisdictions with no data
    all_juris = {"FED", "BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS", "PE", "NL", "YT", "NT", "NU"}
    present = {r["jurisdiction"] for r in cov_rows}
    # Expand NT_NU → NT, NU
    resolved = set()
    for j in present:
        if "_" in j:
            resolved.update(j.split("_"))
        else:
            resolved.add(j)
    gaps = sorted(all_juris - resolved)
    gap_html = "<table><tr><th>Jurisdiction</th><th>Issue</th></tr>"
    labeled_gaps = {
        "MB": "No ArcGIS/CKAN endpoint found; MapGallery only",
        "AB": "No public mineral claim API; AGS publications only",
        "NL": "GeoFiles PDF server blocked; no claims API",
        "PE": "No geoscience data portal found",
        "QC": "SIGEOM tabular data harvested; GESTIM claims blocked (no API)",
        "FED": "NRCan CKAN packages found but may have gaps (CDoGS, geophysics)",
    }
    for g in gaps:
        issue = labeled_gaps.get(g, "No data source discovered yet")
        gap_html += f'<tr><td>{g}</td><td><span class="badge miss">{issue}</span></td></tr>'
    gap_html += "</table>"

    coverage_pct = round(len(present) / len(all_juris) * 100)

    return (
        DASHBOARD_HTML.replace("{{STYLE}}", LAYOUT_CSS)
        .replace("{{LAYERS}}", str(layers_data.get("total", 0)))
        .replace("{{TABLES}}", str(n_tables))
        .replace("{{FEATURES}}", f"{n_features:,}")
        .replace("{{JURIS}}", str(len(cov_rows)))
        .replace("{{TENURE}}", str(len(ten_rows)))
        .replace("{{SIZE}}", gpkg_size)
        .replace("{{FRESHNESS}}", freshness)
        .replace("{{HARVEST_ICON}}", "✅" if freshness != "never" else "❌")
        .replace("{{COVERAGE_PCT}}", str(coverage_pct))
        .replace("{{PROCESS_ICON}}", "✅" if coverage_pct > 75 else "⚠️")
        .replace("{{SEARCH_ROWS}}", f"{search_rows:,}")
        .replace("{{INDEX_ICON}}", "✅" if search_rows > 0 else "❌")
        .replace("{{COVERAGE_TABLE}}", cov_html)
        .replace("{{TENURE_TABLE}}", ten_html)
        .replace("{{GAPS_TABLE}}", gap_html)
    )


GUIDE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canada Geo Data Lake — Guide</title>
<style>{{STYLE}}</style>
</head>
<body>
<nav>
  <a href="/">Dashboard</a>
  <a href="/guide" class="active">Guide</a>
  <a href="http://localhost:9877">Map</a>
</nav>
<div class="container">
  <h1>Usage Guide</h1>
  <p>How to query, explore, and use the Canada Geoscience &amp; Mining Data Lake.</p>

  <h2>Architecture Overview</h2>
  <div class="card">
    <pre>BULK — /media/vis/Expansion/canada-geo-lake/   (external drive)
  raw/&lt;JURISDICTION&gt;/&lt;CODE&gt;/&lt;YYYY-MM-DD&gt;/   # immutable dated snapshots
  processed/
    geo.gpkg                                  # ALL vector layers (77 layers, ~1.4 GB)
    tables/*.parquet                          # flat attribute tables (40 files, ~206 MB)

INDEX — ~/infra/canada-geo-lake-data/          (local NVMe)
  catalog.duckdb                               # national metadata search index (119 tables)
  manifest.sqlite                              # harvest ledger
  chroma_db/                                   # PDF vector store</pre>
  </div>

  <h2>Quick Start — DuckDB CLI</h2>
  <div class="card">
    <pre>duckdb ~/infra/canada-geo-lake-data/catalog.duckdb</pre>
    <p>Then load the spatial extension:</p>
    <pre>INSTALL spatial; LOAD spatial;</pre>
    <p>List what's available:</p>
    <pre>SHOW TABLES;</pre>
  </div>

  <h2>SQL Recipes</h2>

  <h3>Cross-Canada search for a commodity</h3>
  <div class="card">
    <pre>SELECT source_table, rid, text
FROM search_text
WHERE text ILIKE '%gold%' AND text ILIKE '%copper%'
ORDER BY rid ASC
LIMIT 20;</pre>
  </div>

  <h3>Mineral occurrences by jurisdiction</h3>
  <div class="card">
    <pre>SELECT regexp_extract(table_name, '^geo_([A-Z_]+?)__', 1) AS juris,
       count(*) AS layers
FROM information_schema.tables
WHERE table_name LIKE 'geo_%'
GROUP BY 1 ORDER BY 1;</pre>
  </div>

  <h3>Ontario mineral claims — count by commodity</h3>
  <div class="card">
    <pre>DESCRIBE "geo_ON__ON_CLAIMS2";
-- Then adapt to actual column names:
SELECT "COMMODITY", count(*)
FROM "geo_ON__ON_CLAIMS2"
GROUP BY 1 ORDER BY 2 DESC
LIMIT 10;</pre>
  </div>

  <h3>Drill holes by depth</h3>
  <div class="card">
    <pre>SELECT * FROM "geo_NB__NB_DRILLHOLE"
WHERE TRY_CAST("length_m" AS DOUBLE) > 500
ORDER BY length_m DESC
LIMIT 20;</pre>
  </div>

  <h3>Provenance: when was each source last refreshed?</h3>
  <div class="card">
    <pre>SELECT jurisdiction, code, max(snapshot_date) AS last_snapshot,
       count(*) AS files
FROM resources
GROUP BY 1, 2 ORDER BY 1, 2;</pre>
  </div>

  <h2>REST API</h2>
  <p>The FastAPI server runs on <code>localhost:9877</code>.</p>
  <div class="card">
    <table>
      <tr><th>Endpoint</th><th>Description</th></tr>
      <tr><td><code>GET /</code></td><td>Interactive map UI (MapLibre)</td></tr>
      <tr><td><code>GET /layers</code></td><td>List all 77 GPKG layers with feature counts</td></tr>
      <tr><td><code>GET /search?q=gold+copper</code></td><td>Full-text search across 874K rows</td></tr>
      <tr><td><code>GET /geojson/{layer}?limit=200</code></td><td>Sample GeoJSON from any layer (5% Bernoulli)</td></tr>
      <tr><td><code>GET /coverage</code></td><td>Jurisdiction coverage summary</td></tr>
      <tr><td><code>GET /tenure</code></td><td>Mineral claim / tenure layer overview</td></tr>
    </table>
  </div>

  <h2>Refreshing the Pipeline</h2>
  <div class="card">
    <p>Full pipeline (harvest → process → index → coverage):</p>
    <pre>uv run python src/update_all.py</pre>
    <p>Individual steps:</p>
    <pre>uv run python src/harvest.py               # download new/changed data
uv run python src/process.py                # rebuild geo.gpkg + parquet tables
uv run python src/coverage.py               # print coverage report
uv run python src/build_index.py            # rebuild catalog.duckdb</pre>
    <p>Check what's in the lake:</p>
    <pre>uv run python src/check_lake.py             # full summary
uv run python src/check_lake.py ON          # just Ontario
uv run python src/check_lake.py BC/BC_MINFILE  # specific layer</pre>
  </div>

  <h2>Starting the Servers</h2>
  <div class="card">
    <p>Data API + map UI (port 9877):</p>
    <pre>uv run python src/serve.py --port 9877</pre>
    <p>Visibility dashboard + guide (port 8888):</p>
    <pre>uv run python src/guide.py --port 8888</pre>
  </div>

  <h2>Data Sources</h2>
  <div class="card">
    <table>
      <tr><th>Source</th><th>Connector</th><th>Data</th></tr>
      <tr><td>NRCan (open.canada.ca)</td><td>CKAN</td><td>CGMC, CDoGS, geophysics, national tenure</td></tr>
      <tr><td>Ontario GeoHub</td><td>direct + ogsearth + es_scroll</td><td>Bedrock/surficial geology, geochem, geophys, AMIS, OAFD, claims (KMZ tiles)</td></tr>
      <tr><td>BC GeoHub (catalogue.data.gov.bc.ca)</td><td>CKAN + WFS</td><td>MINFILE, geology, MTA grid, geochem, ARIS</td></tr>
      <tr><td>Québec (donneesquebec.ca)</td><td>CKAN</td><td>SIGÉOM (13 packages: bedrock, drillholes, geochem, minpot, etc.)</td></tr>
      <tr><td>New Brunswick</td><td>CKAN</td><td>Drillholes, mineral claims, MPS, reports of work</td></tr>
      <tr><td>Yukon</td><td>ArcGIS MapServer</td><td>Claims, placer/quartz leases, crown grants</td></tr>
      <tr><td>Nunavut</td><td>ArcGIS</td><td>Mineral claims, mining leases, prospecting permits</td></tr>
      <tr><td>Saskatchewan</td><td>ArcGIS</td><td>SMDI, mineral exploration</td></tr>
      <tr><td>USGS</td><td>direct</td><td>MRDS (global mineral sites), USMIN (per-state mineral sites)</td></tr>
    </table>
  </div>

  <h2>Coverage Gaps</h2>
  <div class="card">
    <p>See <code>SCRAPERS_BLOCKED.md</code> for full details. Key gaps:</p>
    <ul style="margin-left:20px">
      <li><strong>MB</strong> — MapGallery only; no ArcGIS REST or CKAN found</li>
      <li><strong>NL</strong> — GeoFiles PDF server returns 500/404</li>
      <li><strong>AB</strong> — no public mineral claim API</li>
      <li><strong>PE</strong> — no geoscience data portal</li>
      <li><strong>QC GESTIM</strong> — claims boundaries require paid license</li>
      <li><strong>SK, NS, NB, NT</strong> — assessment PDFs blocked by ASP.NET/JSF portals</li>
    </ul>
  </div>
</div>
</body>
</html>"""


@app.get("/")
def dashboard():
    return HTMLResponse(_build_dashboard())


@app.get("/guide")
def guide():
    return HTMLResponse(GUIDE_HTML.replace("{{STYLE}}", LAYOUT_CSS))


@app.get("/guide/raw")
def guide_raw():
    """Return the guide as plain markdown for embedding."""
    return PlainTextResponse("See the HTML guide at /guide")


@app.get("/api/ping")
def ping():
    """Health check."""
    try:
        with urllib.request.urlopen("http://localhost:9877/layers", timeout=3) as r:
            data = json.loads(r.read())
        return {"status": "ok", "layers": data.get("total", 0)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8888)
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
