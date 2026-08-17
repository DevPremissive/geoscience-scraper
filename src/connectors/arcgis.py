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
import json, sys, time
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


#: Attempts per page before the whole layer is abandoned.
PAGE_ATTEMPTS = 4


#: Smallest page worth trying before giving up on a layer.
MIN_PAGE = 50


def _get_page(url: str) -> dict:
    """Fetch one page, retrying transient server-side drops.

    The Ontario LIO service closes the connection partway through long paged
    reads — `Remote end closed connection without response`, and an SSL
    `UNEXPECTED_EOF_WHILE_READING`. Without a per-page retry a single dropped
    page discards every page already fetched, which is why the 172,259-feature
    OMEIS drillhole layer never completed while short layers on the same
    service succeeded first time.

    Only *transient* failures are retried here. A response that parses as
    something other than JSON is a deterministic refusal — retrying it just
    burns the backoff — so it propagates for the caller to handle by asking for
    less data.
    """
    delay = 2
    for attempt in range(1, PAGE_ATTEMPTS + 1):
        try:
            return _get_json(url)
        except json.JSONDecodeError:
            raise
        except Exception as e:                                  # noqa: BLE001
            if attempt == PAGE_ATTEMPTS:
                raise
            print(f"    … page retry {attempt}/{PAGE_ATTEMPTS - 1}: {e}",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 2


def _page_with_backoff(layer_url: str, offset: int, page: int):
    """One page, halving the request size when the server refuses to serve it.

    Ontario's Provincial Park Regulated layer answers a 2,000-feature GeoJSON
    request with an HTML error page from the ArcGIS Web Adaptor: parks like
    Polar Bear and Algonquin carry enormous vertex counts, so the response
    exceeds what the adaptor will return, and no amount of retrying the same
    request helps. Smaller pages succeed, so ask for less rather than fail the
    layer. Returns `(data, page)` so the caller keeps the working size.
    """
    while True:
        q = urlencode({"where": "1=1", "outFields": "*", "f": "geojson",
                       "resultOffset": offset, "resultRecordCount": page,
                       "outSR": 4326})
        try:
            return _get_page(f"{layer_url}/query?{q}"), page
        except json.JSONDecodeError:
            if page <= MIN_PAGE:
                raise
            page = max(MIN_PAGE, page // 4)
            print(f"    … response was not JSON at {page * 4} features; "
                  f"retrying this page at {page}", file=sys.stderr)


def fetch_layer_paged(layer_url: str, out_path) -> int:
    """Page an ArcGIS layer to a single GeoJSON FeatureCollection on disk."""
    features: list[dict] = []
    offset = 0
    page = C.ARCGIS_PAGE
    while True:
        data, page = _page_with_backoff(layer_url, offset, page)
        batch = data.get("features", [])
        features.extend(batch)
        # Compare against the page size actually used, not the configured one:
        # after a backoff a full page is smaller, and testing against the
        # original would stop paging while features remain.
        if len(batch) < page:
            break
        offset += page
        time.sleep(C.REQUEST_GAP)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection",
                                    "features": features}), encoding="utf-8")
    return len(features)
