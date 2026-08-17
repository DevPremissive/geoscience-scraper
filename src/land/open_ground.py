#!/usr/bin/env python3
"""
open_ground.py — what ground is actually open (C1.1).

Open ground = jurisdiction landmass minus the union of every encumbrance layer,
computed as a **cell-state table** on r9 cells within an AOI, plus dissolved
polygons for map display.

The output is a state per cell rather than a boolean, because "not open" is a
different conversation depending on why:

    open        nothing known blocks staking
    claimed     an active cell claim covers it
    alienated   withdrawn from staking by an alienation order
    withdrawn   already disposed — mining or non-mining land tenure
    park        regulated provincial or national park
    unknown     a known encumbrance type exists here that we cannot locate

`unknown` is a real state and is reported, never folded into `open`. The
distinction matters more here than anywhere else in the system: the terminal
output of this project is a recommendation to spend money staking ground, and a
cell that is silently called open because its encumbrance layer was never
harvested is the most expensive possible error.

Usage:
    python -m land.open_ground --aoi abitibi
    python -m land.open_ground --aoi abitibi --sample 15
"""
from __future__ import annotations
import argparse, json, sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
import fabric as F

STATE_DIR = C.PROCESSED_DIR / "land_state"

#: Encumbrance layers per jurisdiction, in precedence order — the first match
#: wins, so a claim inside a park reports `claimed`, which is the reason a
#: prospector actually cares about. `filter` restricts a layer to the rows that
#: genuinely encumber.
SUBTRACTION_STACK = {
    "ON": [
        ("claimed",   "ON__ON_MLAS_TENURE__Operational_Cell_Claims",   None),
        ("alienated", "ON__ON_MLAS_TENURE__Operational_Alienations",   None),
        ("withdrawn", "ON__ON_MLAS_TENURE__Mining_Land_Tenure",        None),
        ("withdrawn", "ON__ON_MLAS_TENURE__Non_Mining_Land_Tenure",    None),
        ("withdrawn", "ON__ON_MLAS_TENURE__Plans_Permits",             None),
        # Authoritative LIO protected areas, replacing the MRD126 basemap
        # polygons this originally used. That layer was park outlines shipped
        # inside a 1:250 000 bedrock geology map — 336 parks against LIO's 347,
        # i.e. eleven parks stale, with no conservation reserves at all.
        ("park",      "ON__ON_PARKS_REGULATED",                        None),
        ("park",      "ON__ON_CONSERVATION_RESERVE",                   None),
        ("park",      "ON__ON_FEDERAL_PROTECTED",                      None),
        ("withdrawn", "ON__ON_INDIAN_RESERVE",                         None),
    ],
}

#: Harvested and available, but NOT subtracted, because whether each actually
#: bars staking is a legal question rather than a data question. MASTER §8 puts
#: that in C1.2's human-verified rules/<juris>.yaml and says no scraper
#: substitutes for it. Listed here so the choice is visible rather than an
#: omission somebody has to notice.
PENDING_LEGAL_REVIEW = {
    "ON": [
        ("ON__ON_LANDFORM_CONSERVATION", 244,
         "Growth Plan planning designation — restricts development, but does "
         "not obviously withdraw land from staking."),
        ("ON__ON_MUNICIPAL_PARK", 388,
         "Municipal land. Surface rights and mining rights are frequently "
         "severed in Ontario, so a municipal park does not by itself imply the "
         "mining rights are unavailable."),
        ("ON__ON_PARK_ADMIN_ZONE", 5,
         "Administrative envelope, larger than the regulated boundary. "
         "ON_PARKS_REGULATED is the layer with legal effect."),
    ],
}

#: Encumbrance types known to exist in a jurisdiction with no layer on disk.
#: Recorded per jurisdiction because an unharvested layer cannot be located —
#: we know these cells exist, we just cannot say which they are, so the caveat
#: travels with the result instead of being silently dropped.
MISSING_ENCUMBRANCES = {
    # Ontario's protected-area gap was closed on 2026-08-17 by registering the
    # LIO layers. What remains is a question of legal interpretation, not of
    # missing data — see PENDING_LEGAL_REVIEW.
    "ON": [],
}


def _load_encumbrance(layer, flt, bbox):
    """Read one encumbrance layer, clipped to the AOI bbox."""
    import geopandas as gpd
    try:
        g = gpd.read_file(C.GPKG_PATH, layer=layer, bbox=bbox)
    except Exception as e:                                      # noqa: BLE001
        print(f"   ! {layer}: {str(e)[:70]}", file=sys.stderr)
        return None
    if g.empty:
        return g
    if flt:
        col, val = flt
        if col in g.columns:
            g = g[g[col] == val]
    return g.to_crs(4326) if g.crs and g.crs.to_epsg() != 4326 else g


