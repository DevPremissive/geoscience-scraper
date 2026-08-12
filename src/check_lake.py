#!/usr/bin/env python3
"""
check_lake.py — inspect the national data lake.

Scans all harvested files across jurisdictions and produces a
consolidated summary with feature counts, file sizes, and format info.

Usage:
    python src/check_lake.py                        # full summary
    python src/check_lake.py BC                     # jurisdiction filter
    python src/check_lake.py BC/BC_MTA_CURRENT      # layer filter
    python src/check_lake.py --feat BC/BC_MTA_CURRENT 5  # peek at n features
"""
from __future__ import annotations
import json, sys, zipfile, csv, io
from pathlib import Path
import config as C


def _fmt(sz: int) -> str:
    if sz >= 1_000_000_000:
        return f"{sz / 1_000_000_000:.1f}GB"
    if sz >= 1_000_000:
        return f"{sz / 1_000_000:.1f}MB"
    if sz >= 1_000:
        return f"{sz / 1_000:.1f}KB"
    return f"{sz}B"


def _count_features(path: Path) -> tuple[int, str]:
    """Return (feature_count, geom_desc) for a native geospatial file."""
    import xml.etree.ElementTree as ET
    ext = path.suffix.lower()
    try:
        if ext in (".geojson", ".json"):
            data = json.loads(path.read_bytes())
            feats = data.get("features", [])
            types: dict[str, int] = {}
            for f in feats:
                gt = (f.get("geometry") or {}).get("type", "None")
                types[gt] = types.get(gt, 0) + 1
            gt_str = ", ".join(f"{k}={v}" for k, v in sorted(types.items()))
            return len(feats), gt_str
        elif ext == ".csv":
            with open(path) as f:
                reader = csv.reader(f)
                rows = sum(1 for _ in reader)
            return max(0, rows - 1), "rows"
        elif ext == ".kmz":
            with zipfile.ZipFile(path) as z:
                kml_names = [n for n in z.namelist() if n.endswith(".kml")]
                if kml_names:
                    tree = ET.fromstring(z.read(kml_names[0]))
                    ns = {"k": "http://earth.google.com/kml/2.2"}
                    pm = tree.findall(".//k:Placemark", ns)
                    polys = tree.findall(".//k:Polygon", ns)
                    return len(pm), f"polygons={len(polys)}"
        elif ext in (".gpkg", ".gdb", ".shp"):
            return 0, "(binary - use ogr/geopandas)"
        elif ext in (".tif", ".tiff", ".geotif"):
            return 0, "(raster)"
        elif ext == ".kml":
            tree = ET.fromstring(path.read_bytes())
            ns = {"k": "http://earth.google.com/kml/2.2"}
            pm = tree.findall(".//k:Placemark", ns)
            return len(pm), "placemarks"
        elif ext == ".zip":
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                shp_count = sum(1 for n in names if n.endswith(".shp"))
                return 0, f"zip ({len(names)} files, {shp_count} SHP)" if shp_count else f"zip ({len(names)} files)"
        elif ext == ".xlsx":
            return 0, "(Excel)"
        elif ext == ".pdf":
            return 0, "(PDF)"
    except Exception:
        pass
    return 0, "(?)"


def scan(juris_filter: str | None = None, layer_filter: str | None = None):
    root = C.RAW_DIR
    if not root.exists():
        print(f"Data lake not found at {root}")
        return

    jurisdictions = sorted(root.iterdir()) if root.exists() else []
    jurisdictions = [p for p in jurisdictions if p.is_dir()]

    if juris_filter:
        jurisdictions = [p for p in jurisdictions if p.name.upper() == juris_filter.upper()]
        if not jurisdictions:
            print(f"No jurisdiction matching '{juris_filter}'")
            return

    total_size = 0
    total_features = 0
    all_rows: list[tuple[str, str, str, str, str, int, str]] = []

    for jdir in jurisdictions:
        juris = jdir.name
        for code_dir in sorted(jdir.iterdir()):
            if not code_dir.is_dir():
                continue
            if layer_filter and code_dir.name != layer_filter:
                continue

            dates = sorted(code_dir.iterdir())
            if not dates:
                continue
            latest = dates[-1]

            all_files = sorted(latest.iterdir())
            data_files = [f for f in all_files if f.name != "_source.json"]

            if not data_files:
                continue

            layer_size = 0
            layer_feats = 0
            layer_details: list[str] = []

            for f in data_files:
                sz = f.stat().st_size
                layer_size += sz
                feats, desc = _count_features(f)
                layer_feats += feats
                total_features += feats
                if desc and desc != "(?)":
                    layer_details.append(f"{f.suffix or 'raw'}={desc}")
                else:
                    layer_details.append(f.suffix or "raw")

            total_size += layer_size
            detail_str = " + ".join(layer_details[:5])
            if len(layer_details) > 5:
                detail_str += f" +{len(layer_details)-5} more"
            all_rows.append((juris, code_dir.name, len(data_files),
                             _fmt(layer_size), layer_feats, detail_str))

    # Print
    print(f"\n{'Jurs':<6} {'Layer':<30} {'Files':>5} {'Size':>10} {'Features':>10}  Details")
    print("-" * 80)
    jurs_last = ""
    for jurs, code, nf, sz, feats, detail in sorted(all_rows, key=lambda r: (r[0], r[1])):
        prefix = jurs if jurs != jurs_last else ""
        jurs_last = jurs if jurs_last != jurs else jurs_last
        print(f"{prefix:<6} {code:<30} {nf:>5} {sz:>10} {feats:>10,}  {detail}")
    print("-" * 80)
    print(f"{'':6} {'':30} {'':5} {_fmt(total_size):>10} {total_features:>10,}  TOTAL")
    print()


def show_features(path: str, n: int = 5):
    p = C.RAW_DIR / path
    if not p.exists():
        print(f"Not found: {p}")
        return
    if p.is_dir():
        dates = sorted(p.iterdir())
        if not dates:
            print(f"No harvests in {p}")
            return
        latest = sorted(dates)[-1]
        geojsons = list(latest.glob("*.geojson")) or list(latest.glob("*.json"))
        if not geojsons:
            print(f"No GeoJSON in {latest}")
            return
        p = geojsons[0]
    if p.suffix.lower() not in (".geojson", ".json"):
        print(f"Feature view only for GeoJSON. File: {p}")
        return
    data = json.loads(p.read_bytes())
    feats = data.get("features", [])
    print(f"{len(feats):,d} features in {p}")
    for i, f in enumerate(feats[:n]):
        props = f.get("properties") or f.get("attributes", {})
        geom = f.get("geometry", {})
        print(f"\n  [{i}] {geom.get('type', '?')}")
        for k, v in list(props.items())[:10]:
            print(f"      {k}: {v}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--feat":
        show_features(args[1], int(args[2]) if len(args) > 2 else 5)
    elif args:
        parts = args[0].split("/", 1)
        if len(parts) == 2:
            scan(parts[0], parts[1])
        else:
            scan(parts[0])
    else:
        scan()
