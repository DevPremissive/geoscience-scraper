#!/usr/bin/env python3
"""
helpers.py — materialised lookups the screen SQL joins against (C4.3).

Two things the compiler needs that neither the fabric nor DuckDB provides:

  **Cell centroids.** `fabric/r7_<juris>.parquet` stores `cell_id` and WKB
  geometry, and the H3 id already determines the centroid exactly — but a
  DuckDB query cannot call `h3.cell_to_latlng`, and parsing 164,577 WKB
  polygons to find their centres inside every screen would be absurd. Computed
  once here and cached.

  **Point classes assigned to cells.** `count(nonbarren_holes, 1km)` needs the
  points bucketed onto the fabric. Doing that with a lat/lon band join inside
  the screen works but recomputes on every run; doing it once per source file
  is cheaper and, more importantly, makes the assignment identical across runs
  of the same screen — which is what C4.3's "re-run identically" acceptance is
  about.

Everything is cached on `(source path, mtime)` and rebuilt when the source
changes, the same rule `mapapi` uses. Cache files live under
`processed/screens/_cache/` and are safe to delete.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

CACHE = C.PROCESSED_DIR / "screens" / "_cache"


def _stale(out: Path, *sources: Path) -> bool:
    if not out.exists():
        return True
    t = out.stat().st_mtime
    return any(s.exists() and s.stat().st_mtime > t for s in sources)


def cell_centroids(juris: str = "ON") -> Path:
    """`cell_id, lat, lon` for every cell in the jurisdiction's r7 fabric."""
    import pandas as pd
    import h3

    src = C.PROCESSED_DIR / "fabric" / f"r7_{juris}.parquet"
    if not src.exists():
        raise FileNotFoundError(f"no r7 fabric for {juris}")
    out = CACHE / f"cells_r7_{juris}.parquet"
    if not _stale(out, src):
        return out
    CACHE.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(src, columns=["cell_id", "terrane_id", "area_km2"])
    ll = [h3.cell_to_latlng(c) for c in df["cell_id"]]
    df["lat"] = [a for a, _ in ll]
    df["lon"] = [b for _, b in ll]
    df.to_parquet(out, index=False)
    return out


def points_on_fabric(name: str, spec: dict, juris: str = "ON") -> Path:
    """A point class bucketed onto r7, as `cell_id, n`.

    Assignment is by the point's own H3 cell, not by distance to a centroid: a
    point is *in* exactly one hex, and counting it into whichever centroid
    happens to be nearest would double-count along every shared edge."""
    import pandas as pd
    import h3

    out = CACHE / f"points_{name}_{juris}.parquet"
    if "path" in spec:
        src = Path(spec["path"])
        if not src.exists():
            raise FileNotFoundError(f"{name}: {src} not on disk")
        if not _stale(out, src):
            return out
        df = pd.read_parquet(src, columns=[spec["lat"], spec["lon"]])
        lat, lon = spec["lat"], spec["lon"]
    else:
        import duckdb
        con = duckdb.connect(str(C.CATALOG_DB), read_only=True)
        con.execute("LOAD spatial")
        try:
            df = con.execute(
                f'SELECT "{spec["lat"]}" AS lat, "{spec["lon"]}" AS lon '
                f'FROM "{spec["table"]}" '
                f'WHERE "{spec["lat"]}" IS NOT NULL').fetchdf()
        finally:
            con.close()
        lat, lon = "lat", "lon"
        if not _stale(out, C.CATALOG_DB):
            return out

    CACHE.mkdir(parents=True, exist_ok=True)
    df = df.dropna(subset=[lat, lon])
    cells = [h3.latlng_to_cell(float(a), float(b), 7)
             for a, b in zip(df[lat], df[lon])]
    counts = pd.Series(cells).value_counts().rename_axis("cell_id")
    res = counts.reset_index(name="n")
    res.to_parquet(out, index=False)
    return out


def points_with_radius(name: str, spec: dict, radius_km: float,
                       juris: str = "ON") -> Path:
    """Point counts within `radius_km` of each cell, via H3 ring expansion.

    A hex ring is the natural radius here: r7 edge length is ~1.4 km, so k rings
    out is roughly 1.4k km and the shape is a hexagon rather than a circle. That
    is coarser than a true buffer and enormously cheaper than a spatial join
    over 164,577 cells, and for a screening predicate — "is there a non-barren
    hole near this" — the difference between a hexagon and a circle at 1 km is
    not a difference anyone is deciding on."""
    import pandas as pd
    import h3

    base = points_on_fabric(name, spec, juris)
    k = max(0, round(radius_km / 1.4))
    out = CACHE / f"points_{name}_{juris}_k{k}.parquet"
    if k == 0:
        return base
    if not _stale(out, base):
        return out
    pts = pd.read_parquet(base)
    spread: dict = {}
    for cell, n in zip(pts["cell_id"], pts["n"]):
        for c in h3.grid_disk(cell, k):
            spread[c] = spread.get(c, 0) + int(n)
    res = pd.DataFrame({"cell_id": list(spread), "n": list(spread.values())})
    CACHE.mkdir(parents=True, exist_ok=True)
    res.to_parquet(out, index=False)
    return out


def barren_cells(commodity: str | None = None,
                 min_depth_m: float | None = None) -> Path:
    """Cells holding a barren hole, filtered by what the hole actually tested.

    Master §2: a hole barren for gold does not test a lithium thesis, so the
    filter is on `commodities_tested` and not on the mere existence of a
    negative."""
    import pandas as pd
    import h3

    src = C.PROCESSED_DIR / "negatives.parquet"
    if not src.exists():
        raise FileNotFoundError("negatives.parquet missing — run negatives.py")
    tag = f"{commodity or 'any'}_{min_depth_m or 0:.0f}"
    out = CACHE / f"barren_{tag}.parquet"
    if not _stale(out, src):
        return out
    df = pd.read_parquet(src)
    if commodity:
        df = df[df["commodities_tested"].astype(str)
                  .str.contains(commodity, case=False, na=False)]
    if min_depth_m and "depth_to_m" in df.columns:
        df = df[df["depth_to_m"].fillna(0) >= float(min_depth_m)]
    df = df.dropna(subset=["latitude", "longitude"])
    cells = sorted({h3.latlng_to_cell(float(a), float(b), 7)
                    for a, b in zip(df["latitude"], df["longitude"])})
    CACHE.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"cell_id": cells}).to_parquet(out, index=False)
    return out
