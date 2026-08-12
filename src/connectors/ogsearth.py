"""
ogsearth.py — ON OGSEarth SuperOverlay KML tile discovery.

OGSEarth publishes mineral tenure boundaries as Google Earth SuperOverlay KMLs.
A root doc.kml contains hundreds of NetworkLink Regions, each pointing to a KMZ
tile with polygon Placemarks for a 0.5x0.5 degree bbox.

Usage::
    from connectors.ogsearth import discover
    tiles = discover(spec, "ON")
"""
from __future__ import annotations
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import config as C


NS = {"kml": "http://earth.google.com/kml/2.2"}


def _parse_tiles(root_url: str, tile_dir: str) -> list[dict]:
    """Fetch root doc.kml, return tile resource dicts."""
    req = Request(root_url, headers={"User-Agent": C.USER_AGENT})
    with urlopen(req, timeout=C.TIMEOUT) as r:
        raw = r.read()

    tree = ET.fromstring(raw)
    # Base URL is the directory containing the root KML
    base = root_url.rsplit("/", 1)[0]
    tiles = []
    for nl in tree.iter(f"{{{NS['kml']}}}NetworkLink"):
        href_el = nl.find("kml:Link/kml:href", NS)
        if href_el is None or not href_el.text:
            continue
        href = href_el.text.strip()
        # Only collect .kmz files (skip relative links to other KMLs)
        if not href.endswith(".kmz"):
            continue
        name_el = nl.find("kml:name", NS)
        tile_name = name_el.text.strip() if name_el is not None else Path(href).stem
        # Resolve relative URL
        if href.startswith("http"):
            tile_url = href
        else:
            tile_url = f"{base}/{href}" if not href.startswith("/") else f"{base}{href}"

        # Extract bbox from Region for metadata
        region = nl.find("kml:Region/kml:LatLonAltBox", NS)
        bbox = {}
        if region is not None:
            for bound in ("north", "south", "east", "west"):
                el = region.find(f"kml:{bound}", NS)
                if el is not None and el.text:
                    bbox[bound] = el.text

        tiles.append({
            "jurisdiction": "ON",
            "connector": "ogsearth",
            "code": tile_name,
            "dataset": tile_name,
            "resource_id": tile_name,
            "resource_name": tile_name,
            "format": "kmz",
            "url": tile_url,
            "last_modified": "",
            "size": None,
            "license": "Open Government Licence - Ontario",
            "portal": base,
            "note": "",
            "bbox": bbox,
        })
    return tiles


def discover(spec: dict, jurisdiction: str) -> list[dict]:
    """Yield tile resources for every OGSEarth layer declared in spec."""
    all_tiles: list[dict] = []
    layers = spec.get("layers", {})
    portal = spec.get("portal", "")
    for layer_key, layer_cfg in layers.items():
        root_kml_path = layer_cfg.get("root_kml", "")
        tile_dir = layer_cfg.get("tile_dir", "")
        if not root_kml_path:
            print(f"  ! {jurisdiction}/{layer_key}: no root_kml", file=sys.stderr)
            continue
        root_url = f"{portal.rstrip('/')}/{root_kml_path.lstrip('/')}"
        try:
            layer_tiles = _parse_tiles(root_url, tile_dir)
            for t in layer_tiles:
                t["code"] = layer_key
                t["dataset"] = layer_cfg.get("description", layer_key)
            all_tiles.extend(layer_tiles)
            print(f"  [ogsearth   ] {jurisdiction}/{layer_key}: "
                  f"{len(layer_tiles)} tiles", file=sys.stderr)
        except Exception as e:
            print(f"  ! {jurisdiction}/{layer_key}: {e}", file=sys.stderr)
    return all_tiles
