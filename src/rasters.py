#!/usr/bin/env python3
"""
rasters.py — raster ingest and zonal statistics (C0.4).

Two entry points, per PLAN_C0 0.4:

    ingest(src, code)      validate CRS, convert to a Cloud-Optimized GeoTIFF with
                           overviews, register it in processed/rasters/rasters.json
    zonal(cog, cells)      exact-extract zonal statistics against fabric cells,
                           returned in the long format the feature store wants

**Categorical rasters are a first-class case, not an afterthought.** The national
CGMC lithology raster — the first thing this module ingests — holds 34 class codes.
Averaging class codes produces a number that looks fine and means nothing, and
building overviews with `average` resampling silently invents classes that do not
exist. So `ingest()` takes `categorical=` and switches resampling accordingly, and
`zonal()` returns per-class area fractions plus a majority class rather than
mean/std.

The CGMC legend ships as a QGIS `.qml` style file misnamed `.gpkg` (audit I6), so
`load_qml_legend()` reads the value→label mapping out of it. Without that mapping
the raster's pixel values are meaningless, so the registry stores it alongside.

Usage:
    python src/rasters.py --ingest-cgmc
    python src/rasters.py --list
    python src/rasters.py --zonal <layer> --sample 1000
"""
from __future__ import annotations
import argparse, hashlib, json, sys
import xml.etree.ElementTree as ET
from pathlib import Path

import config as C

COG_DIR = C.PROCESSED_DIR / "rasters"
REGISTRY = COG_DIR / "rasters.json"

#: Statistics for continuous rasters. Categorical ones ignore these entirely.
CONTINUOUS_STATS = ("mean", "min", "max", "stdev")


def _sha256(path: Path, limit: int | None = None) -> str:
    """Digest a file. `limit` hashes only the first N bytes, for huge rasters."""
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 22):
            h.update(chunk)
            read += len(chunk)
            if limit and read >= limit:
                break
    return h.hexdigest()


