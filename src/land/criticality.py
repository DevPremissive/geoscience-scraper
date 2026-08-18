#!/usr/bin/env python3
"""
criticality.py — how badly a neighbour needs a given open cell (C1.5).

For each (block, open r9 cell) pair, three sub-scores are computed and combined
by **max, never by blending**:

  on_trend    the cell lies in the direction the block's geology is heading
  gap_closing the cell is a hole between two blocks of the SAME owner
  chokepoint  the cell covers a large share of the block's on-trend frontier

Max-combination with reason codes is a deliberate choice from PLAN_C1 1.5:
interpretability over elegance. A weighted blend would produce a 0.62 that
nobody can argue with or against; a max plus `reason_codes` produces "0.8,
because it closes a consolidation gap", which a landman can agree or disagree
with. Every score explains itself.

**`trend_source` is recorded on every row, and "none" is a real answer.** The
priority order is: a human-entered azimuth (C4) always wins; then the strike of
mapped structures crossing the block; then the alignment axis of occurrence and
drillhole points inside it, but only when there are enough points and they
actually line up. If none of those hold, on_trend is **0 and the source is
`none`** — never a guessed bearing. A fabricated trend is worse than no trend,
because the whole point of the score is to say which direction matters.

Usage:
    python -m land.criticality --build --aoi abitibi
    python -m land.criticality --top 20
    python -m land.criticality --block ON-B000123
"""
from __future__ import annotations
import argparse, json, math, sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

OUT_PATH = C.PROCESSED_DIR / "criticality.parquet"
DB_PATH = C.PROCESSED_DIR / "ownership.duckdb"

#: Structures within this distance of a block inform its trend.
TREND_SEARCH_M = 2000.0
#: A point cloud needs this many points, and this much of its variance on the
#: first component, before its long axis counts as a trend.
PCA_MIN_POINTS = 5
PCA_MIN_EXPLAINED = 0.70
#: On-trend score decays to ~0 at this distance from the block boundary.
TREND_DECAY_M = 3000.0
#: Corridor half-width as a multiple of the block's cross-trend extent.
CORRIDOR_WIDTH_FACTOR = 1.0
#: Same-owner blocks within this distance can define a consolidation gap.
GAP_SEARCH_M = 5000.0


def _bearing_of_line(geom) -> float | None:
    """Dominant bearing (0–180°) of a line, from its endpoints."""
    try:
        coords = list(geom.coords) if geom.geom_type == "LineString" else \
            [c for part in geom.geoms for c in part.coords]
    except Exception:                                           # noqa: BLE001
        return None
    if len(coords) < 2:
        return None
    (x0, y0), (x1, y1) = coords[0], coords[-1]
    if x0 == x1 and y0 == y1:
        return None
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 180.0


def _circular_mean_180(bearings, weights=None):
    """Mean of axial (0–180°) bearings. Doubling handles the wrap at 180."""
    import numpy as np
    if not len(bearings):
        return None
    b = np.radians(np.asarray(bearings, dtype=float) * 2.0)
    w = np.ones_like(b) if weights is None else np.asarray(weights, dtype=float)
    ang = math.atan2(float(np.sum(w * np.sin(b))), float(np.sum(w * np.cos(b))))
    return (math.degrees(ang) / 2.0) % 180.0


def trend_for_block(block_geom, structures, points, manual: float | None = None):
    """(azimuth, source) for a block. Returns (None, 'none') rather than guessing."""
    import numpy as np

    if manual is not None:
        return float(manual) % 180.0, "manual"

    # (b) strike of mapped structures crossing or near the block
    if structures is not None and len(structures):
        near = structures[structures.geometry.intersects(
            block_geom.buffer(TREND_SEARCH_M))]
        if len(near):
            bs, ws = [], []
            for g in near.geometry:
                b = _bearing_of_line(g)
                if b is not None:
                    bs.append(b)
                    ws.append(g.length)       # longer structures weigh more
            if bs:
                return _circular_mean_180(bs, ws), "structure_strike"

    # (c) alignment axis of points inside the block — only if they really align
    if points is not None and len(points):
        inside = points[points.geometry.within(block_geom)]
        if len(inside) >= PCA_MIN_POINTS:
            xy = np.column_stack([inside.geometry.x, inside.geometry.y])
            xy = xy - xy.mean(axis=0)
            try:
                _u, s, vt = np.linalg.svd(xy, full_matrices=False)
                explained = (s[0] ** 2) / float(np.sum(s ** 2))
                if explained > PCA_MIN_EXPLAINED:
                    vx, vy = vt[0]
                    return math.degrees(math.atan2(vx, vy)) % 180.0, "point_axis"
            except Exception:                                   # noqa: BLE001
                pass

    # (d) nothing defensible
    return None, "none"


