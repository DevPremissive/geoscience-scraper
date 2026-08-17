#!/usr/bin/env python3
"""
fabric.py — the H3 hex fabric every model and every feature hangs off (C0.5).

Two resolutions, settled in PLAN_C0 and not to be reopened:

  r7  ≈5.2 km² — the regional modelling fabric. C2 trains on it, C1 computes
      heat on it. Canada-wide this is ~2 M land cells.
  r9  ≈0.10 km² — claim scale, comparable to one Ontario cell claim (~21 ha).
      Built lazily per AOI, never nationally: the national r9 set is ~150 M
      cells for no benefit.

Delegated decisions, made here and documented rather than left implicit:

  **Containment.** A cell belongs to a region when its *centroid* falls inside
  (`ContainsCentroid`). The alternative, any-intersection, double-counts every
  cell along a shared border, so a cell on the Ontario/Manitoba line would be
  in both fabrics and any per-province aggregate would over-count. Centroid
  containment gives each cell exactly one province.

  **Terrane.** `terrane_id` comes from the bedrock geological-province polygons
  by majority area within the cell. See `TERRANE_FIELD` for what that field
  actually contains in Ontario, which is coarser than the plan assumes.

Usage:
    python src/fabric.py --build-r7 ON
    python src/fabric.py --build-r9 ON --aoi -81.5,48.0,-80.0,49.0 --aoi-id abitibi
    python src/fabric.py --info
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, sys
from pathlib import Path

import config as C

FABRIC_DIR = C.PROCESSED_DIR / "fabric"
R9_DIR = FABRIC_DIR / "r9"

R7, R9 = 7, 9

#: Boundary polygon per jurisdiction, as (gpkg layer, optional attribute filter).
#: Ontario's is the authoritative MNDM provincial boundary shipped in the MLAS
#: administrative bundle.
BOUNDARY_LAYERS = {
    "ON": "ON__ON_MLAS_ADMIN__PROVINCE",
}

#: Water to subtract from the boundary. A provincial boundary is *total* area:
#: Ontario's is 1,076,395 km², of which 158,654 km² is water. Polyfilling that
#: puts ~27,000 r7 cells in the Great Lakes and Hudson Bay, where every feature
#: this system computes is undefined — no bedrock, no tenure, no drilling — and
#: they would otherwise sit in the modelling fabric as permanent null rows.
WATER_LAYERS = {
    "ON": ["ON__ON_GEOL_BEDROCK__MAJORLAKES"],
}

#: Bedrock polygons carrying a geological-province attribute, per jurisdiction.
#:
#: Ontario's MRD126 offers PROVINCE_P (98% populated, **3 distinct values**:
#: SUPERIOR, GRENVILLE, "SOUTHERN and SUPERIOR"), TECTZONE_P (only 14%
#: populated, 2 values, Grenville-only) and OROGEN_P (entirely empty).
#: PROVINCE_P is therefore the only usable one — but see the warning in
#: `build_r7`: three terranes with one covering ~75% is a weak basis for the
#: leave-one-terrane-out CV C2.5 wants.
#: Both Ontario bedrock layers are needed: `Geopoly` stops at the Precambrian
#: shield, and `GeopolyLOWLAND` carries the Hudson Bay Lowland. Using Geopoly
#: alone left 37% of the province with no terrane.
TERRANE_LAYERS = {
    "ON": (["ON__ON_GEOL_BEDROCK__Geopoly",
            "ON__ON_GEOL_BEDROCK__GeopolyLOWLAND"], "PROVINCE_P"),
}


def _read_layer(layer: str, columns=None):
    import geopandas as gpd
    return gpd.read_file(C.GPKG_PATH, layer=layer, columns=columns)


def _geom_hash(geom) -> str:
    return hashlib.sha256(geom.wkb).hexdigest()[:16]


def fabric_version(resolutions, landmass_hash: str, built: str | None = None) -> str:
    """Stable id for a fabric build, stamped into every downstream matrix.

    Deliberately includes the build date: two fabrics over the same geometry at
    the same resolutions are still different artifacts if the boundary source
    was re-harvested between them, and a feature matrix must be able to say
    which one it was computed against.
    """
    built = built or dt.date.today().isoformat()
    payload = json.dumps({"res": sorted(resolutions), "landmass": landmass_hash,
                          "built": built}, sort_keys=True)
    return "fab-" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def _cells_for(geom, res: int):
    """H3 cells whose centroid falls inside `geom`, as (cell_ids, geometries)."""
    import geopandas as gpd
    import shapely
    from h3ronpy import ContainmentMode
    from h3ronpy.vector import geometry_to_cells, cells_to_wkb_polygons
    import h3

    cells = geometry_to_cells(geom, res, containment_mode=ContainmentMode.ContainsCentroid)
    wkb = cells_to_wkb_polygons(cells)
    geoms = shapely.from_wkb(wkb.to_pylist() if hasattr(wkb, "to_pylist") else list(wkb))
    ids = [h3.int_to_str(int(c)) for c in (cells.to_pylist()
                                           if hasattr(cells, "to_pylist") else list(cells))]
    return ids, geoms


def build_r7(juris: str = "ON", write: bool = True):
    """Build the r7 fabric for a jurisdiction, with province and terrane joins."""
    import geopandas as gpd
    import pandas as pd

    layer = BOUNDARY_LAYERS.get(juris)
    if not layer:
        sys.exit(f"no boundary layer registered for {juris} — see BOUNDARY_LAYERS")
    bnd = _read_layer(layer).to_crs(4326)
    landmass = bnd.geometry.union_all()

    total_km2 = None
    for wl in WATER_LAYERS.get(juris, []):
        try:
            water = _read_layer(wl).to_crs(4326).geometry.union_all()
        except Exception as e:                                  # noqa: BLE001
            print(f"  ! water layer {wl} unreadable ({e}); not clipping")
            continue
        import geopandas as _g
        total_km2 = total_km2 or _g.GeoSeries([landmass], crs=4326).to_crs("EPSG:6933").area.iloc[0] / 1e6
        landmass = landmass.difference(water)
    lm_hash = _geom_hash(landmass)

    ids, geoms = _cells_for(landmass, R7)
    cells = gpd.GeoDataFrame({"cell_id": ids}, geometry=list(geoms), crs=4326)
    cells["province"] = juris

    # Equal-area for area_km2: degrees are not a unit of area, and cell area
    # varies by latitude enough to matter across a province 15° tall.
    cells["area_km2"] = cells.to_crs("EPSG:6933").area / 1e6

    cells["terrane_id"] = _terrane_join(cells, juris)

    ver = fabric_version([R7], lm_hash)
    cells.attrs["fabric_version"] = ver

    n_terr = cells["terrane_id"].nunique(dropna=True)
    land = cells["area_km2"].sum()
    print(f"  r7 {juris}: {len(cells):,} cells, {land:,.0f} km², "
          f"mean {cells['area_km2'].mean():.2f} km²/cell"
          + (f"  (boundary total {total_km2:,.0f} km²; "
             f"{total_km2 - land:,.0f} km² water clipped)" if total_km2 else ""))
    print(f"  terranes: {n_terr} distinct, "
          f"{cells['terrane_id'].notna().mean()*100:.1f}% assigned")
    if n_terr and n_terr < 5:
        share = cells["terrane_id"].value_counts(normalize=True).iloc[0]
        print(f"  ! only {n_terr} terranes and the largest covers {share*100:.0f}% "
              f"of cells — leave-one-terrane-out CV (C2.5) will be weak here")

    if write:
        FABRIC_DIR.mkdir(parents=True, exist_ok=True)
        out = FABRIC_DIR / f"r7_{juris}.parquet"
        cells.to_parquet(out, index=False)
        meta = {"fabric_version": ver, "resolution": R7, "jurisdiction": juris,
                "boundary_layer": layer, "landmass_hash": lm_hash,
                "containment": "ContainsCentroid",
                "water_clipped": WATER_LAYERS.get(juris, []),
                "cells": len(cells), "built": dt.date.today().isoformat(),
                "terrane_layers": TERRANE_LAYERS.get(juris, (None, None))[0],
                "terrane_field": TERRANE_LAYERS.get(juris, (None, None))[1],
                "terranes": int(n_terr)}
        (FABRIC_DIR / f"r7_{juris}.json").write_text(json.dumps(meta, indent=2),
                                                     encoding="utf-8")
        print(f"  → {out}  ({ver})")
    return cells


def _terrane_join(cells, juris: str):
    """Majority-area geological province per cell, or None where unmapped."""
    import geopandas as gpd
    import pandas as pd

    spec = TERRANE_LAYERS.get(juris)
    if not spec:
        return pd.Series([None] * len(cells), index=cells.index)
    layers, field = spec
    if isinstance(layers, str):
        layers = [layers]
    parts = [_read_layer(l, columns=[field]).to_crs(4326) for l in layers]
    terr = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    terr = gpd.GeoDataFrame(terr, geometry="geometry", crs=4326)
    terr = terr[terr[field].notna()]
    if terr.empty:
        return pd.Series([None] * len(cells), index=cells.index)
    terr = terr.dissolve(by=field, as_index=False)

    # Overlay, then take the largest intersecting piece per cell. `intersection`
    # on an equal-area CRS so "largest" means largest on the ground.
    join = gpd.overlay(cells[["cell_id", "geometry"]], terr[[field, "geometry"]],
                       how="intersection", keep_geom_type=False)
    if join.empty:
        return pd.Series([None] * len(cells), index=cells.index)
    join["a"] = join.to_crs("EPSG:6933").area
    best = join.sort_values("a").groupby("cell_id", as_index=False).last()
    return cells["cell_id"].map(dict(zip(best["cell_id"], best[field])))


def build_r9(aoi_geom, aoi_id: str, write: bool = True):
    """Build an r9 fabric for one AOI, each cell linked to its r7 ancestor."""
    import geopandas as gpd
    import h3

    ids, geoms = _cells_for(aoi_geom, R9)
    cells = gpd.GeoDataFrame({"cell_id": ids}, geometry=list(geoms), crs=4326)
    cells["parent"] = [h3.cell_to_parent(c, R7) for c in ids]
    cells["area_km2"] = cells.to_crs("EPSG:6933").area / 1e6
    print(f"  r9 {aoi_id}: {len(cells):,} cells under "
          f"{cells['parent'].nunique():,} r7 parents, "
          f"mean {cells['area_km2'].mean():.3f} km²/cell")
    if write:
        R9_DIR.mkdir(parents=True, exist_ok=True)
        out = R9_DIR / f"{aoi_id}.parquet"
        cells.to_parquet(out, index=False)
        print(f"  → {out}")
    return cells


def load_r7(juris: str = "ON"):
    import geopandas as gpd
    p = FABRIC_DIR / f"r7_{juris}.parquet"
    if not p.exists():
        sys.exit(f"no r7 fabric for {juris} — run --build-r7 {juris}")
    return gpd.read_parquet(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-r7", metavar="JURIS")
    ap.add_argument("--build-r9", metavar="JURIS")
    ap.add_argument("--aoi", help="minx,miny,maxx,maxy in EPSG:4326")
    ap.add_argument("--aoi-id", default="aoi")
    ap.add_argument("--info", action="store_true")
    args = ap.parse_args()
    C.require_lake()

    if args.build_r7:
        build_r7(args.build_r7)
    if args.build_r9:
        from shapely.geometry import box
        if not args.aoi:
            sys.exit("--build-r9 needs --aoi minx,miny,maxx,maxy")
        build_r9(box(*[float(v) for v in args.aoi.split(",")]), args.aoi_id)
    if args.info:
        for m in sorted(FABRIC_DIR.glob("*.json")):
            d = json.loads(m.read_text())
            print(f"  {m.stem:<14}{d['fabric_version']}  {d['cells']:>9,} cells  "
                  f"r{d['resolution']}  {d['terranes']} terranes  built {d['built']}")
        for p in sorted(R9_DIR.glob("*.parquet")):
            print(f"  r9/{p.stem}")


if __name__ == "__main__":
    main()