def load_qml_legend(path) -> dict:
    """Extract a value→label mapping from a QGIS style document.

    Ontario aside, this is how the CGMC legend is published: a `.qml` XML file
    named `.gpkg`, which no GeoPackage reader will open (audit I6). Parsed as
    XML it yields the 34 `paletteEntry` rows that make the raster readable.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {}
    out = {}
    for el in root.iter():
        if el.tag not in ("paletteEntry", "category"):
            continue
        val, label = el.get("value"), el.get("label")
        if val is None:
            continue
        try:
            out[int(float(val))] = label or ""
        except ValueError:
            continue
    return out


def _registry() -> list:
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def _register(entry: dict) -> None:
    entries = [e for e in _registry() if e["layer"] != entry["layer"]]
    entries.append(entry)
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(sorted(entries, key=lambda e: e["layer"]),
                                   indent=2), encoding="utf-8")


def ingest(src, code: str, juris: str = "FED", categorical: bool = False,
           legend: dict | None = None, snapshot: str | None = None,
           assume_crs: str | None = None, crs_evidence: str | None = None) -> Path:
    """Convert `src` to a registered COG under processed/rasters/.

    Returns the COG path. Re-ingesting the same source is a no-op unless the
    source hash changed.

    **`assume_crs` is for a CRS that is determined, not guessed.** A raster with
    no CRS is normally refused, because inventing one silently misplaces every
    value it holds. But publishers do ship untagged rasters whose CRS is
    recoverable from evidence — the CMMI CD prospectivity GeoTIFF carries no CRS
    while its MVT sibling, same release and same team, declares EPSG:4326 on a
    grid with an identical size, transform and bounds (audit N3). Passing
    `assume_crs` requires `crs_evidence`, which is stored in the registry, so the
    assertion travels with the layer and can be argued with later. Without
    evidence the refusal stands.
    """
    import rasterio
    from rasterio.shutil import copy as rio_copy

    src = Path(src)
    # Validate arguments before touching the disk: a caller who passed an
    # unevidenced CRS assertion should hear about that, not about whatever the
    # filesystem says first.
    if assume_crs and not crs_evidence:
        raise ValueError(
            f"assume_crs={assume_crs!r} given for {src.name} with no crs_evidence. "
            f"An asserted CRS without a recorded reason is a guess with extra steps.")
    layer = f"{juris}__{code}__{src.stem}".replace(" ", "_").replace("-", "_")[:80]
    dst = COG_DIR / f"{layer}.tif"
    COG_DIR.mkdir(parents=True, exist_ok=True)

    # Hash a bounded prefix: the CGMC raster is 610 MB and the full digest is
    # recorded by the harvest ledger already — this only needs to detect change.
    src_hash = _sha256(src, limit=64 << 20)
    prior = next((e for e in _registry() if e["layer"] == layer), None)
    if prior and prior.get("source_hash") == src_hash and dst.exists():
        # "Unchanged" is a claim about the source. Before acting on it, confirm
        # the output still matches what the registry says about it — audit I1's
        # lesson was that a skip decision resting on a record rather than on the
        # file turns a repair into a silent no-op. Only the asserted CRS is
        # re-checked here; it is the one property this function writes that the
        # source cannot vouch for.
        stale = False
        if prior.get("crs_asserted"):
            try:
                import rasterio as _rio
                with _rio.open(dst) as _out:
                    stale = (_out.crs is None
                             or _out.crs.to_string() != prior.get("crs"))
            except Exception:
                stale = True
        if not stale:
            print(f"   = {layer} unchanged")
            return dst
        print(f"   ! {layer}: registry claims crs={prior.get('crs')} but the COG "
              f"on disk disagrees — re-ingesting")

    with rasterio.open(src) as ds:
        crs = ds.crs
        asserted = False
        if crs is None:
            if not assume_crs:
                raise ValueError(f"{src.name} has no CRS — refusing to guess one")
            crs = rasterio.crs.CRS.from_string(assume_crs)
            asserted = True
            print(f"   ~ {src.name}: no CRS on the file; asserting {assume_crs} "
                  f"— {crs_evidence}")
        meta = {
            "crs": str(crs),
            "width": ds.width, "height": ds.height, "count": ds.count,
            "dtype": ds.dtypes[0],
            "nodata": None if ds.nodata is None else float(ds.nodata),
            "resolution": [abs(ds.transform.a), abs(ds.transform.e)],
            "bounds": list(ds.bounds),
        }
        # Class codes must not be interpolated. `average` overviews on a
        # categorical raster produce values that are not any real class.
        resampling = "mode" if categorical else "average"
        if asserted:
            # Two things that do NOT work, both of which fail silently and leave
            # the registry describing a file that does not exist:
            #   rio_copy(ds, dst, crs=crs)  -> the kwarg is ignored outright
            #   open(dst, "r+").crs = crs   -> the COG driver does not take it
            # A WarpedVRT with src_crs == crs presents the same grid with the CRS
            # attached and warps nothing: identical size, transform and bounds.
            from rasterio.vrt import WarpedVRT
            with WarpedVRT(ds, src_crs=crs, crs=crs) as vrt:
                rio_copy(vrt, dst, driver="COG", compress="DEFLATE",
                         overview_resampling=resampling, BIGTIFF="IF_SAFER")
        else:
            rio_copy(ds, dst, driver="COG", compress="DEFLATE",
                     overview_resampling=resampling, BIGTIFF="IF_SAFER")

    if asserted:
        # Read it back. An assertion that does not survive to disk must fail the
        # ingest, not be recorded as fact.
        with rasterio.open(dst) as out:
            if out.crs is None or out.crs.to_string() != crs.to_string():
                dst.unlink(missing_ok=True)
                raise RuntimeError(
                    f"asserted CRS {crs.to_string()} did not survive to {dst.name} "
                    f"(got {out.crs}); refusing to register a layer whose file "
                    f"disagrees with its registry entry")

    entry = {
        "layer": layer, "code": code, "jurisdiction": juris,
        "path": str(dst), "categorical": categorical,
        "source": str(src), "source_hash": src_hash,
        "source_snapshot": snapshot or src.parent.name,
        "overview_resampling": resampling,
        "cog_bytes": dst.stat().st_size,
        **meta,
    }
    if asserted:
        entry["crs_asserted"] = True
        entry["crs_evidence"] = crs_evidence
    if legend:
        entry["legend"] = {str(k): v for k, v in sorted(legend.items())}
    _register(entry)
    print(f"   + {layer}  {meta['width']}x{meta['height']} {meta['dtype']} "
          f"{'categorical' if categorical else 'continuous'}  "
          f"{dst.stat().st_size/1e6:.0f}MB")
    return dst


def zonal(cog, cells, stats=CONTINUOUS_STATS, categorical: bool = False,
          feature_prefix: str | None = None):
    """Zonal statistics of `cog` over `cells`, as long-format feature rows.

    `cells` is a GeoDataFrame with `cell_id` and geometry. Returns a DataFrame of
    `(cell_id, feature, value)` — the shape the feature store stores, so a caller
    never has to reshape it.

    Cells are reprojected to the raster's CRS rather than the raster to the
    cells: the raster is the expensive side, and reprojecting 610 MB per call to
    match a hex grid would dominate the run.
    """
    import geopandas as gpd
    import pandas as pd
    import rasterio
    from exactextract import exact_extract

    with rasterio.open(cog) as ds:
        raster_crs = ds.crs
    if cells.crs is None:
        raise ValueError("cells have no CRS")
    cells_r = cells.to_crs(raster_crs) if cells.crs != raster_crs else cells

    ops = ["majority", "unique", "frac"] if categorical else list(stats)
    res = exact_extract(str(cog), cells_r, ops, output="pandas",
                        include_cols=["cell_id"] if "cell_id" in cells_r.columns else None)

    prefix = feature_prefix or Path(cog).stem
    rows = []
    for _, r in res.iterrows():
        cid = r.get("cell_id")
        if categorical:
            maj = r.get("majority")
            if maj is not None and not pd.isna(maj):
                rows.append((cid, f"{prefix}__majority", float(maj)))
            # frac/unique come back as parallel arrays: class codes and their
            # area share within the cell. Emitted per class so a consumer can
            # pivot only the classes it cares about.
            uniq, frac = r.get("unique"), r.get("frac")
            if uniq is not None and frac is not None:
                try:
                    for u, f in zip(list(uniq), list(frac)):
                        rows.append((cid, f"{prefix}__frac_{int(u)}", float(f)))
                except TypeError:
                    pass
        else:
            for s in stats:
                v = r.get(s)
                if v is not None and not pd.isna(v):
                    rows.append((cid, f"{prefix}__{s}", float(v)))
    return pd.DataFrame(rows, columns=["cell_id", "feature", "value"])


def sample_r7_cells(bounds, n: int = 1000):
    """Up to `n` r7 cells covering `bounds` (minx, miny, maxx, maxy).

    `n` is a cap, not a target: a bounding box contains however many r7 cells it
    contains, and the walk stops at the box edge.

    Standalone on purpose: C0.4's acceptance needs r7 cells before C0.5's fabric
    exists, and this must not become a second fabric implementation. It is a test
    fixture — `fabric.build_r7()` is the real thing.
    """
    import geopandas as gpd
    import h3
    from shapely.geometry import Polygon

    minx, miny, maxx, maxy = bounds
    step = 0.05
    seen, cells = set(), []
    lat = miny
    while lat <= maxy and len(cells) < n:
        lng = minx
        while lng <= maxx and len(cells) < n:
            # h3 4.x: latlng_to_cell / cell_to_boundary, and boundaries come back
            # as (lat, lng) pairs — reversed from GeoJSON order.
            cid = h3.latlng_to_cell(lat, lng, 7)
            if cid not in seen:
                seen.add(cid)
                ring = h3.cell_to_boundary(cid)
                cells.append((cid, Polygon([(lng_, lat_) for lat_, lng_ in ring])))
            lng += step
        lat += step
    return gpd.GeoDataFrame({"cell_id": [c for c, _ in cells]},
                            geometry=[g for _, g in cells], crs="EPSG:4326")


def ingest_cgmc():
    """Ingest the national CGMC lithology raster with its legend (C0.4 acceptance)."""
    base = C.RAW_DIR / "FED" / "CGMC"
    tifs = sorted(p for p in base.rglob("*")
                  if p.suffix.lower() in (".tif", ".tiff", ".geotif") and p.is_file())
    if not tifs:
        sys.exit("no CGMC raster on disk — harvest FED/CGMC first")
    src = max(tifs, key=lambda p: p.stat().st_size)

    legend = {}
    for cand in sorted(base.rglob("*.gpkg")):
        if "English" in cand.name or not legend:
            legend = load_qml_legend(cand) or legend
    print(f"   legend: {len(legend)} classes"
          + (f" (e.g. {legend.get(1)!r})" if legend else " — NOT FOUND"))
    return ingest(src, "CGMC", juris="FED", categorical=True, legend=legend)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest-cgmc", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--zonal", metavar="LAYER")
    ap.add_argument("--sample", type=int, default=1000)
    args = ap.parse_args()

    C.require_lake()
    if args.ingest_cgmc:
        ingest_cgmc()
    if args.list:
        for e in _registry():
            print(f"  {e['layer']:<52}{e['dtype']:<9}"
                  f"{'cat' if e['categorical'] else 'cont':<5}"
                  f"{e['cog_bytes']/1e6:>8.0f}MB  {e['crs']}")
            if e.get("legend"):
                print(f"      legend: {len(e['legend'])} classes")
    if args.zonal:
        import time
        entry = next((e for e in _registry() if e["layer"] == args.zonal), None)
        if not entry:
            sys.exit(f"no such registered raster: {args.zonal}")
        # A patch of the Abitibi greenstone belt in northeastern Ontario.
        cells = sample_r7_cells((-81.5, 48.0, -80.0, 49.0), n=args.sample)
        t = time.time()
        out = zonal(entry["path"], cells, categorical=entry["categorical"])
        print(f"  {len(cells)} r7 cells → {len(out)} feature rows "
              f"in {time.time()-t:.1f}s")
        print(out.head(12).to_string(index=False))
        if entry.get("legend"):
            maj = out[out.feature.str.endswith("__majority")]
            if not maj.empty:
                top = maj.value.value_counts().head(5)
                print("\n  dominant lithology by cell count:")
                for val, cnt in top.items():
                    print(f"    {cnt:>5}  {entry['legend'].get(str(int(val)), '?')}")


if __name__ == "__main__":
    main()
