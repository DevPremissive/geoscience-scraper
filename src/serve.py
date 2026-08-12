"""
serve.py — FastAPI geospatial data server for the Canada Geo Data Lake.

Endpoints:
  GET /                    — HTML map UI (MapLibre)
  GET /layers              — list GPKG layers with feature counts
  GET /search?q=...        — full-text search across catalog
  GET /geojson/{layer}     — sample GeoJSON from a layer (no bbox filter)
  GET /coverage            — jurisdiction coverage summary
"""
from __future__ import annotations
import json, sys
from pathlib import Path

import config as C

try:
    import duckdb
except ImportError:
    sys.exit("pip install duckdb")

try:
    from fastapi import FastAPI, Query, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
except ImportError:
    sys.exit("pip install fastapi uvicorn")

app = FastAPI(title="Canada Geo Data Lake API")


def get_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(C.CATALOG_DB))
    con.execute("SET autoinstall_known_extensions=1; SET autoload_known_extensions=1")
    try:
        con.execute("LOAD spatial")
    except Exception:
        con.execute("FORCE INSTALL spatial; LOAD spatial")
    return con


HTML_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Canada Geo Data Lake</title>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css">
<script src="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;font-size:14px}
#map{position:absolute;top:0;left:0;width:100%;height:100%}
#panel{position:absolute;top:12px;left:12px;z-index:10;background:rgba(255,255,255,.95);border-radius:8px;padding:16px;width:380px;max-height:calc(100vh-24px);box-shadow:0 2px 8px rgba(0,0,0,.15);overflow-y:auto}
#panel h1{font-size:16px;margin-bottom:8px}
#panel input,#panel select{width:100%;padding:6px 8px;margin-bottom:6px;border:1px solid #ccc;border-radius:4px}
#panel button{width:100%;padding:6px;background:#2563eb;color:#fff;border:none;border-radius:4px;cursor:pointer}
#panel button:hover{background:#1d4ed8}
#results{margin-top:8px;max-height:250px;overflow-y:auto}
.result{padding:4px 6px;border-bottom:1px solid #eee;cursor:pointer;font-size:12px}
.result:hover{background:#f0f0f0}
.result small{color:#666}
#layerList{margin-top:6px;max-height:200px;overflow-y:auto}
.layer-item{display:flex;align-items:center;gap:6px;padding:2px 0;font-size:11px}
.layer-item input{width:auto;margin:0}
</style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <h1>Canada Geo Data Lake</h1>
  <input id="search" type="text" placeholder='Search (e.g. "gold copper")' />
  <select id="layerSelect"><option value="">All layers</option></select>
  <button onclick="doSearch()">Search</button>
  <div id="results"></div>
  <details open><summary><strong>Layers</strong></summary><div id="layerList"></div></details>
</div>
<script>
const map = new maplibregl.Map({
  container:'map',
  style:'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  center:[-95,55], zoom:3
});
map.addControl(new maplibregl.NavigationControl());
let activeGeoLayer = null;

async function loadLayers() {
  const r = await fetch('/layers');
  const data = await r.json();
  const sel = document.getElementById('layerSelect');
  const legend = document.getElementById('layerList');
  data.layers.forEach(l => {
    const opt = document.createElement('option');
    opt.value = l.name; opt.textContent = l.name;
    sel.appendChild(opt);
    const div = document.createElement('div'); div.className = 'layer-item';
    const cb = document.createElement('input'); cb.type = 'checkbox';
    cb.dataset.layer = l.name;
    cb.onchange = () => toggleLayer(l.name, cb.checked);
    div.appendChild(cb);
    const label = document.createElement('span');
    label.textContent = l.name + ' (' + l.features.toLocaleString() + ')';
    div.appendChild(label);
    legend.appendChild(div);
  });
}

async function toggleLayer(name, show) {
  if (!show) {
    if (map.getLayer(name)) map.removeLayer(name);
    if (map.getSource(name)) map.removeSource(name);
    if (activeGeoLayer === name) activeGeoLayer = null;
    return;
  }
  if (activeGeoLayer && activeGeoLayer !== name) {
    if (map.getLayer(activeGeoLayer)) map.removeLayer(activeGeoLayer);
    if (map.getSource(activeGeoLayer)) map.removeSource(activeGeoLayer);
  }
  try {
    const r = await fetch('/geojson/' + encodeURIComponent(name) + '?limit=3000');
    const geojson = await r.json();
    if (!geojson.features || geojson.features.length === 0) return;
    map.addSource(name, {type:'geojson', data:geojson});
    const type = detectGeomType(geojson);
    if (type === 'Point' || type === 'MultiPoint') {
      map.addLayer({id:name, type:'circle', source:name,
        paint:{'circle-radius':3,'circle-color':'#2563eb','circle-opacity':0.6}});
    } else {
      map.addLayer({id:name, type:'fill', source:name,
        paint:{'fill-color':'#2563eb','fill-opacity':0.3}});
    }
    activeGeoLayer = name;
  } catch(e) { console.error(e); }
}

function detectGeomType(gj) {
  const types = new Set();
  for (const f of gj.features.slice(0,10)) {
    if (f.geometry) types.add(f.geometry.type);
  }
  if (types.has('Point') || types.has('MultiPoint')) return 'Point';
  return 'Polygon';
}

async function doSearch() {
  const q = document.getElementById('search').value;
  const layer = document.getElementById('layerSelect').value;
  const params = new URLSearchParams({q});
  if (layer) params.set('layer', layer);
  const r = await fetch('/search?' + params);
  const data = await r.json();
  const div = document.getElementById('results');
  if (data.results.length === 0) { div.innerHTML = '<em>no results</em>'; return; }
  div.innerHTML = data.results.slice(0, 50).map(res =>
    '<div class="result"><strong>' + res.source_table + '</strong><br>' +
    escHtml(res.snippet) + '<br><small>score: ' + res.score.toFixed(2) + '</small></div>'
  ).join('');
}

function escHtml(s) { return (s||'').replace(/[&<>]/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }

loadLayers();
</script>
</body>
</html>"""


@app.get("/")
def index():
    return HTMLResponse(HTML_UI)


@app.get("/layers")
def list_layers():
    con = get_db()
    try:
        row = con.execute("SELECT * FROM st_read_meta(?)", [str(C.GPKG_PATH)]).fetchone()
        layers = [
            {"name": l["name"], "features": l["feature_count"],
             "geom_type": l["geometry_fields"][0]["type"] if l["geometry_fields"] else "?"}
            for l in (row[3] if row else [])
        ]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        con.close()
    return {"layers": layers, "total": len(layers)}


@app.get("/search")
def search(q: str = Query(...), layer: str = Query(""), limit: int = Query(50)):
    con = get_db()
    try:
        terms = [t.strip() for t in q.replace('"', "").split() if t.strip()]
        conditions = " AND ".join(f"text ILIKE '%{t}%'" for t in terms)
        score_expr = " + ".join(f"(CASE WHEN text ILIKE '%{t}%' THEN 1 ELSE 0 END)" for t in terms)
        tbl_filter = f" AND source_table LIKE '%{layer}%'" if layer else ""
        sql = f"""
            SELECT source_table, rid, text,
                   ({score_expr})::REAL AS score
            FROM search_text
            WHERE {conditions}{tbl_filter}
            ORDER BY score DESC, rid ASC
            LIMIT {limit}
        """
        rows = con.execute(sql).fetchall()
        results = [
            {"source_table": r[0], "rid": r[1],
             "snippet": (r[2] or "")[:200],
             "score": float(r[3]) if r[3] else 0}
            for r in rows
        ]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        con.close()
    return {"query": q, "results": results, "total": len(results)}


@app.get("/geojson/{layer:path}")
def get_geojson(layer: str, limit: int = Query(200)):
    con = get_db()
    try:
        safe = layer.replace('"', '""')
        sql = f"""
            SELECT ST_AsGeoJSON(geom)::JSON AS _geom,
                   * EXCLUDE (geom)
            FROM "geo_{safe}"
            USING SAMPLE 5% (bernoulli)
            LIMIT {limit}
        """
        rows = con.execute(sql).fetchall()
        cols = [d[0] for d in con.description]
        features = []
        for row in rows:
            props = dict(zip(cols[1:], row[1:]))
            for k in list(props.keys()):
                v = props[k]
                if isinstance(v, (int, float)) and (v != v or v is None):
                    props[k] = None
            geom_val = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            features.append({"type": "Feature", "geometry": geom_val, "properties": props})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        con.close()
    return {"type": "FeatureCollection", "features": features}


TENURE_CODES = (
    "ON_CLAIMS2", "ON_ALIENATIONS", "ON_DISPOSITIONS",
    "ON_DISPOSITIONS_NONMINING", "ON_PLANS_PERMITS",
    "NB_MINERAL_CLAIMS", "NT_NU_MINERAL_CLAIMS",
    "NU_MINING_LEASES", "NU_PROSPECTING_PERMITS",
    "YT_MINERAL_CLAIMS_POLY", "YT_MINERAL_CLAIMS_LINE",
    "YT_PLACER_CLAIMS", "YT_QUARTZ_CLAIMS",
    "SK_MINERAL_EXPLORATION",
)


@app.get("/coverage")
def coverage():
    con = get_db()
    try:
        rows = con.execute("""
            SELECT regexp_extract(table_name, '^geo_([A-Z_]+?)__', 1) AS juris,
                   count(*) AS layers
            FROM information_schema.tables
            WHERE table_name LIKE 'geo_%'
            GROUP BY 1 ORDER BY 1
        """).fetchall()
        data = [{"jurisdiction": r[0], "layers": r[1]} for r in rows]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        con.close()
    return {"coverage": data, "total_jurisdictions": len(data)}


@app.get("/stats")
def stats():
    db = get_db()
    try:
        search_rows = db.execute("SELECT count(*) FROM search_text").fetchone()[0]
    except Exception:
        search_rows = 0
    try:
        n_tables = len(list(Path(C.LAKE_ROOT).glob("processed/tables/*.parquet")))
    except Exception:
        n_tables = 0
    return {"search_rows": search_rows, "parquet_tables": n_tables}

@app.get("/tenure")
def tenure():
    con = get_db()
    tenure_layers = []
    try:
        row = con.execute("SELECT * FROM st_read_meta(?)", [str(C.GPKG_PATH)]).fetchone()
        layers = row[3] if row else []
        for l in layers:
            code = l["name"].split("__")[1] if "__" in l["name"] else ""
            if code in TENURE_CODES:
                tenure_layers.append({
                    "name": l["name"],
                    "jurisdiction": l["name"].split("__")[0],
                    "code": code,
                    "features": l["feature_count"],
                    "geom_type": l["geometry_fields"][0]["type"] if l["geometry_fields"] else "?",
                })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        con.close()
    return {"tenure_layers": tenure_layers, "total": len(tenure_layers)}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
