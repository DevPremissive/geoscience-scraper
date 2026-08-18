#!/usr/bin/env python3
"""
negatives.py — the barren-negative pipeline (C2.4).

Implements Master §2 doctrine exactly, and the doctrine is unusually strict:

  **The only true negative is barren drilling.** Not unstaked ground, not
  dropped ground, not ground nobody has looked at. A negative is a specific
  hole, at a place, tested for specific commodities over a specific interval.

  **Negatives are asymmetric.** Absence of restaking may SUPPORT a provisional
  negative; presence of restaking only REMOVES a hole from the negative set. It
  is never itself a positive label. That asymmetry is deliberate and is the
  easiest thing in this file to accidentally break.

Tier 1 (provisional) is SQL-definable from data on hand. Tier 2 (confirmed)
requires retrieving the assessment report behind the hole and is C5.2's job;
this module emits tier 1 and leaves the field for the upgrade.

**Hole type is filtered FIRST, and that is a correctness requirement.** Ontario's
OMEIS layer carries 12,792 overburden auger holes, 11,264 sonic, 5,745
percussion and 6,332 underground alongside 135,239 diamond drill holes. An
overburden auger hole that found no gold is not evidence that no gold is there —
it never reached bedrock. Treating it as a negative would poison both the veto
in screening and the calibration set in training.

Usage:
    python src/negatives.py --build ON
    python src/negatives.py --stats
"""
from __future__ import annotations
import argparse, json, sys
import datetime as dt
from pathlib import Path

import config as C

OUT_PATH = C.PROCESSED_DIR / "negatives.parquet"

#: Hole types that actually test bedrock. Everything else is excluded before any
#: hole enters the pipeline — see the module docstring.
EXPLORATION_HOLE_TYPES = {
    "Diamond Drill Hole", "Wedged Diamond Drill Hole", "Underground Drilling",
}

#: A hole is not barren if a known occurrence sits this close.
OCCURRENCE_RADIUS_M = 500.0
#: A hole with a later hole this close was followed up — not abandoned.
FOLLOWUP_RADIUS_M = 500.0
#: Years after the hole in which restaking disqualifies it as a negative.
RESTAKE_LOOKAHEAD_YEARS = 7

DRILL_LAYER = {"ON": "ON__ON_OMEIS_DRILLHOLE"}
OCCURRENCE_LAYER = {"ON": "ON__ON_OMEIS_MINERAL_INV"}


