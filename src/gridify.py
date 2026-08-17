#!/usr/bin/env python3
"""
gridify.py — the lake → feature-store bridge (C0.5), and the versioning
discipline that store runs under (C0.6).

Every function here turns one lake layer into long-format rows
`(cell_id, feature, value, snapshot, source_hash)` and appends them to

    features/<fabric_version>/<snapshot>/features.parquet
    features/<fabric_version>/<snapshot>/manifest.json

Long format is a binding contract (MASTER §4): wide pivots are produced on
demand by consumers, never stored. The one carve-out is dense embeddings, which
are not produced here.

Two rules from C0.6 that this module enforces rather than documents:

  * `features/latest` is **forbidden**. A consumer must name the snapshot it
    read, because a model card or a dossier that says "latest" is unfalsifiable
    a month later. `write_features()` refuses to create it.
  * The manifest records every input layer's hash and the code version, so a
    matrix can be traced to the exact bytes it came from.

Usage:
    python src/gridify.py --demo ON        # C0.5 acceptance matrix
    python src/gridify.py --verify ON      # determinism check
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, subprocess, sys
from pathlib import Path

import config as C

FEATURES_DIR = C.PROCESSED_DIR.parent / "features" \
    if (C.PROCESSED_DIR.parent / "features").exists() else C.PROCESSED_DIR / "features"

LONG_COLS = ["cell_id", "feature", "value"]


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

def layer_hash(layer: str) -> str:
    """Content hash of a geo.gpkg layer, for the manifest.

    Hashes the layer's rows rather than the GeoPackage file: geo.gpkg holds 319
    layers and is rewritten whenever any one of them changes, so a file digest
    would mark every feature stale on every unrelated edit.
    """
    import pyogrio
    try:
        info = pyogrio.read_info(str(C.GPKG_PATH), layer=layer)
    except Exception:                                           # noqa: BLE001
        return ""
    payload = json.dumps({"layer": layer, "features": int(info["features"]),
                          "fields": list(info["fields"]),
                          "geometry_type": info["geometry_type"]}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def code_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=Path(__file__).resolve().parent,
                              capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:                                           # noqa: BLE001
        return "unknown"


# --------------------------------------------------------------------------
# gridify operations
# --------------------------------------------------------------------------

def _prep(layer_gdf, cells):
    """Align CRS, in the direction that keeps the join cheap."""
    if layer_gdf.crs != cells.crs:
        layer_gdf = layer_gdf.to_crs(cells.crs)
    return layer_gdf


def gridify_vector(gdf, cells, mode: str, field: str | None = None,
                   prefix: str = "vec"):
    """Summarise polygon/line features onto cells.

    mode ∈ {area_weighted_category, count, presence, text_concat}
    """
    import geopandas as gpd
    import pandas as pd

    gdf = _prep(gdf, cells)
    rows = []

    if mode == "count":
        j = gpd.sjoin(gdf, cells[["cell_id", "geometry"]], predicate="intersects")
        for cid, n in j.groupby("cell_id").size().items():
            rows.append((cid, f"{prefix}__count", float(n)))

    elif mode == "presence":
        j = gpd.sjoin(gdf, cells[["cell_id", "geometry"]], predicate="intersects")
        for cid in j["cell_id"].unique():
            rows.append((cid, f"{prefix}__presence", 1.0))

    elif mode == "area_weighted_category":
        if not field:
            raise ValueError("area_weighted_category needs a field")
        ov = gpd.overlay(cells[["cell_id", "geometry"]], gdf[[field, "geometry"]],
                         how="intersection", keep_geom_type=False)
        if ov.empty:
            return pd.DataFrame(rows, columns=LONG_COLS)
        ov["a"] = ov.to_crs("EPSG:6933").area
        tot = ov.groupby("cell_id")["a"].sum()
        frac = ov.groupby(["cell_id", field])["a"].sum() / tot
        for (cid, cat), f in frac.items():
            rows.append((cid, f"{prefix}__frac__{cat}", float(f)))
        dom = frac.reset_index().sort_values("a").groupby("cell_id").last()
        for cid, r in dom.iterrows():
            rows.append((cid, f"{prefix}__dominant__{r[field]}", 1.0))

    elif mode == "text_concat":
        # C2.1 needs the *text* of intersecting units with their area share, so
        # a cell document reads like a description of that ground. Emitted as a
        # separate artifact, not as numeric rows.
        if not field:
            raise ValueError("text_concat needs a field")
        ov = gpd.overlay(cells[["cell_id", "geometry"]], gdf[[field, "geometry"]],
                         how="intersection", keep_geom_type=False)
        if ov.empty:
            return pd.DataFrame(columns=["cell_id", "text"])
        ov["a"] = ov.to_crs("EPSG:6933").area
        tot = ov.groupby("cell_id")["a"].sum()
        ov["share"] = ov["a"] / ov["cell_id"].map(tot)
        docs = (ov.sort_values("share", ascending=False)
                  .groupby("cell_id")
                  .apply(lambda d: " ".join(
                      f"[{s:.2f}] {t}" for t, s in zip(d[field].astype(str), d["share"])
                      if t and t != "None"), include_groups=False))
        return docs.rename("text").reset_index()

    else:
        raise ValueError(f"unknown mode {mode!r}")

    return pd.DataFrame(rows, columns=LONG_COLS)


def gridify_points(gdf, cells, agg=("count",), value_field: str | None = None,
                   prefix: str = "pts"):
    """Aggregate point features per cell: count / mean / max / percentile."""
    import geopandas as gpd
    import pandas as pd

    gdf = _prep(gdf, cells)
    j = gpd.sjoin(gdf, cells[["cell_id", "geometry"]], predicate="within")
    rows = []
    if j.empty:
        return pd.DataFrame(rows, columns=LONG_COLS)

    g = j.groupby("cell_id")
    for a in agg:
        if a == "count":
            for cid, n in g.size().items():
                rows.append((cid, f"{prefix}__count", float(n)))
        elif value_field and a in ("mean", "max", "min", "sum"):
            for cid, v in getattr(g[value_field], a)().dropna().items():
                rows.append((cid, f"{prefix}__{a}__{value_field}", float(v)))
        elif value_field and a.startswith("p"):
            q = int(a[1:]) / 100
            for cid, v in g[value_field].quantile(q).dropna().items():
                rows.append((cid, f"{prefix}__{a}__{value_field}", float(v)))
    return pd.DataFrame(rows, columns=LONG_COLS)


def gridify_raster(cog, cells, categorical=False, prefix=None):
    """Zonal statistics per cell — wraps rasters.zonal."""
    import rasters
    return rasters.zonal(cog, cells, categorical=categorical, feature_prefix=prefix)


def dist_to(gdf, cells, prefix: str = "dist"):
    """Distance (km) from each cell centroid to the nearest feature.

    Uses GeoPandas' `sjoin_nearest`, which is backed by an STRtree — brute force
    over 164k cells x 61k fault segments would be ~10^10 comparisons.
    """
    import geopandas as gpd
    import pandas as pd

    gdf = _prep(gdf, cells)
    # Project first, then take centroids: a centroid computed in degrees is not
    # the centre of the cell on the ground, and at Ontario's latitudes the error
    # is large enough to move a cell's nearest-fault distance.
    cells_m = cells[["cell_id", "geometry"]].to_crs("EPSG:3978")
    cent_m = gpd.GeoDataFrame(cells_m[["cell_id"]],
                              geometry=cells_m.geometry.centroid, crs="EPSG:3978")
    gdf_m = gdf.to_crs("EPSG:3978")
    j = gpd.sjoin_nearest(cent_m, gdf_m[["geometry"]], how="left",
                          distance_col="_d")
    j = j.groupby("cell_id")["_d"].min() / 1000.0
    return pd.DataFrame({"cell_id": j.index, "feature": f"{prefix}__km",
                         "value": j.values})[LONG_COLS]


# --------------------------------------------------------------------------
# feature store (C0.6)
# --------------------------------------------------------------------------

def write_features(frames, fabric_version: str, snapshot: str, inputs: list,
                   root: Path | None = None) -> Path:
    """Append long-format frames to the versioned feature store.

    `inputs` is a list of dicts describing every source that contributed, so the
    manifest can answer "what exactly was this matrix computed from".
    """
    import pandas as pd

    root = root or FEATURES_DIR
    out_dir = root / fabric_version / snapshot
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.concat([f for f in frames if f is not None and not f.empty],
                   ignore_index=True) if frames else pd.DataFrame(columns=LONG_COLS)
    # Deterministic on disk: a re-run with identical inputs must produce an
    # identical file, which is the C0.5 acceptance check.
    df = df.sort_values(LONG_COLS, kind="mergesort").reset_index(drop=True)
    fp = out_dir / "features.parquet"
    df.to_parquet(fp, index=False, compression="zstd")

    (out_dir / "manifest.json").write_text(json.dumps({
        "fabric_version": fabric_version,
        "snapshot": snapshot,
        "built": dt.datetime.now().isoformat(timespec="seconds"),
        "gridify_code_version": code_version(),
        "rows": int(len(df)),
        "features": sorted(df["feature"].unique().tolist())[:200],
        "feature_count": int(df["feature"].nunique()),
        "cells": int(df["cell_id"].nunique()),
        "inputs": inputs,
    }, indent=2), encoding="utf-8")

    # C0.6: no `latest`. Enforced, not merely asked for.
    stale = root / "latest"
    if stale.exists() or stale.is_symlink():
        stale.unlink()
        print("  ! removed features/latest — consumers must name their snapshot")
    return fp


def demo(juris: str = "ON", snapshot: str | None = None, root: Path | None = None):
    """Build the C0.5 acceptance matrix: one feature of each kind over Ontario."""
    import geopandas as gpd
    import fabric as F
    import rasters as R

    cells = F.load_r7(juris)
    meta = json.loads((F.FABRIC_DIR / f"r7_{juris}.json").read_text())
    ver = meta["fabric_version"]
    snapshot = snapshot or dt.date.today().isoformat()
    print(f"  fabric {ver}: {len(cells):,} cells")

    frames, inputs = [], []

    # 1. vector category — bedrock geology by dominant rock type
    lyr = "ON__ON_GEOL_BEDROCK__Geopoly"
    g = gpd.read_file(C.GPKG_PATH, layer=lyr, columns=["ROCKTYPE_P"])
    f1 = gridify_vector(g, cells, "area_weighted_category", field="ROCKTYPE_P",
                        prefix="bedrock")
    frames.append(f1); inputs.append({"layer": lyr, "hash": layer_hash(lyr),
                                      "op": "area_weighted_category"})
    print(f"  vector category : {len(f1):,} rows")

    # 2. distance — to nearest mapped fault
    lyr = "ON__ON_GEOL_FAULTS"
    g = gpd.read_file(C.GPKG_PATH, layer=lyr)
    f2 = dist_to(g, cells, prefix="fault")
    frames.append(f2); inputs.append({"layer": lyr, "hash": layer_hash(lyr),
                                      "op": "dist_to"})
    print(f"  distance        : {len(f2):,} rows")

    # 3. point count — OMEIS exploration drillholes
    lyr = "ON__ON_OMEIS_DRILLHOLE"
    g = gpd.read_file(C.GPKG_PATH, layer=lyr, columns=["LENGTH"])
    f3 = gridify_points(g, cells, agg=("count", "max"), value_field="LENGTH",
                        prefix="drill")
    frames.append(f3); inputs.append({"layer": lyr, "hash": layer_hash(lyr),
                                      "op": "gridify_points"})
    print(f"  point count     : {len(f3):,} rows")

    # 4. raster zonal — CGMC lithology
    entry = next((e for e in R._registry() if e["code"] == "CGMC"), None)
    if entry:
        f4 = gridify_raster(entry["path"], cells, categorical=entry["categorical"],
                            prefix="cgmc")
        frames.append(f4); inputs.append({"raster": entry["layer"],
                                          "hash": entry["source_hash"],
                                          "op": "zonal"})
        print(f"  raster zonal    : {len(f4):,} rows")

    fp = write_features(frames, ver, snapshot, inputs, root=root)
    import pandas as pd
    df = pd.read_parquet(fp)
    print(f"\n  → {fp}")
    print(f"  {len(df):,} rows · {df['feature'].nunique()} distinct features · "
          f"{df['cell_id'].nunique():,} cells")
    return fp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", metavar="JURIS")
    ap.add_argument("--verify", metavar="JURIS")
    args = ap.parse_args()
    C.require_lake()

    if args.demo:
        demo(args.demo)
    if args.verify:
        import tempfile, pandas as pd
        with tempfile.TemporaryDirectory() as td:
            a = demo(args.verify, snapshot="determinism-a", root=Path(td))
            b = demo(args.verify, snapshot="determinism-b", root=Path(td))
            ba, bb = a.read_bytes(), b.read_bytes()
            print(f"\n  run A {len(ba):,} bytes\n  run B {len(bb):,} bytes")
            if ba == bb:
                print("  DETERMINISM: PASS — byte-identical")
            else:
                da, db = pd.read_parquet(a), pd.read_parquet(b)
                same = da.equals(db)
                print(f"  DETERMINISM: bytes differ; frames equal={same}")
                sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