def compute(aoi_id: str, juris: str = "ON", write: bool = True):
    """Assign a land state to every r9 cell in an AOI."""
    import geopandas as gpd
    import pandas as pd

    p = F.R9_DIR / f"{aoi_id}.parquet"
    if not p.exists():
        sys.exit(f"no r9 fabric for {aoi_id} — run fabric.py --build-r9 first")
    cells = gpd.read_parquet(p)
    if cells.crs is None:
        cells.set_crs(4326, inplace=True)
    print(f"  AOI {aoi_id}: {len(cells):,} r9 cells")

    cells["state"] = "open"
    cells["blocking_layer"] = None
    bbox = tuple(cells.total_bounds)

    for state, layer, flt in SUBTRACTION_STACK.get(juris, []):
        g = _load_encumbrance(layer, flt, bbox)
        if g is None or g.empty:
            print(f"   {layer.split('__')[-1]:<34} no features in AOI")
            continue
        # Only cells still open can be claimed by a later layer — precedence is
        # the point of the ordering.
        todo = cells[cells["state"] == "open"]
        if todo.empty:
            break
        hit = gpd.sjoin(todo[["cell_id", "geometry"]], g[["geometry"]],
                        predicate="intersects", how="inner")
        ids = set(hit["cell_id"])
        cells.loc[cells["cell_id"].isin(ids), "state"] = state
        cells.loc[cells["cell_id"].isin(ids), "blocking_layer"] = layer
        print(f"   {layer.split('__')[-1]:<34} {len(g):>7,} features → "
              f"{len(ids):>7,} cells {state}")

    snapshot = dt.date.today().isoformat()
    cells["juris"] = juris
    cells["as_of_snapshot"] = snapshot

    counts = cells["state"].value_counts()
    total = len(cells)
    print("\n  land state:")
    for st, n in counts.items():
        print(f"    {st:<12}{n:>8,}  {100*n/total:5.1f}%")

    unknown_pct = 100 * counts.get("unknown", 0) / total
    pending = PENDING_LEGAL_REVIEW.get(juris, [])
    if pending:
        print(f"\n  ! {len(pending)} harvested layer(s) NOT subtracted, pending "
              f"legal review in C1.2:")
        for lyr, n, why in pending:
            print(f"    - {lyr.split('__')[-1]} ({n:,}): {why[:88]}…")
    missing = MISSING_ENCUMBRANCES.get(juris, [])
    if missing:
        print(f"\n  ! {len(missing)} encumbrance type(s) known-missing for {juris}:")
        for m in missing:
            extent = f" (~{m['approx_km2']:,} km²)" if m.get("approx_km2") else ""
            print(f"    - {m['type']}{extent}: {m['note'][:96]}…")

    if write:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        out = STATE_DIR / f"{juris}__{aoi_id}.parquet"
        cells.to_parquet(out, index=False)
        meta = {
            "aoi_id": aoi_id, "jurisdiction": juris, "snapshot": snapshot,
            "cells": int(total),
            "state_counts": {k: int(v) for k, v in counts.items()},
            "unknown_pct": round(unknown_pct, 3),
            "subtraction_stack": [
                {"state": s, "layer": l, "filter": list(f) if f else None}
                for s, l, f in SUBTRACTION_STACK.get(juris, [])],
            "missing_encumbrances": missing,
            "pending_legal_review": [
                {"layer": l, "features": n, "why": w} for l, n, w in pending],
        }
        (STATE_DIR / f"{juris}__{aoi_id}.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")

        # Dissolved open ground for map display.
        openg = cells[cells["state"] == "open"]
        if not openg.empty:
            diss = gpd.GeoDataFrame(
                geometry=[openg.geometry.union_all()], crs=cells.crs)
            diss["km2"] = diss.to_crs("EPSG:6933").area / 1e6
            diss.to_file(STATE_DIR / f"{juris}__{aoi_id}__open.gpkg",
                         layer="open_ground", driver="GPKG")
            print(f"\n  open ground: {diss['km2'].iloc[0]:,.0f} km² dissolved")
        print(f"  → {out}")
    return cells


def sample(aoi_id: str, juris: str = "ON", n: int = 15):
    """Cells spanning every observed state, for hand-verification (C1.1 acceptance).

    Emits H3 cell ids with centroid coordinates so each can be checked against
    the MLAS Map Viewer by eye — that check is a human step and is not claimed
    here.
    """
    import geopandas as gpd
    import pandas as pd
    out = STATE_DIR / f"{juris}__{aoi_id}.parquet"
    if not out.exists():
        sys.exit("run without --sample first")
    cells = gpd.read_parquet(out)
    states = list(cells["state"].unique())
    per = max(1, n // max(1, len(states)))
    picks = []
    for st in states:
        picks.append(cells[cells["state"] == st].head(per))
    s = gpd.GeoDataFrame(pd.concat(picks, ignore_index=True), crs=cells.crs)
    cent = s.to_crs("EPSG:3978").geometry.centroid.to_crs(4326)
    print(f"  {len(s)} cells spanning {len(states)} state(s) — verify each by hand "
          f"in the MLAS Map Viewer:\n")
    print(f"  {'cell_id':<18}{'state':<12}{'lat':>9}{'lon':>11}  blocking_layer")
    for i, r in s.iterrows():
        c = cent.loc[i]
        # `None` survives a Parquet round-trip as NaN, which has no .split().
        bl = r["blocking_layer"]
        bl = "" if bl is None or pd.isna(bl) else str(bl).split("__")[-1]
        print(f"  {r['cell_id']:<18}{r['state']:<12}{c.y:>9.4f}{c.x:>11.4f}  {bl}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    ap.add_argument("--juris", default="ON")
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()
    C.require_lake()
    if args.sample:
        sample(args.aoi, args.juris, args.sample)
    else:
        compute(args.aoi, args.juris)


if __name__ == "__main__":
    main()