def build(juris: str = "ON", write: bool = True):
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    layer = DRILL_LAYER.get(juris)
    if not layer:
        sys.exit(f"no drillhole layer registered for {juris}")
    g = gpd.read_file(C.GPKG_PATH, layer=layer).to_crs("EPSG:3978")
    print(f"  {layer.split('__')[-1]}: {len(g):,} holes")

    # 1. hole type FIRST
    before = len(g)
    if "HOLE_TYPE" in g.columns:
        g = g[g["HOLE_TYPE"].isin(EXPLORATION_HOLE_TYPES)].copy()
    print(f"  exploration holes: {len(g):,} "
          f"({before-len(g):,} excluded — overburden/percussion/sonic never "
          f"reached bedrock)")
    if g.empty:
        sys.exit("no exploration holes")

    # 2. no occurrence within 500 m
    occ_layer = OCCURRENCE_LAYER.get(juris)
    occ = gpd.read_file(C.GPKG_PATH, layer=occ_layer).to_crs("EPSG:3978")
    near_occ = gpd.sjoin_nearest(g[["geometry"]], occ[["geometry"]],
                                 max_distance=OCCURRENCE_RADIUS_M, how="inner")
    has_occ = set(near_occ.index)
    print(f"  {len(has_occ):,} holes within {OCCURRENCE_RADIUS_M:.0f} m of a known "
          f"occurrence — not barren")

    # 3. no LATER hole within 500 m (follow-up drilling)
    year = pd.to_numeric(g.get("YEAR_DRILLED"), errors="coerce")
    g = g.assign(_year=year)
    pairs = gpd.sjoin_nearest(g[["_year", "geometry"]], g[["_year", "geometry"]],
                              max_distance=FOLLOWUP_RADIUS_M, how="inner",
                              lsuffix="a", rsuffix="b")
    pairs = pairs[pairs["_year_b"] > pairs["_year_a"]]
    followed = set(pairs.index)
    print(f"  {len(followed):,} holes have a later hole within "
          f"{FOLLOWUP_RADIUS_M:.0f} m — followed up, not abandoned")

    # 4. no staking overlapping the site within N years AFTER the hole.
    #    ASYMMETRY: this can only REMOVE a hole from the negative set.
    restaked = set()
    ev_p = C.PROCESSED_DIR / "tenure_events.parquet"
    if ev_p.exists():
        import h3
        ev = pd.read_parquet(ev_p)
        ev = ev[(ev["juris"] == juris) & (ev["event_type"] == "staked")]
        ev["year"] = pd.to_datetime(ev["event_window_start"],
                                    errors="coerce").dt.year
        stake_by_cell: dict = {}
        for r in ev[["cell_r7", "year"]].dropna().itertuples(index=False):
            stake_by_cell.setdefault(r.cell_r7, []).append(int(r.year))
        # Drillholes are POINTS, so representative_point() is the point itself
        # and avoids the geographic-CRS centroid warning entirely.
        pts4326 = g.to_crs(4326).geometry.representative_point()
        cells = [h3.latlng_to_cell(p.y, p.x, 7) for p in pts4326]
        for idx, cell, yr in zip(g.index, cells, g["_year"]):
            if not np.isfinite(yr):
                continue
            for sy in stake_by_cell.get(cell, ()):
                if yr < sy <= yr + RESTAKE_LOOKAHEAD_YEARS:
                    restaked.add(idx)
                    break
        print(f"  {len(restaked):,} holes had staking within "
              f"{RESTAKE_LOOKAHEAD_YEARS} years after — removed from the negative "
              f"set (never treated as a positive)")

    keep = g.index.difference(pd.Index(list(has_occ | followed | restaked)))
    neg = g.loc[keep].copy()

    elements = neg["ELEMENTS"] if "ELEMENTS" in neg.columns else None
    out = pd.DataFrame({
        "hole_id": neg.get("HOLE_IDENT", pd.Series(neg.index)).astype(str),
        "source_code": "ON_OMEIS_DRILLHOLE",
        "juris": juris,
        "longitude": neg.to_crs(4326).geometry.x,
        "latitude": neg.to_crs(4326).geometry.y,
        "depth_from_m": 0.0,
        "depth_to_m": pd.to_numeric(neg.get("LENGTH"), errors="coerce"),
        "commodities_tested": (elements.fillna("").astype(str)
                               if elements is not None else ""),
        "year_drilled": neg["_year"],
        "tier": 1,
        "review_status": "auto",
        "snapshot": dt.date.today().isoformat(),
    })
    out["evidence"] = json.dumps({
        "no_occurrence_within_m": OCCURRENCE_RADIUS_M,
        "no_followup_drilling_within_m": FOLLOWUP_RADIUS_M,
        "no_restake_within_years": RESTAKE_LOOKAHEAD_YEARS,
        "hole_types_included": sorted(EXPLORATION_HOLE_TYPES),
        "verdict_confidence": "provisional",
    })

    known_comm = int((out["commodities_tested"].str.strip() != "").sum())
    print(f"\n  TIER 1 NEGATIVES: {len(out):,} holes")
    print(f"    depth known        {int(out['depth_to_m'].notna().sum()):,}")
    print(f"    commodities known  {known_comm:,} "
          f"({100*known_comm/max(1,len(out)):.0f}%) — the rest are "
          f"(location, depth) with commodities UNKNOWN, which is a weaker "
          f"negative and is recorded as such")
    if write:
        out.to_parquet(OUT_PATH, index=False, compression="zstd")
        print(f"  → {OUT_PATH}")
    return out


def stats():
    import pandas as pd
    if not OUT_PATH.exists():
        sys.exit("no negatives.parquet")
    d = pd.read_parquet(OUT_PATH)
    print(f"  {len(d):,} negatives, tier {sorted(d['tier'].unique())}")
    print(f"  year range {d['year_drilled'].min():.0f}..{d['year_drilled'].max():.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", metavar="JURIS")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    C.require_lake()
    if args.build:
        build(args.build)
    elif args.stats:
        stats()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