def _corridor(block_geom, azimuth: float, reach: float):
    """Block hull swept along ±azimuth — the ground the trend points at."""
    from shapely import affinity
    from shapely.ops import unary_union
    hull = block_geom.convex_hull
    dx = math.sin(math.radians(azimuth))
    dy = math.cos(math.radians(azimuth))
    steps = 12
    swept = [affinity.translate(hull, xoff=dx * reach * k / steps,
                                yoff=dy * reach * k / steps)
             for k in range(-steps, steps + 1)]
    return unary_union(swept)


def build(aoi_id: str = "abitibi", juris: str = "ON", min_claims: int = 5,
          max_blocks: int = 400, write: bool = True):
    import duckdb
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from shapely import wkt

    if not DB_PATH.exists():
        sys.exit("no ownership.duckdb — run land.ownership_graph --build first")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    blocks = con.execute(
        "SELECT block_id, owner_id, n_claims, area_ha, geometry_wkt "
        "FROM blocks WHERE n_claims >= ? ORDER BY n_claims DESC LIMIT ?",
        [min_claims, max_blocks]).fetchdf()
    con.close()
    if blocks.empty:
        sys.exit("no blocks of that size")
    blocks["geometry"] = blocks["geometry_wkt"].map(wkt.loads)
    blocks = gpd.GeoDataFrame(blocks.drop(columns=["geometry_wkt"]),
                              geometry="geometry", crs="EPSG:3978")

    state = C.PROCESSED_DIR / "land_state" / f"{juris}__{aoi_id}.parquet"
    if not state.exists():
        sys.exit(f"no land_state for {aoi_id} — run land.open_ground first")
    cells = gpd.read_parquet(state).set_crs(4326, allow_override=True)
    cells = cells[cells["state"] == "open"].to_crs("EPSG:3978")
    if cells.empty:
        sys.exit("no open cells in AOI")

    aoi = cells.union_all().envelope
    blocks = blocks[blocks.geometry.intersects(aoi)].copy()
    print(f"  {len(blocks):,} blocks x {len(cells):,} open cells in {aoi_id}")
    if blocks.empty:
        sys.exit("no blocks intersect the AOI")

    bbox4326 = gpd.GeoSeries([aoi], crs="EPSG:3978").to_crs(4326).total_bounds
    structures = gpd.read_file(C.GPKG_PATH, layer="ON__ON_GEOL_FAULTS",
                               bbox=tuple(bbox4326)).to_crs("EPSG:3978")
    pts = gpd.read_file(C.GPKG_PATH, layer="ON__ON_OMEIS_DRILLHOLE",
                        bbox=tuple(bbox4326)).to_crs("EPSG:3978")
    print(f"  trend inputs: {len(structures):,} structures, {len(pts):,} drillholes")

    # Same-owner block index, for gap closing.
    by_owner: dict = {}
    for r in blocks.itertuples(index=False):
        by_owner.setdefault(r.owner_id, []).append(r.geometry)

    cell_idx = cells.sindex
    rows = []
    for b in blocks.itertuples(index=False):
        az, source = trend_for_block(b.geometry, structures, pts)

        # Candidate cells: those near the block at all.
        near_ids = list(cell_idx.query(b.geometry.buffer(TREND_DECAY_M),
                                       predicate="intersects"))
        if not near_ids:
            continue
        cand = cells.iloc[near_ids]

        corridor = None
        if az is not None:
            corridor = _corridor(b.geometry, az, TREND_DECAY_M)

        # --- gap closing: cells between two blocks of the same owner
        siblings = [g for g in by_owner.get(b.owner_id, []) if g is not b.geometry]
        gap_zone = None
        if siblings:
            near_sibs = [g for g in siblings
                         if g.distance(b.geometry) <= GAP_SEARCH_M]
            if near_sibs:
                from shapely.ops import unary_union
                gap_zone = b.geometry.buffer(GAP_SEARCH_M).intersection(
                    unary_union([g.buffer(GAP_SEARCH_M) for g in near_sibs]))

        # --- chokepoint denominator: how much frontier is on-trend at all
        on_trend_total = 0
        if corridor is not None:
            on_trend_total = int(cand.geometry.intersects(corridor).sum())

        for c in cand.itertuples(index=False):
            reasons, score = [], 0.0
            d = c.geometry.distance(b.geometry)

            if corridor is not None and c.geometry.intersects(corridor):
                s = max(0.0, 1.0 - d / TREND_DECAY_M)
                if s > score:
                    score = s
                reasons.append(f"on_trend:{source}:{az:.0f}deg")

            if gap_zone is not None and c.geometry.intersects(gap_zone):
                if 1.0 > score:
                    score = 1.0
                reasons.append("gap_closing:same_owner_consolidation")

            if on_trend_total and corridor is not None and \
                    c.geometry.intersects(corridor):
                choke = 1.0 / on_trend_total
                if choke > score:
                    score = choke
                if on_trend_total <= 5:
                    reasons.append(f"chokepoint:1_of_{on_trend_total}_on_trend_cells")

            if not reasons:
                continue
            rows.append({
                "block_id": b.block_id, "owner_id": b.owner_id,
                "cell_id": c.cell_id, "score": round(float(score), 4),
                "reason_codes": json.dumps(reasons),
                "trend_source": source,
                "trend_azimuth": None if az is None else round(float(az), 1),
                "distance_m": round(float(d), 1),
                "computed_at": dt.datetime.now().isoformat(timespec="seconds"),
            })

    df = pd.DataFrame(rows)
    if df.empty:
        print("  no criticality pairs produced")
        return df
    src = df.groupby("trend_source")["block_id"].nunique()
    print(f"\n  {len(df):,} (block, cell) pairs over "
          f"{df['block_id'].nunique():,} blocks")
    print("  trend sources (blocks):")
    for s, n in src.items():
        print(f"    {s:<20}{n:>5}")
    print(f"  score: median {df['score'].median():.3f}  max {df['score'].max():.3f}")
    if write:
        df.to_parquet(OUT_PATH, index=False, compression="zstd")
        print(f"  → {OUT_PATH}")
    return df


def top(n: int = 20):
    import pandas as pd
    import duckdb
    if not OUT_PATH.exists():
        sys.exit("no criticality.parquet — run --build first")
    df = pd.read_parquet(OUT_PATH).nlargest(n, "score")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    names = con.execute("SELECT owner_id, name_raw FROM owners").fetchdf()
    con.close()
    df = df.merge(names, on="owner_id", how="left")
    print(f"  top {n} critical (block, cell) pairs — every score self-explaining:\n")
    for r in df.itertuples(index=False):
        why = ", ".join(json.loads(r.reason_codes))
        print(f"  {r.score:.3f}  {r.cell_id:<17}{str(r.name_raw)[:30]:<32}"
              f"{r.distance_m:>8.0f}m  {why}")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--aoi", default="abitibi")
    ap.add_argument("--min-claims", type=int, default=5)
    ap.add_argument("--top", type=int, default=0)
    args = ap.parse_args()
    C.require_lake()
    if args.build:
        build(args.aoi, min_claims=args.min_claims)
    if args.top:
        top(args.top)
    if not (args.build or args.top):
        ap.print_help()


if __name__ == "__main__":
    main()
