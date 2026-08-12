"""
connectors/wfs.py — OGC WFS harvester for BC MTA mineral tenure.

BC's mineral tenure data is served through BCGW WFS (not FeatureServer).
WFS 2.0 with sortBy + startIndex paging gets us all 42K claims polygons.

Usage:
    python -c "from connectors.wfs import discover, fetch_paged"
"""
from __future__ import annotations
import json, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config as C


def discover(spec: dict, jurisdiction: str) -> list[dict]:
    """Build harvestable records from a WFS source spec."""
    out: list[dict] = []
    for code, layer_cfg in (spec.get("layers") or {}).items():
        url = layer_cfg.get("url") or spec["portal"]
        out.append({
            "jurisdiction": jurisdiction,
            "connector": "wfs_layer",
            "code": code,
            "dataset": code,
            "resource_id": code,
            "resource_name": code,
            "format": "geojson",
            "url": url,
            "type_name": layer_cfg["type_name"],
            "sort_by": layer_cfg.get("sort_by", "OBJECTID"),
            "page_size": layer_cfg.get("page_size", 10000),
            "last_modified": "",
            "size": None,
            "license": None,
            "portal": spec.get("portal"),
        })
    return out


def fetch_paged(layer_url: str, type_name: str, sort_by: str,
                out_path, page_size: int = 10000) -> int:
    """Page a WFS 2.0 feature type to a single GeoJSON FeatureCollection."""
    features: list[dict] = []
    offset = 0
    while True:
        params = urlencode({
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": type_name,
            "count": page_size,
            "startIndex": offset,
            "sortBy": sort_by,
            "outputFormat": "application/json",
        })
        req = Request(f"{layer_url}?{params}",
                      headers={"User-Agent": C.USER_AGENT})
        with urlopen(req, timeout=C.TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        batch = data.get("features", [])
        features.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        time.sleep(C.REQUEST_GAP)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    return len(features)
