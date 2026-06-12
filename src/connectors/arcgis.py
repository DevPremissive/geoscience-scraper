"""
connectors/arcgis.py — generic ArcGIS REST / Hub harvester.

Two modes:
  * layers : page a FeatureServer/MapServer layer .../query with f=geojson.
  * items  : an ArcGIS Hub download endpoint /api/download/v1/items/<id>/geojson.

Used for provincial tenure systems and ArcGIS-Hub geological surveys (SK, YT, NB...).
Output records look like CKAN ones so the harvester treats them uniformly: the "url"
is a ready-to-GET endpoint that returns GeoJSON.
"""
from __future__ import annotations
import json, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config as C


def _get_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": C.USER_AGENT})
    with urlopen(req, timeout=C.TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _layer_count(layer_url: str) -> int | None:
    try:
        q = urlencode({"where": "1=1", "returnCountOnly": "true", "f": "json"})
        return _get_json(f"{layer_url}/query?{q}").get("count")
    except Exception:
        return None


def discover(spec: dict, jurisdiction: str) -> list[dict]:
    """Build harvestable records from an arcgis spec (layers and/or hub items)."""
    out: list[dict] = []

    # --- explicit FeatureServer/MapServer layers ---------------------------
    for code, layer_url in (spec.get("layers") or {}).items():
        cnt = _layer_count(layer_url)
        # We harvest these by paging at fetch time; record the base layer URL.
        out.append({
            "jurisdiction": jurisdiction, "connector": "arcgis_layer",
            "code": code, "dataset": code, "resource_id": code,
            "resource_name": code, "format": "geojson",
            "url": layer_url, "last_modified": "", "size": None,
            "license": None, "portal": spec.get("portal"),
            "feature_count": cnt,
        })

    # --- ArcGIS Hub download items ----------------------------------------
    portal = (spec.get("portal") or "").rstrip("/")
    for code, item_id in (spec.get("items") or {}).items():
        url = f"{portal}/api/download/v1/items/{item_id}/geojson?layers=1"
        out.append({
            "jurisdiction": jurisdiction, "connector": "arcgis_hub",
            "code": code, "dataset": code, "resource_id": item_id,
            "resource_name": code, "format": "geojson",
            "url": url, "last_modified": "", "size": None,
            "license": None, "portal": portal,
        })
        time.sleep(C.REQUEST_GAP)
    return out


def fetch_layer_paged(layer_url: str, out_path) -> int:
    """Page an ArcGIS layer to a single GeoJSON FeatureCollection on disk."""
    features: list[dict] = []
    offset = 0
    while True:
        q = urlencode({"where": "1=1", "outFields": "*", "f": "geojson",
                       "resultOffset": offset, "resultRecordCount": C.ARCGIS_PAGE,
                       "outSR": 4326})
        data = _get_json(f"{layer_url}/query?{q}")
        batch = data.get("features", [])
        features.extend(batch)
        if len(batch) < C.ARCGIS_PAGE:
            break
        offset += C.ARCGIS_PAGE
        time.sleep(C.REQUEST_GAP)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection",
                                    "features": features}), encoding="utf-8")
    return len(features)
