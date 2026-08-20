#!/usr/bin/env python3
"""
mapapi.py — C4.2 map service: the navigation layer over the lake.

The dossier (C4.1) is the work product; this is how a human finds the ground a
dossier should be written about, and how they check that the ground is where the
system says it is. PLAN_C4 4.2 says extend `serve.py` rather than stand up a
second service, so this module is an `APIRouter` that `serve.py` mounts, plus the
pure layer resolvers underneath it.

Four decisions were delegated to the build. Each is recorded here rather than
left implicit in the code.

**No external basemap, and no CDN.** The inline UI that shipped with `serve.py`
loaded MapLibre from unpkg and tiles from `basemaps.cartocdn.com`. Every tile
request tells a third party which bounding box is on screen, and the whole point
of this system is knowing which ground is worth looking at before anyone else
does — that is the one thing that must not leave the machine. The viewer vendors
its own MapLibre and draws the province, lakes and geology from `geo.gpkg` as its
ground. Offline by construction, not by configuration.

**Resolution step-down instead of vector tiles.** Audit D5 corrected the plan's
assumption that a tile endpoint already existed: it does not, and MVT is real
work. It is also avoidable here. Ontario's r7 fabric is 164,577 cells, far too
many to ship as GeoJSON at province zoom, but an H3 cell id *is* a spatial index:
`cell_to_parent` aggregates a viewport's cells to r6, r5 or r4 for free. The
resolver picks the finest resolution whose feature count fits the budget and
stamps the choice on the response, so an aggregate can never be mistaken for the
underlying cells. MVT becomes a performance optimisation for later rather than a
prerequisite for shipping.

**Geometry comes from H3, never from disk.** The fabric parquet carries WKB
polygons, but the cell id already determines the polygon exactly. Deriving
boundaries with `cell_to_boundary` is ~2 µs a cell — faster than parsing stored
WKB — and, more importantly, it cannot drift from the fabric the way a second
copy of the geometry can.

**Overview aggregation is `max`, not `mean`.** Heat, criticality and
prospectivity are all intensity measures over a sparse population: 36,299 of
164,577 r7 cells have any heat at all. Averaging a hot cell against its eleven
empty neighbours hides exactly the thing the overview exists to surface. `max`
answers the navigational question ("is there anything here worth zooming into"),
`n_source_cells` and `agg` are returned so the reader knows they are looking at
an aggregate, and `agg=mean` is available for anyone who wants the regional
average instead.

Endpoints (all mounted under the app in `serve.py`):

  GET  /api/catalog                       — what this viewer can currently show
  GET  /api/fabric?metric=&bbox=          — r7 choropleth, auto resolution
  GET  /api/land?aoi=&bbox=               — r9 land_state, r7 open-fraction rollup
  GET  /api/criticality?bbox=&block=      — r9 criticality scores
  GET  /api/claims?bbox=                  — tenure polygons
  GET  /api/blocks?bbox=                  — ownership blocks (C1.4)
  GET  /api/events?bbox=&start=&end=      — tenure-event pulses, time filtered
  GET  /api/cell/{cell_id}                — evidence payload for the popover
  GET  /api/dossiers                      — dossiers already on disk
  POST /api/dossier                       — generate one for a cell
  GET  /render?layers=&bbox=              — static PNG, same resolvers

Usage:
    python src/serve.py --port 9877          # serves this router and the viewer
    python src/mapapi.py --smoke             # resolver smoke test, no server
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import config as C

try:
    import h3
    import pandas as pd
except ImportError:  # pragma: no cover - environment guard
    sys.exit("pip install h3 pandas")

R7, R9 = 7, 9

#: Lowest resolution the step-down will fall back to. r4 cells are ~1,770 km²;
#: below that the aggregate stops being a map of Ontario and starts being a map
#: of Canada, which no metric here is computed over.
MIN_RES = 4

#: Feature budget per response. Chosen from what MapLibre draws as GeoJSON
#: without dropping frames on this machine, not from a standard.
MAX_FEATURES = 6000

#: Ontario, comfortably. Used when a request omits bbox.
DEFAULT_BBOX = (-95.5, 41.5, -74.0, 57.0)


# ---------------------------------------------------------------------------
# Caching. Every artifact is a file on the bulk volume that a nightly timer may
# rewrite underneath a long-running server, so the cache key is (path, mtime):
# a harvest that replaces heat.parquet invalidates it without a restart.
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, object]] = {}


def _cached(path: Path, loader, key_extra: str = ""):
    """Load `path` through `loader`, re-reading only when its mtime changes."""
    key = f"{path}::{key_extra}" if key_extra else str(path)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        _CACHE.pop(key, None)
        return None
    hit = _CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    obj = loader(path)
    _CACHE[key] = (mtime, obj)
    return obj


def _read_parquet(path: Path, columns=None):
    """Read a parquet, cached.

    The column projection is part of the cache key. It was not, once, and the
    bug it produced is worth remembering: `/api/catalog` reads three columns of
    `tenure_events.parquet` for the time scrubber, `events_layer` reads six.
    Whichever ran first won the cache entry, so in a live server — where the
    catalog always loads first — the events layer got a frame with no `cell_r7`
    and 500'd, while every direct call to the same function in a fresh process
    passed. A cache keyed on less than what distinguishes the values it stores
    fails only under the interleaving it will actually meet in production."""
    key = "cols=" + (",".join(sorted(columns)) if columns else "*")
    return _cached(path, lambda p: pd.read_parquet(p, columns=columns), key)


# ---------------------------------------------------------------------------
# Paths. Resolved through config so an unmounted lake fails loudly rather than
# looking like an empty one (config.require_lake()).
# ---------------------------------------------------------------------------

FABRIC_DIR = C.PROCESSED_DIR / "fabric"
R9_DIR = FABRIC_DIR / "r9"
LAND_DIR = C.PROCESSED_DIR / "land_state"
MODEL_DIR = C.PROCESSED_DIR / "models"
MARKET_DIR = C.MARKET_DIR
HEAT_PATH = C.PROCESSED_DIR / "heat.parquet"
CRIT_PATH = C.PROCESSED_DIR / "criticality.parquet"
EVENTS_PATH = C.PROCESSED_DIR / "tenure_events.parquet"
LAPSE_PATH = C.PROCESSED_DIR / "lapse_watch.parquet"
OWNERSHIP_DB = C.PROCESSED_DIR / "ownership.duckdb"
DOSSIER_DIR = C.PROCESSED_DIR / "dossiers"
FEATURES_DIR = C.PROCESSED_DIR / "features"

#: Tenure polygons per jurisdiction, as catalog table names. Ontario's
#: operational cell claims are the authoritative current register (audit F).
CLAIM_TABLES = {
    "ON": "geo_ON__ON_MLAS_TENURE__Operational_Cell_Claims",
}

#: Context layers drawn as the map's ground, in draw order. These replace the
#: third-party basemap; all are already in geo.gpkg.
CONTEXT_TABLES = [
    ("province", "geo_ON__ON_MLAS_ADMIN__PROVINCE"),
    ("lakes", "geo_ON__ON_GEOL_BEDROCK__MAJORLAKES"),
]

#: `ownership.duckdb:blocks.geometry_wkt` is written in **EPSG:3978** (Canada
#: Atlas Lambert), not lon/lat — `ownership_graph.py` dissolves in a metric CRS
#: and stores the metric geometry. Nothing downstream noticed because C1.4's own
#: consumers join on `block_id` and `frontier.open_cell_id` rather than
#: intersecting geometry. A lon/lat envelope against this column silently
#: matches nothing, which is a wrong answer that looks like an empty
#: neighbourhood, so every spatial query here transforms explicitly.
BLOCKS_CRS = "EPSG:3978"


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def json_safe(obj):
    """Make a payload strictly-JSON serialisable.

    Pandas hands back `NaN`, `NaT` and numpy scalars. `json.dumps` writes bare
    `NaN`, which is not JSON: `JSON.parse` in the browser throws on it and takes
    the whole popover down over one missing attribute. Every value crossing the
    HTTP boundary goes through here."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item") and not isinstance(obj, type):  # numpy scalar
        try:
            return json_safe(obj.item())
        except Exception:
            pass
    if obj is getattr(pd, "NaT", object()):
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _maybe_json(v):
    """Upstream stores some structured fields as JSON strings (C6.2 writes
    `missing_because` that way, since parquet has no dict type). Hand the
    structure back to the client rather than a string it has to re-parse."""
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


@dataclass
class Feature:
    """One drawable thing. `geometry` is always a GeoJSON geometry dict so the
    GeoJSON endpoints and the PNG renderer consume exactly the same objects."""
    geometry: dict
    props: dict = field(default_factory=dict)
    value: float | None = None


@dataclass
class Layer:
    name: str
    features: list[Feature]
    meta: dict = field(default_factory=dict)

    def geojson(self) -> dict:
        return json_safe({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": f.geometry,
                 "properties": {**f.props, "value": f.value}}
                for f in self.features
            ],
            "meta": self.meta,
        })


def parse_bbox(s: str | None) -> tuple[float, float, float, float]:
    """`minlon,minlat,maxlon,maxlat`. Rejects inverted or degenerate boxes —
    a silently-empty response from a transposed bbox is a nasty debugging hour."""
    if not s:
        return DEFAULT_BBOX
    try:
        parts = [float(x) for x in s.split(",")]
    except ValueError:
        raise ValueError(f"bbox must be four numbers, got {s!r}")
    if len(parts) != 4:
        raise ValueError(f"bbox needs 4 values (minlon,minlat,maxlon,maxlat), got {len(parts)}")
    minx, miny, maxx, maxy = parts
    if minx >= maxx or miny >= maxy:
        raise ValueError(f"bbox is inverted or degenerate: {parts}")
    if not (-180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90):
        raise ValueError(f"bbox is outside lon/lat range: {parts}")
    return (minx, miny, maxx, maxy)


def cell_polygon(cell_id: str) -> dict:
    """GeoJSON polygon for an H3 cell. h3 returns (lat, lng); GeoJSON is
    (lng, lat) — transposing this is the classic H3 bug and it renders as an
    empty map over the Indian Ocean rather than as an error."""
    ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell_id)]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _centroids(cell_ids) -> tuple[list[float], list[float]]:
    lats, lngs = [], []
    for c in cell_ids:
        lat, lng = h3.cell_to_latlng(c)
        lats.append(lat)
        lngs.append(lng)
    return lats, lngs


def _bbox_mask(df: pd.DataFrame, bbox, lat_col="_lat", lng_col="_lng"):
    minx, miny, maxx, maxy = bbox
    return ((df[lng_col] >= minx) & (df[lng_col] <= maxx)
            & (df[lat_col] >= miny) & (df[lat_col] <= maxy))


def _with_centroids(df: pd.DataFrame, cell_col: str) -> pd.DataFrame:
    """Attach centroid lat/lng, cached on the frame so repeated requests against
    the same cached artifact do not recompute 164k H3 lookups."""
    if "_lat" in df.columns:
        return df
    lats, lngs = _centroids(df[cell_col].values)
    df = df.copy()
    df["_lat"] = lats
    df["_lng"] = lngs
    return df


# ---------------------------------------------------------------------------
# Fabric metrics. Each resolver returns a frame of (cell_id, value) at r7 plus
# a meta dict describing exactly which artifact and which slice produced it.
# ---------------------------------------------------------------------------

def _fabric_cells(juris: str = "ON") -> pd.DataFrame:
    path = FABRIC_DIR / f"r7_{juris}.parquet"
    df = _read_parquet(path, columns=["cell_id", "province", "area_km2", "terrane_id"])
    if df is None:
        raise FileNotFoundError(f"no r7 fabric for {juris}: run fabric.py --build-r7 {juris}")
    return _cached_centroids(path, df, "cell_id")


def _cached_centroids(path: Path, df: pd.DataFrame, cell_col: str) -> pd.DataFrame:
    key = f"{path}::centroids"
    mtime = path.stat().st_mtime
    hit = _CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    out = _with_centroids(df, cell_col)
    _CACHE[key] = (mtime, out)
    return out


def heat_values(quarter: str | None = None, column: str = "heat_cross_smoothed",
                juris: str = "ON") -> tuple[pd.DataFrame, dict]:
    """C1.3 staking heat. Defaults to the latest quarter present.

    `quarter=peak` takes each cell's maximum across all quarters, which is the
    right slice for "where has anything ever happened" and the wrong one for
    "where is it happening now" — the response says which was used."""
    df = _read_parquet(HEAT_PATH)
    if df is None:
        raise FileNotFoundError("heat.parquet missing: run land/heat.py --build")
    df = df[df["juris"] == juris]
    if column not in df.columns:
        raise ValueError(f"unknown heat column {column!r}; have "
                         f"{[c for c in df.columns if c.startswith('heat') or c.startswith('z_')]}")
    quarters = sorted(df["quarter"].unique())
    if quarter == "peak":
        out = (df.groupby("cell_r7", as_index=False)[column].max()
                 .rename(columns={"cell_r7": "cell_id", column: "value"}))
        used = "peak"
    else:
        used = quarter or (quarters[-1] if quarters else None)
        sl = df[df["quarter"] == used]
        out = sl[["cell_r7", column]].rename(columns={"cell_r7": "cell_id", column: "value"})
    meta = {"source": "heat.parquet", "column": column, "quarter": used,
            "quarters_available": quarters,
            "survivorship_biased": bool(df["survivorship_biased"].any()),
            "note": "C1.3 heat. Pre-archive quarters are descriptive and "
                    "survivorship-biased (Master §8); the flag is per-row upstream."}
    return out.dropna(subset=["value"]), meta


def prospectivity_values(system: str = "orogenic_au", version: str | None = None,
                         juris: str = "ON") -> tuple[pd.DataFrame, dict]:
    """C2.1 calibrated scores. Reads the out-of-fold scores, not in-sample fits:
    an in-sample surface would paint the training positives bright and mislead
    exactly the human this map exists to inform."""
    base = MODEL_DIR / system
    if not base.exists():
        raise FileNotFoundError(f"no model for system {system!r} under {MODEL_DIR}")
    versions = sorted(p.name for p in base.iterdir() if p.is_dir())
    if not versions:
        raise FileNotFoundError(f"no trained versions under {base}")
    used = version or versions[-1]
    scores = _read_parquet(base / used / "oof_scores.parquet")
    if scores is None:
        raise FileNotFoundError(f"no oof_scores.parquet in {base / used}")
    card_path = base / used / "card.json"
    card = json.loads(card_path.read_text()) if card_path.exists() else {}
    out = scores[["cell_id", "score"]].rename(columns={"score": "value"})
    folds = (card.get("validation", {}).get("spatially_blocked", {}) or {}).get("folds", [])
    aucs = [f.get("roc_auc") for f in folds if f.get("roc_auc") is not None]
    meta = {"source": f"models/{system}/{used}/oof_scores.parquet",
            "system": system, "version": used, "versions_available": versions,
            "fabric_version": card.get("fabric_version"),
            "blocked_roc_auc_mean": round(sum(aucs) / len(aucs), 4) if aucs else None,
            "scores": "out-of-fold, spatially blocked",
            "note": "Prospectivity is one term in the deal score, not the ranking "
                    "(Master §1). Out-of-fold scores only."}
    return out.dropna(subset=["value"]), meta


def ever_staked_values(juris: str = "ON") -> tuple[pd.DataFrame, dict]:
    df = _read_parquet(HEAT_PATH)
    if df is None:
        raise FileNotFoundError("heat.parquet missing")
    df = df[df["juris"] == juris]
    out = (df.groupby("cell_r7", as_index=False)["ever_staked_count"].max()
             .rename(columns={"cell_r7": "cell_id", "ever_staked_count": "value"}))
    meta = {"source": "heat.parquet", "column": "ever_staked_count",
            "note": "Prior interest is a weak positive prior, never a label "
                    "(Master §2)."}
    return out.dropna(subset=["value"]), meta


def criticality_r7_values(block: str | None = None) -> tuple[pd.DataFrame, dict]:
    """C1.5 criticality rolled up to r7 for the overview. The underlying scores
    are r9 — see `criticality_layer` for the real thing. A rollup is offered
    because the r9 scores cover 10,297 cells inside a handful of corridors and
    are invisible at province zoom otherwise."""
    df = _read_parquet(CRIT_PATH)
    if df is None:
        raise FileNotFoundError("criticality.parquet missing: run land/criticality.py --build")
    if block:
        df = df[df["block_id"] == block]
    if df.empty:
        return pd.DataFrame(columns=["cell_id", "value"]), {"source": "criticality.parquet",
                                                            "block": block, "rows": 0}
    parents = [h3.cell_to_parent(c, R7) for c in df["cell_id"].values]
    tmp = pd.DataFrame({"cell_id": parents, "value": df["score"].values})
    out = tmp.groupby("cell_id", as_index=False)["value"].max()
    meta = {"source": "criticality.parquet", "block": block,
            "rollup": "max of r9 child scores",
            "blocks": int(df["block_id"].nunique()),
            "note": "Criticality is scored on OPEN cells. A claim's own cells "
                    "carry no score — check neighbours (audit J)."}
    return out, meta


#: metric name -> (resolver, human label). Adding a metric is one line here.
FABRIC_METRICS = {
    "heat": (heat_values, "Staking heat (C1.3)"),
    "prospectivity": (prospectivity_values, "Prospectivity score (C2.1)"),
    "ever_staked": (ever_staked_values, "Times ever staked (C1.3)"),
    "criticality": (criticality_r7_values, "Criticality, r7 rollup (C1.5)"),
}


def fabric_layer(metric: str = "heat", bbox=None, res: int | None = None,
                 agg: str = "max", max_features: int = MAX_FEATURES,
                 juris: str = "ON", **params) -> Layer:
    """r7 choropleth with automatic resolution step-down."""
    if metric not in FABRIC_METRICS:
        raise ValueError(f"unknown metric {metric!r}; have {sorted(FABRIC_METRICS)}")
    if agg not in ("max", "mean", "sum"):
        raise ValueError(f"agg must be max|mean|sum, got {agg!r}")
    bbox = bbox or DEFAULT_BBOX
    resolver, label = FABRIC_METRICS[metric]
    values, meta = resolver(**params) if metric != "heat" else resolver(**params)

    # Restrict to the fabric: a metric row for a cell outside the jurisdiction's
    # fabric is a bug upstream, and drawing it would hide that bug.
    fabric = _fabric_cells(juris)
    df = values.merge(fabric[["cell_id", "_lat", "_lng"]], on="cell_id", how="inner")
    dropped = len(values) - len(df)

    df = df[_bbox_mask(df, bbox)]
    in_view = len(df)

    used_res = R7
    if res is not None:
        used_res = max(MIN_RES, min(R7, int(res)))
    n_source = in_view
    while len(df) > max_features and used_res > MIN_RES:
        used_res -= 1
        parents = [h3.cell_to_parent(c, used_res) for c in df["cell_id"].values]
        df = pd.DataFrame({"cell_id": parents, "value": df["value"].values,
                           "_n": 1})
        df = df.groupby("cell_id", as_index=False).agg(value=("value", agg),
                                                       _n=("_n", "sum"))
    if used_res != R7 and res is not None:
        # Explicit res request: honour it even if the count already fitted.
        parents = [h3.cell_to_parent(c, used_res) for c in df["cell_id"].values]
        df = pd.DataFrame({"cell_id": parents, "value": df["value"].values, "_n": 1})
        df = df.groupby("cell_id", as_index=False).agg(value=("value", agg), _n=("_n", "sum"))

    truncated = len(df) > max_features
    if truncated:
        df = df.nlargest(max_features, "value")

    feats = []
    for row in df.itertuples(index=False):
        props = {"cell_id": row.cell_id, "resolution": used_res}
        n = getattr(row, "_n", None)
        if n is not None:
            props["n_source_cells"] = int(n)
        feats.append(Feature(cell_polygon(row.cell_id), props, float(row.value)))

    vals = df["value"].astype(float)
    meta = {**meta, "metric": metric, "label": label, "resolution": used_res,
            "aggregated": used_res != R7, "agg": agg if used_res != R7 else None,
            "n_source_cells": int(n_source), "features": len(feats),
            "truncated": truncated, "bbox": list(bbox),
            "value_min": float(vals.min()) if len(vals) else None,
            "value_max": float(vals.max()) if len(vals) else None,
            "off_fabric_rows_dropped": int(dropped)}
    return Layer(f"fabric:{metric}", feats, meta)


# ---------------------------------------------------------------------------
# Land state (C1.1) at r9, with an r7 open-fraction rollup for the overview.
# ---------------------------------------------------------------------------

LAND_COLOURS = {
    "open": "#2e7d32", "claimed": "#c62828", "withdrawn": "#6d4c41",
    "park": "#1565c0", "alienated": "#8e24aa", "unknown": "#9e9e9e",
}


def available_aois() -> list[dict]:
    out = []
    if not LAND_DIR.exists():
        return out
    for p in sorted(LAND_DIR.glob("*.json")):
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        out.append({"aoi_id": m.get("aoi_id"), "jurisdiction": m.get("jurisdiction"),
                    "snapshot": m.get("snapshot"), "cells": m.get("cells"),
                    "state_counts": m.get("state_counts", {}),
                    "unknown_pct": m.get("unknown_pct")})
    return out


def _land_frame(aoi: str, juris: str = "ON") -> tuple[pd.DataFrame, dict]:
    path = LAND_DIR / f"{juris}__{aoi}.parquet"
    df = _read_parquet(path, columns=["cell_id", "parent", "state", "blocking_layer",
                                      "juris", "as_of_snapshot"])
    if df is None:
        raise FileNotFoundError(
            f"no land state for {juris}/{aoi}: run land/open_ground.py --aoi {aoi}")
    meta_path = LAND_DIR / f"{juris}__{aoi}.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return _cached_centroids(path, df, "cell_id"), meta


def land_layer(aoi: str = "abitibi", bbox=None, juris: str = "ON",
               states: str | None = None, max_features: int = MAX_FEATURES) -> Layer:
    """r9 land state in the viewport, or an r7 open-fraction rollup when the
    viewport holds more r9 cells than the budget.

    The rollup is not a downsample — it answers a different question ("what
    fraction of this hex is open") and is labelled as such, because a
    downsampled r9 state map would show one arbitrary child's state as if it
    were the hex's."""
    bbox = bbox or DEFAULT_BBOX
    df, jmeta = _land_frame(aoi, juris)
    if states:
        wanted = {s.strip() for s in states.split(",") if s.strip()}
        df = df[df["state"].isin(wanted)]
    df = df[_bbox_mask(df, bbox)]
    n_source = len(df)

    if n_source > max_features:
        tmp = pd.DataFrame({"cell_id": [h3.cell_to_parent(c, R7) for c in df["cell_id"].values],
                            "open": (df["state"] == "open").astype(int).values,
                            "n": 1})
        roll = tmp.groupby("cell_id", as_index=False).agg(open_cells=("open", "sum"),
                                                          n=("n", "sum"))
        roll["value"] = roll["open_cells"] / roll["n"]
        truncated = len(roll) > max_features
        if truncated:
            roll = roll.nlargest(max_features, "value")
        feats = [Feature(cell_polygon(r.cell_id),
                         {"cell_id": r.cell_id, "resolution": R7,
                          "open_cells": int(r.open_cells), "n_source_cells": int(r.n)},
                         float(r.value))
                 for r in roll.itertuples(index=False)]
        meta = {"mode": "r7_open_fraction", "resolution": R7, "aggregated": True,
                "aoi": aoi, "jurisdiction": juris, "snapshot": jmeta.get("snapshot"),
                "n_source_cells": int(n_source), "features": len(feats),
                "truncated": truncated, "bbox": list(bbox),
                "note": "Too many r9 cells in view; showing the fraction of each "
                        "r7 hex that is open. Zoom in for per-cell state.",
                "unknown_pct": jmeta.get("unknown_pct")}
        return Layer("land", feats, meta)

    feats = []
    for r in df.itertuples(index=False):
        feats.append(Feature(cell_polygon(r.cell_id), {
            "cell_id": r.cell_id, "resolution": R9, "state": r.state,
            "blocking_layer": r.blocking_layer, "colour": LAND_COLOURS.get(r.state, "#9e9e9e"),
            "as_of": str(r.as_of_snapshot)}, None))
    meta = {"mode": "r9_state", "resolution": R9, "aggregated": False, "aoi": aoi,
            "jurisdiction": juris, "snapshot": jmeta.get("snapshot"),
            "n_source_cells": int(n_source), "features": len(feats),
            "truncated": False, "bbox": list(bbox),
            "state_counts": df["state"].value_counts().to_dict(),
            "colours": LAND_COLOURS, "unknown_pct": jmeta.get("unknown_pct")}
    return Layer("land", feats, meta)


def criticality_layer(bbox=None, block: str | None = None, min_score: float = 0.0,
                      max_features: int = MAX_FEATURES) -> Layer:
    """C1.5 criticality at its native r9, optionally for one watched block."""
    bbox = bbox or DEFAULT_BBOX
    df = _read_parquet(CRIT_PATH)
    if df is None:
        raise FileNotFoundError("criticality.parquet missing")
    df = _cached_centroids(CRIT_PATH, df, "cell_id")
    if block:
        df = df[df["block_id"] == block]
    if min_score:
        df = df[df["score"] >= min_score]
    df = df[_bbox_mask(df, bbox)]
    n_source = len(df)
    truncated = n_source > max_features
    if truncated:
        df = df.nlargest(max_features, "score")
    feats = []
    for r in df.itertuples(index=False):
        reasons = r.reason_codes
        if isinstance(reasons, str):
            try:
                reasons = json.loads(reasons)
            except Exception:
                reasons = [reasons]
        feats.append(Feature(cell_polygon(r.cell_id), {
            "cell_id": r.cell_id, "block_id": r.block_id, "owner_id": r.owner_id,
            "reason_codes": reasons, "trend_source": r.trend_source,
            "trend_azimuth": None if pd.isna(r.trend_azimuth) else float(r.trend_azimuth),
            "distance_m": None if pd.isna(r.distance_m) else float(r.distance_m),
        }, float(r.score)))
    meta = {"source": "criticality.parquet", "resolution": R9, "block": block,
            "min_score": min_score, "n_source_cells": int(n_source),
            "features": len(feats), "truncated": truncated, "bbox": list(bbox),
            "note": "Scored on OPEN cells only — a claim's own footprint has no "
                    "score by construction."}
    return Layer("criticality", feats, meta)


# ---------------------------------------------------------------------------
# Vector layers out of the catalog / ownership store
# ---------------------------------------------------------------------------

def _catalog():
    try:
        import duckdb
    except ImportError:  # pragma: no cover
        raise RuntimeError("duckdb is required for tenure layers")
    con = duckdb.connect(str(C.CATALOG_DB), read_only=True)
    try:
        con.execute("LOAD spatial")
    except Exception:
        con.execute("INSTALL spatial; LOAD spatial")
    return con


def _envelope_sql(bbox) -> str:
    minx, miny, maxx, maxy = bbox
    return f"ST_MakeEnvelope({minx},{miny},{maxx},{maxy})"


def _envelope_sql_in(bbox, crs: str) -> str:
    """A lon/lat bbox expressed in `crs`, for tables that store projected
    geometry. Transforming the envelope beats transforming every row."""
    return (f"ST_Transform({_envelope_sql(bbox)}, 'EPSG:4326', '{crs}', "
            f"always_xy := true)")


def _to_wgs84_sql(expr: str, crs: str) -> str:
    return f"ST_Transform({expr}, '{crs}', 'EPSG:4326', always_xy := true)"


def _geom_rows(table: str, bbox, columns: list[str], limit: int):
    con = _catalog()
    try:
        # Cast every attribute to VARCHAR in SQL. The MLAS date columns are
        # TIMESTAMP WITH TIME ZONE, and DuckDB's Python conversion for that type
        # imports pytz — which is not a dependency of this repo and should not
        # become one so a map can show an issue date as text.
        cols = ", ".join(f'CAST("{c}" AS VARCHAR) AS "{c}"' for c in columns)
        sql = (f"SELECT ST_AsGeoJSON(geom) AS _g{', ' + cols if cols else ''} "
               f'FROM "{table}" WHERE ST_Intersects(geom, {_envelope_sql(bbox)}) '
               f"LIMIT {int(limit) + 1}")
        rows = con.execute(sql).fetchall()
        names = [d[0] for d in con.description]
        return rows, names
    finally:
        con.close()


def claims_layer(bbox=None, juris: str = "ON", max_features: int = MAX_FEATURES,
                 holder: str | None = None) -> Layer:
    """Current tenure polygons in the viewport, straight from the register."""
    bbox = bbox or DEFAULT_BBOX
    table = CLAIM_TABLES.get(juris)
    if not table:
        raise ValueError(f"no claim table registered for {juris!r}")
    cols = ["TENURE_NUM", "TENURE_STA", "HOLDER", "ISSUE_DATE", "CLAIM_DUE_"]
    rows, names = _geom_rows(table, bbox, cols, max_features)
    truncated = len(rows) > max_features
    rows = rows[:max_features]
    feats = []
    for row in rows:
        rec = dict(zip(names, row))
        geom = rec.pop("_g")
        props = {k: (None if v is None else str(v)) for k, v in rec.items()}
        feats.append(Feature(json.loads(geom), props))
    if holder:
        needle = holder.lower()
        feats = [f for f in feats if needle in (f.props.get("HOLDER") or "").lower()]
    meta = {"source": table, "jurisdiction": juris, "features": len(feats),
            "truncated": truncated, "bbox": list(bbox),
            "licence": "MNDM Electronic Information Products terms — internal use "
                       "only, redistribution requires written permission (audit H)."}
    return Layer("claims", feats, meta)


def context_layer(bbox=None, max_features: int = 2000) -> Layer:
    """Province outline and major lakes — the map's ground, in place of a
    third-party basemap."""
    bbox = bbox or DEFAULT_BBOX
    feats = []
    for name, table in CONTEXT_TABLES:
        try:
            rows, names = _geom_rows(table, bbox, [], max_features)
        except Exception:
            continue
        for row in rows[:max_features]:
            feats.append(Feature(json.loads(row[0]), {"context": name}))
    return Layer("context", feats, {"tables": [t for _, t in CONTEXT_TABLES],
                                    "features": len(feats), "bbox": list(bbox)})


def blocks_layer(bbox=None, juris: str = "ON", min_claims: int = 1,
                 max_features: int = 2000) -> Layer:
    """C1.4 ownership blocks. Geometry is stored as WKT in ownership.duckdb."""
    try:
        import duckdb
    except ImportError:  # pragma: no cover
        raise RuntimeError("duckdb is required for the blocks layer")
    if not OWNERSHIP_DB.exists():
        raise FileNotFoundError("ownership.duckdb missing: run land/ownership_graph.py")
    con = duckdb.connect(str(OWNERSHIP_DB), read_only=True)
    try:
        try:
            con.execute("LOAD spatial")
        except Exception:
            con.execute("INSTALL spatial; LOAD spatial")
        bbox = bbox or DEFAULT_BBOX
        geom = "ST_GeomFromText(geometry_wkt)"
        sql = f"""
            SELECT block_id, owner_id, owner_norm, n_claims, area_ha,
                   CAST(first_seen AS VARCHAR) AS first_seen,
                   CAST(last_change AS VARCHAR) AS last_change,
                   ST_AsGeoJSON({_to_wgs84_sql(geom, BLOCKS_CRS)}) AS _g
            FROM blocks
            WHERE juris = ? AND n_claims >= ?
              AND ST_Intersects({geom}, {_envelope_sql_in(bbox, BLOCKS_CRS)})
            ORDER BY n_claims DESC
            LIMIT {int(max_features) + 1}
        """
        rows = con.execute(sql, [juris, int(min_claims)]).fetchall()
        names = [d[0] for d in con.description]
    finally:
        con.close()
    truncated = len(rows) > max_features
    feats = []
    for row in rows[:max_features]:
        rec = dict(zip(names, row))
        geom = rec.pop("_g")
        feats.append(Feature(json.loads(geom),
                             {k: (None if v is None else str(v)) for k, v in rec.items()},
                             float(rec.get("n_claims") or 0)))
    return Layer("blocks", feats, {"source": "ownership.duckdb:blocks",
                                   "features": len(feats), "truncated": truncated,
                                   "min_claims": min_claims, "bbox": list(bbox)})


# ---------------------------------------------------------------------------
# Tenure-event pulses. The point-in-time archive is the differentiator
# (Master §8) — this is where it becomes visible.
# ---------------------------------------------------------------------------

def events_layer(bbox=None, start: str | None = None, end: str | None = None,
                 juris: str = "ON", event_types: str | None = None,
                 max_features: int = MAX_FEATURES) -> Layer:
    """Tenure events aggregated to their r7 cell over a time window.

    `tenure_events.parquet` carries `cell_r7` and no geometry, so the pulse is
    drawn on the hex. Events are windowed on `event_window_start`, which for a
    diffed event is the earlier of the two snapshot dates — the event happened
    somewhere inside the window, and dating it to the later snapshot would
    systematically shift the whole series forward."""
    bbox = bbox or DEFAULT_BBOX
    df = _read_parquet(EVENTS_PATH, columns=["juris", "event_type", "event_window_start",
                                             "cell_r7", "area_ha", "survivorship_biased"])
    if df is None:
        raise FileNotFoundError("tenure_events.parquet missing: run tenure_events.py")
    df = df[df["juris"] == juris]
    if event_types:
        wanted = {t.strip() for t in event_types.split(",") if t.strip()}
        df = df[df["event_type"].isin(wanted)]
    ws = pd.to_datetime(df["event_window_start"], errors="coerce")
    if start:
        df = df[ws >= pd.Timestamp(start)]
        ws = ws[ws >= pd.Timestamp(start)]
    if end:
        df = df[ws <= pd.Timestamp(end)]
    df = df.dropna(subset=["cell_r7"])
    n_events = len(df)

    grouped = (df.groupby(["cell_r7", "event_type"], as_index=False)
                 .agg(events=("event_type", "size"), area_ha=("area_ha", "sum")))
    wide = grouped.pivot(index="cell_r7", columns="event_type",
                         values="events").fillna(0).reset_index()
    wide["total"] = wide.drop(columns=["cell_r7"]).sum(axis=1)
    wide = _cached_centroids_frame(wide, "cell_r7")
    wide = wide[_bbox_mask(wide, bbox)]
    truncated = len(wide) > max_features
    if truncated:
        wide = wide.nlargest(max_features, "total")

    types = [c for c in wide.columns if c not in ("cell_r7", "total", "_lat", "_lng")]
    feats = []
    for r in wide.itertuples(index=False):
        d = r._asdict()
        props = {"cell_id": d["cell_r7"], "resolution": R7,
                 "by_type": {t: int(d[t]) for t in types if t in d}}
        feats.append(Feature(cell_polygon(d["cell_r7"]), props, float(d["total"])))
    meta = {"source": "tenure_events.parquet", "jurisdiction": juris,
            "start": start, "end": end, "event_types": sorted(types),
            "events_in_window": int(n_events), "features": len(feats),
            "truncated": truncated, "bbox": list(bbox), "resolution": R7,
            "survivorship_biased": bool(df["survivorship_biased"].any()) if len(df) else False,
            "note": "Windowed on event_window_start (the earlier snapshot of the "
                    "diff), so the series is not shifted forward."}
    return Layer("events", feats, meta)


def _cached_centroids_frame(df: pd.DataFrame, cell_col: str) -> pd.DataFrame:
    """Centroids for a frame built at request time (no file to key a cache on)."""
    return _with_centroids(df, cell_col)


def event_window(juris: str = "ON") -> dict:
    """Extent of the event series for the viewer's time scrubber.

    Filtered to one jurisdiction, because `events_layer` is. Unfiltered, the
    series runs 1899→2027: Yukon's `YT_HISTORICAL_CLAIMS` genuinely reaches back
    to 1899 (it never converted to map staking, so the paper record survives),
    and a scrubber spanning 128 years to drive an Ontario layer that starts in
    2018-04 would put every Ontario event in the last 6% of its travel.

    The reported extent is also clipped to today. Two Yukon rows carry `expired`
    dates in the future — a scheduled expiry, not an observed one — and three
    carry 1899-12-30, which is the OLE epoch-zero that spreadsheet exports write
    for a null date. Neither should set the end of a scrubber."""
    df = _read_parquet(EVENTS_PATH, columns=["juris", "event_type", "event_window_start"])
    if df is None:
        return {}
    df = df[df["juris"] == juris]
    ws = pd.to_datetime(df["event_window_start"], errors="coerce").dropna()
    today = pd.Timestamp.today().normalize()
    sane = ws[(ws > pd.Timestamp("1900-01-02")) & (ws <= today)]
    dropped = int(len(ws) - len(sane))
    return {"min": str(sane.min().date()) if len(sane) else None,
            "max": str(sane.max().date()) if len(sane) else None,
            "event_types": sorted(df["event_type"].dropna().unique().tolist()),
            "events": int(len(df)), "jurisdiction": juris,
            "out_of_range_dates_excluded": dropped}


# ---------------------------------------------------------------------------
# Cell evidence — what the popover shows when someone clicks a hex.
# ---------------------------------------------------------------------------

def _feature_index(path: Path):
    """`{feature: sorted value array}` for the whole store, built once per file.

    The naive version masked the full frame once per feature on the cell:
    66 features on a cell meant 66 scans of 10.7 M rows, 2.3 s per popover. The
    feature store grew tenfold when C2.2 landed 60 geophysics features, which
    turned an unnoticed inefficiency into the slowest thing in the viewer.

    Sorted arrays make each percentile a `searchsorted`, and the index is keyed
    on the parquet's mtime like every other cache here, so a nightly rebuild
    invalidates it without a restart."""
    import numpy as np

    def build(p: Path):
        df = _read_parquet(p)
        if df is None or df.empty:
            return {}, {}
        # One pass, grouped, rather than one pass per feature.
        idx = {}
        for feat, grp in df.groupby("feature", sort=False)["value"]:
            arr = grp.to_numpy(dtype="float64", copy=True)
            arr = arr[~np.isnan(arr)]
            arr.sort()
            idx[feat] = arr
        by_cell = {cid: g for cid, g in df.groupby("cell_id", sort=False)}
        return idx, by_cell

    return _cached(path, build, "featindex")


def _feature_percentiles(cell_id: str, top_k: int = 12) -> dict:
    """Per-evidence-layer values against the regional distribution (PLAN_C4 4.1
    section 5). Percentile, not raw value: "0.42" means nothing to a reader,
    "94th percentile for Ontario" means something."""
    import numpy as np

    if not FEATURES_DIR.exists():
        return {"available": False, "reason": "no feature store on disk"}
    fabs = sorted(p for p in FEATURES_DIR.iterdir() if p.is_dir())
    if not fabs:
        return {"available": False, "reason": "no fabric versions in the feature store"}
    snaps = sorted(p for p in fabs[-1].iterdir() if p.is_dir())
    path = next((s / "features.parquet" for s in reversed(snaps)
                 if (s / "features.parquet").exists()), None)
    if path is None:
        return {"available": False, "reason": "no features.parquet in any snapshot"}

    built = _feature_index(path)
    if not built:
        return {"available": False, "reason": "feature store is empty"}
    idx, by_cell = built
    mine = by_cell.get(cell_id)
    if mine is None or mine.empty:
        return {"available": False, "reason": f"cell {cell_id} is not in the feature store",
                "snapshot": path.parent.name}

    out = []
    for r in mine.itertuples(index=False):
        pop = idx.get(r.feature)
        if pop is None or not len(pop) or r.value != r.value:
            pct, n = None, 0 if pop is None else len(pop)
        else:
            # `<= value` to match the previous definition exactly: the share of
            # the population this cell is at or above.
            n = len(pop)
            pct = float(np.searchsorted(pop, r.value, side="right") / n * 100)
        out.append({"feature": r.feature, "value": float(r.value),
                    "percentile": None if pct is None else round(pct, 1),
                    "n_cells_with_feature": int(n)})
    out.sort(key=lambda d: (d["percentile"] is None, -(d["percentile"] or 0)))
    return {"available": True, "snapshot": path.parent.name,
            "fabric_version": path.parent.parent.name,
            "features": out[:top_k], "n_features_on_cell": len(out)}


def cell_evidence(cell_id: str, juris: str = "ON") -> dict:
    """Everything the system knows about one cell, at whatever resolution it is.

    An r9 click and an r7 click are different questions — the r9 one is "can I
    stake this and what is next to it", the r7 one is "is this region worth
    attention" — so the payload differs by resolution rather than pretending to
    a common shape."""
    if not h3.is_valid_cell(cell_id):
        raise ValueError(f"{cell_id!r} is not a valid H3 cell id")
    res = h3.get_resolution(cell_id)
    lat, lng = h3.cell_to_latlng(cell_id)
    out: dict = {"cell_id": cell_id, "resolution": res, "centroid": [lng, lat],
                 "jurisdiction": juris,
                 "area_km2": round(h3.cell_area(cell_id, unit="km^2"), 4)}

    r7 = cell_id if res == R7 else (h3.cell_to_parent(cell_id, R7) if res > R7 else None)
    out["cell_r7"] = r7

    # --- heat series (C1.3) -------------------------------------------------
    if r7:
        heat = _read_parquet(HEAT_PATH)
        if heat is not None:
            sl = heat[heat["cell_r7"] == r7].sort_values("quarter")
            if not sl.empty:
                out["heat"] = {
                    "quarters": sl["quarter"].tolist(),
                    "heat_cross_smoothed": [None if pd.isna(v) else round(float(v), 3)
                                            for v in sl["heat_cross_smoothed"]],
                    "cells_staked": [int(v) for v in sl["cells_staked"].fillna(0)],
                    "expiry_count": [int(v) for v in sl["expiry_count"].fillna(0)],
                    "ever_staked_count": int(sl["ever_staked_count"].max()),
                    "survivorship_biased": bool(sl["survivorship_biased"].any()),
                    "source": "heat.parquet",
                }
            else:
                out["heat"] = {"available": False,
                               "reason": "no staking events have ever touched this cell"}

    # --- prospectivity (C2.1) ----------------------------------------------
    if r7:
        try:
            scores, pmeta = prospectivity_values()
            row = scores[scores["cell_id"] == r7]
            if not row.empty:
                v = float(row["value"].iloc[0])
                pop = scores["value"]
                out["prospectivity"] = {
                    "score": round(v, 5),
                    "percentile": round(float((pop <= v).mean() * 100), 1),
                    "system": pmeta["system"], "version": pmeta["version"],
                    "blocked_roc_auc_mean": pmeta["blocked_roc_auc_mean"],
                    "scores": pmeta["scores"], "source": pmeta["source"],
                }
        except FileNotFoundError as e:
            out["prospectivity"] = {"available": False, "reason": str(e)}

    # --- land state (C1.1) --------------------------------------------------
    land = {"available": False, "reason": "no AOI on disk covers this cell"}
    for aoi in available_aois():
        try:
            df, jmeta = _land_frame(aoi["aoi_id"], aoi.get("jurisdiction") or juris)
        except FileNotFoundError:
            continue
        if res == R9:
            row = df[df["cell_id"] == cell_id]
            if not row.empty:
                land = {"available": True, "aoi": aoi["aoi_id"],
                        "state": row["state"].iloc[0],
                        "blocking_layer": row["blocking_layer"].iloc[0],
                        "as_of": str(row["as_of_snapshot"].iloc[0]),
                        "source": f"land_state/{aoi['jurisdiction']}__{aoi['aoi_id']}.parquet"}
                break
        elif r7:
            kids = df[df["parent"] == r7]
            if not kids.empty:
                counts = kids["state"].value_counts().to_dict()
                land = {"available": True, "aoi": aoi["aoi_id"], "rollup": "r9 children",
                        "state_counts": {k: int(v) for k, v in counts.items()},
                        "open_cells": int(counts.get("open", 0)),
                        "open_fraction": round(counts.get("open", 0) / len(kids), 4),
                        "as_of": jmeta.get("snapshot"),
                        "source": f"land_state/{aoi['jurisdiction']}__{aoi['aoi_id']}.parquet"}
                break
    out["land_state"] = land

    # --- criticality (C1.5) -------------------------------------------------
    crit = _read_parquet(CRIT_PATH)
    if crit is not None:
        if res == R9:
            sl = crit[crit["cell_id"] == cell_id]
        else:
            kids = crit["cell_id"].map(lambda c: h3.cell_to_parent(c, res) == cell_id) \
                if res < R9 else pd.Series(False, index=crit.index)
            sl = crit[kids]
        if not sl.empty:
            top = sl.nlargest(5, "score")
            out["criticality"] = {
                "max_score": round(float(sl["score"].max()), 4),
                "blocks": sorted(sl["block_id"].unique().tolist()),
                "top": [{"block_id": r.block_id, "owner_id": r.owner_id,
                         "score": round(float(r.score), 4),
                         "reason_codes": (json.loads(r.reason_codes)
                                          if isinstance(r.reason_codes, str) else r.reason_codes),
                         "distance_m": None if pd.isna(r.distance_m) else round(float(r.distance_m), 1)}
                        for r in top.itertuples(index=False)],
                "source": "criticality.parquet",
                "note": "Criticality is scored on open cells: a score here means "
                        "a neighbour's trend runs through this ground.",
            }
        else:
            out["criticality"] = {"available": False,
                                  "reason": "not on any watched block's frontier "
                                            "(criticality is scored on open cells only)"}

    # --- lapse watch (C1.6) -------------------------------------------------
    lapse = _read_parquet(LAPSE_PATH)
    if lapse is not None and not lapse.empty:
        col = "cell_r9" if res == R9 else "cell_r7"
        key = cell_id if res in (R7, R9) else None
        sl = lapse[lapse[col] == key] if key else lapse.iloc[0:0]
        if not sl.empty:
            out["lapse_watch"] = {
                "claims": [{"claim_id": r.claim_id, "expiry_date": str(r.expiry_date),
                            "days_to_expiry": int(r.days_to_expiry), "owner": r.owner,
                            "status": r.status,
                            "reasons": (json.loads(r.reasons)
                                        if isinstance(r.reasons, str) else r.reasons)}
                           for r in sl.itertuples(index=False)],
                "source": "lapse_watch.parquet",
                "note": "status is watch_only until rules/<juris>.yaml carries a "
                        "reopening_delay_days — C1.6 refuses to say 'stakeable'.",
            }

    # --- neighbours & buyers (C1.4 / C6.2) ----------------------------------
    out["neighbours"] = _neighbours(cell_id, res, juris)

    # --- evidence layers (C2) ----------------------------------------------
    if r7:
        out["features"] = _feature_percentiles(r7)

    out["dossier"] = _dossier_status(f"{juris}-{cell_id}")
    return json_safe(out)


def _neighbours(cell_id: str, res: int, juris: str, radius_km: float = 5.0) -> dict:
    """Blocks within `radius_km`, with their C6.2 buyer profile where resolved."""
    if not OWNERSHIP_DB.exists():
        return {"available": False, "reason": "ownership.duckdb missing"}
    try:
        import duckdb
    except ImportError:  # pragma: no cover
        return {"available": False, "reason": "duckdb not installed"}
    lat, lng = h3.cell_to_latlng(cell_id)
    # Degrees of longitude per km shrink with latitude; at 48°N one degree of
    # longitude is ~74 km against 111 km for latitude. Using a single degree
    # box would search 50% too wide east-west.
    dlat = radius_km / 111.0
    dlng = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    con = duckdb.connect(str(OWNERSHIP_DB), read_only=True)
    try:
        try:
            con.execute("LOAD spatial")
        except Exception:
            con.execute("INSTALL spatial; LOAD spatial")
        box = (lng - dlng, lat - dlat, lng + dlng, lat + dlat)
        rows = con.execute(f"""
            SELECT block_id, owner_id, owner_norm, n_claims, area_ha,
                   CAST(last_change AS VARCHAR) AS last_change
            FROM blocks
            WHERE juris = ?
              AND ST_Intersects(ST_GeomFromText(geometry_wkt),
                                {_envelope_sql_in(box, BLOCKS_CRS)})
            ORDER BY n_claims DESC LIMIT 25
        """, [juris]).fetchall()
        names = [d[0] for d in con.description]
    finally:
        con.close()
    blocks = [dict(zip(names, r)) for r in rows]
    for b in blocks:
        for k, v in list(b.items()):
            if hasattr(v, "isoformat"):
                b[k] = v.isoformat()

    buyers_path = MARKET_DIR / "buyers.parquet"
    buyers = _read_parquet(buyers_path)
    if buyers is not None and blocks:
        by_owner = {r["owner_id"]: r for r in buyers.to_dict("records")}
        for b in blocks:
            prof = by_owner.get(b["owner_id"])
            if not prof:
                continue
            b["buyer"] = {
                "name": prof.get("name_raw"),
                "sedar_issuer_id": prof.get("sedar_issuer_id"),
                "resolution_status": prof.get("resolution_status"),
                "profile_thin": bool(prof.get("profile_thin")),
                "consolidator_flag": bool(prof.get("consolidator_flag")),
                "buyer_propensity": (None if prof.get("buyer_propensity") is None
                                     else float(prof["buyer_propensity"])),
                "financings_closed_24mo": prof.get("n_financing_closed_24mo"),
                "material_change_24mo": prof.get("n_material_change_24mo"),
                "missing_because": _maybe_json(prof.get("missing_because")),
            }
    return {"available": True, "radius_km": radius_km, "blocks": blocks,
            "source": "ownership.duckdb:blocks + market/buyers.parquet",
            "note": "buyer_capacity and buyer_timing are null by design until the "
                    "filing PDFs are extracted (audit L1)."}


def _dossier_status(target_id: str) -> dict:
    d = DOSSIER_DIR / target_id
    if not d.exists():
        return {"exists": False, "target_id": target_id}
    versions = sorted(p.stem for p in d.glob("*.json"))
    latest = versions[-1] if versions else None
    status = None
    if latest:
        try:
            status = json.loads((d / f"{latest}.json").read_text()).get("status")
        except Exception:
            pass
    return {"exists": bool(versions), "target_id": target_id, "versions": versions,
            "latest": latest, "status": status,
            "html": f"/api/dossier/{target_id}/{latest}.html" if latest else None}


# ---------------------------------------------------------------------------
# Static PNG rendering. Pillow only — matplotlib is not a dependency of this
# repo and a figure renderer is not worth making it one. Both the GeoJSON
# endpoints and this function consume the same Layer objects, so a figure in a
# dossier cannot disagree with the screen (PLAN_C4 4.2: "one map code path").
# ---------------------------------------------------------------------------

#: Sequential ramp, dark-to-bright. Deliberately not a rainbow: rainbow ramps
#: invent visual boundaries where the data is continuous.
RAMP = [(13, 27, 42), (27, 60, 92), (32, 104, 122), (68, 148, 106),
        (168, 181, 74), (238, 191, 62), (247, 129, 46), (222, 62, 46)]


def _ramp_colour(t: float) -> tuple[int, int, int]:
    t = 0.0 if t != t else max(0.0, min(1.0, t))
    pos = t * (len(RAMP) - 1)
    i = int(pos)
    if i >= len(RAMP) - 1:
        return RAMP[-1]
    f = pos - i
    a, b = RAMP[i], RAMP[i + 1]
    return tuple(int(round(a[j] + (b[j] - a[j]) * f)) for j in range(3))


def _mercator_y(lat: float) -> float:
    lat = max(-85.05, min(85.05, lat))
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _projector(bbox, width: int, height: int):
    """lon/lat -> pixel, in Web Mercator so the rendered figure has the same
    shape as the screen map rather than a stretched plate-carrée one."""
    minx, miny, maxx, maxy = bbox
    y0, y1 = _mercator_y(miny), _mercator_y(maxy)

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = (lon - minx) / (maxx - minx) * width
        y = height - (_mercator_y(lat) - y0) / (y1 - y0) * height
        return (x, y)

    return project


def _rings(geom: dict):
    """Every exterior/interior ring in a GeoJSON geometry, as coordinate lists."""
    t = geom.get("type")
    if t == "Polygon":
        return list(geom["coordinates"])
    if t == "MultiPolygon":
        return [ring for poly in geom["coordinates"] for ring in poly]
    if t == "LineString":
        return [geom["coordinates"]]
    if t == "MultiLineString":
        return list(geom["coordinates"])
    if t == "Point":
        return [[geom["coordinates"]]]
    return []


#: Layers drawn as outlines over whatever is beneath them. A block or a claim
#: boundary answers "whose ground is this" — filling it hides the heat or land
#: state the reader is actually comparing it against.
STROKE_ONLY = {"blocks", "claims"}

#: Pillow's built-in bitmap font is latin-1 only and silently draws a box for
#: anything else, which is how an em dash becomes a tofu glyph in a figure that
#: goes in front of a buyer.
_ASCII_SUBS = {"—": "-", "–": "-", "’": "'", "“": '"',
               "”": '"', "©": "(c)", "°": "deg", "×": "x"}


def _plain(s: str) -> str:
    for k, v in _ASCII_SUBS.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def render_png(layers: list[Layer], bbox, width: int = 900, height: int = 700,
               title: str | None = None, marker=None, attribution: str | None = None,
               legend: bool = True, supersample: int = 2) -> bytes:
    """Draw layers to a PNG. `marker` is an optional (lon, lat) for the subject
    cell, so a dossier inset always says where the target is.

    Drawn at `supersample`× and reduced: Pillow's polygon fill has no
    antialiasing, and at 1× the hex fabric shows white seams along every shared
    edge — an artifact a reader reasonably reads as missing data."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:  # pragma: no cover
        raise RuntimeError("pip install pillow — required for /render")

    ss = max(1, min(int(supersample), 4))
    W, H = width * ss, height * ss
    img = Image.new("RGB", (W, H), (247, 247, 245))
    draw = ImageDraw.Draw(img, "RGBA")
    project = _projector(bbox, W, H)
    ramp_used: tuple[float, float, str] | None = None

    for layer in layers:
        vals = [f.value for f in layer.features if f.value is not None]
        lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
        span = (hi - lo) or 1.0
        stroke_only = layer.name in STROKE_ONLY
        if vals and not stroke_only and layer.name != "context":
            ramp_used = (lo, hi, layer.meta.get("label") or layer.name)
        for f in layer.features:
            width_px = max(1, ss)
            if layer.name == "context":
                fill = (222, 232, 240, 255) if f.props.get("context") == "lakes" else (255, 255, 255, 255)
                outline = (185, 195, 205, 255)
            elif stroke_only:
                fill = None
                outline = (35, 45, 60, 220)
                width_px = max(1, ss)
            elif f.props.get("colour"):
                c = f.props["colour"].lstrip("#")
                fill = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (200,)
                outline = None
            elif f.value is not None:
                fill = _ramp_colour((f.value - lo) / span) + (205,)
                outline = None
            else:
                fill = (90, 110, 130, 70)
                outline = (70, 90, 110, 160)
            for ring in _rings(f.geometry):
                pts = [project(x, y) for x, y in ring]
                if len(pts) < 3:
                    if len(pts) == 1:
                        x, y = pts[0]
                        r = 2 * ss
                        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill or outline)
                    continue
                if stroke_only:
                    draw.line(pts + [pts[0]], fill=outline, width=width_px)
                else:
                    draw.polygon(pts, fill=fill, outline=outline)

    if marker:
        mx, my = project(*marker)
        for r, col in ((11, (20, 20, 20, 255)), (8, (255, 255, 255, 255)), (5, (215, 40, 40, 255))):
            rr = r * ss
            draw.ellipse([mx - rr, my - rr, mx + rr, my + rr], fill=col)

    img = img.resize((width, height), Image.LANCZOS)
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.load_default()
    except Exception:  # pragma: no cover
        font = None
    if title:
        draw.rectangle([0, 0, width, 22], fill=(255, 255, 255, 225))
        draw.text((8, 6), _plain(title), fill=(20, 20, 20), font=font)
    if legend and ramp_used:
        lo, hi, label = ramp_used
        bar_w, bar_h, x0 = 150, 9, width - 162
        y0 = height - (34 if attribution else 16)
        draw.rectangle([x0 - 6, y0 - 13, width - 4, y0 + bar_h + 3], fill=(255, 255, 255, 225))
        for i in range(bar_w):
            draw.line([(x0 + i, y0), (x0 + i, y0 + bar_h)],
                      fill=_ramp_colour(i / (bar_w - 1)))
        draw.text((x0, y0 - 12), _plain(label)[:28], fill=(30, 30, 30), font=font)
        draw.text((x0, y0 + bar_h + 1), f"{lo:.3g}", fill=(70, 70, 70), font=font)
        draw.text((x0 + bar_w - 34, y0 + bar_h + 1), f"{hi:.3g}", fill=(70, 70, 70), font=font)
    if attribution:
        draw.rectangle([0, height - 16, width, height], fill=(255, 255, 255, 225))
        draw.text((8, height - 12), _plain(attribution), fill=(70, 70, 70), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


#: Layer names /render accepts, mapped to their resolver. Keeping this explicit
#: rather than reflecting over the module means a typo in a dossier template is
#: a 400 with the valid names, not a blank figure.
RENDER_LAYERS = {
    "context": lambda **kw: context_layer(bbox=kw.get("bbox")),
    "claims": lambda **kw: claims_layer(bbox=kw.get("bbox"), max_features=kw.get("max_features", 4000)),
    "blocks": lambda **kw: blocks_layer(bbox=kw.get("bbox")),
    "land": lambda **kw: land_layer(aoi=kw.get("aoi", "abitibi"), bbox=kw.get("bbox")),
    "criticality": lambda **kw: criticality_layer(bbox=kw.get("bbox"), block=kw.get("block")),
    "heat": lambda **kw: fabric_layer("heat", bbox=kw.get("bbox")),
    "prospectivity": lambda **kw: fabric_layer("prospectivity", bbox=kw.get("bbox")),
    "events": lambda **kw: events_layer(bbox=kw.get("bbox"), start=kw.get("start"),
                                        end=kw.get("end")),
}


def cell_bbox(cell_id: str, pad: float = 8.0) -> tuple[float, float, float, float]:
    """A bbox around one cell, padded by `pad` cell-widths so the inset shows
    the target in its neighbourhood rather than filling the frame with it."""
    ring = h3.cell_to_boundary(cell_id)
    lats = [p[0] for p in ring]
    lngs = [p[1] for p in ring]
    dlat = (max(lats) - min(lats)) * pad
    dlng = (max(lngs) - min(lngs)) * pad
    return (min(lngs) - dlng, min(lats) - dlat, max(lngs) + dlng, max(lats) + dlat)


def render_cell_inset(cell_id: str, layers: tuple[str, ...] = ("context", "land", "claims"),
                      width: int = 760, height: int = 520, pad: float = 8.0,
                      title: str | None = None, aoi: str = "abitibi") -> bytes:
    """The dossier's section-1 figure: the target cell in its land context."""
    bbox = cell_bbox(cell_id, pad)
    built = []
    for name in layers:
        fn = RENDER_LAYERS.get(name)
        if not fn:
            continue
        try:
            built.append(fn(bbox=bbox, aoi=aoi))
        except Exception:
            continue
    lat, lng = h3.cell_to_latlng(cell_id)
    return render_png(built, bbox, width, height,
                      title=title or f"{cell_id} - land context",
                      marker=(lng, lat),
                      attribution="Derived from MNDM MLAS and Ontario LIO data. "
                                  "© King's Printer for Ontario.")


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

def build_router():
    from fastapi import APIRouter, HTTPException, Query
    from fastapi.responses import JSONResponse, Response

    router = APIRouter()

    def _bbox_or_400(bbox: str | None):
        try:
            return parse_bbox(bbox)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    def _guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/api/catalog")
    def catalog():
        """What the viewer can currently show. Every entry is checked against
        disk, so a missing artifact appears as unavailable-with-a-reason rather
        than as a layer that 503s when someone ticks it."""
        metrics = []
        for name, (resolver, label) in FABRIC_METRICS.items():
            entry = {"metric": name, "label": label, "available": True}
            try:
                _, meta = resolver()
                entry["meta"] = {k: v for k, v in meta.items() if k != "note"}
            except Exception as e:
                entry["available"] = False
                entry["reason"] = str(e)
            metrics.append(entry)
        models = []
        if MODEL_DIR.exists():
            for sysdir in sorted(p for p in MODEL_DIR.iterdir() if p.is_dir()):
                models.append({"system": sysdir.name,
                               "versions": sorted(p.name for p in sysdir.iterdir() if p.is_dir())})
        blocks = []
        crit = _read_parquet(CRIT_PATH)
        if crit is not None and not crit.empty:
            agg = (crit.groupby(["block_id", "owner_id"], as_index=False)
                       .agg(cells=("cell_id", "size"), max_score=("score", "max")))
            blocks = [{"block_id": r.block_id, "owner_id": r.owner_id,
                       "cells": int(r.cells), "max_score": round(float(r.max_score), 4)}
                      for r in agg.nlargest(50, "max_score").itertuples(index=False)]
        return json_safe({"fabric_metrics": metrics, "models": models, "aois": available_aois(),
                "watched_blocks": blocks, "events": event_window(),
                "land_colours": LAND_COLOURS, "default_bbox": list(DEFAULT_BBOX),
                "max_features": MAX_FEATURES,
                "dossiers": sorted(p.name for p in DOSSIER_DIR.iterdir()) if DOSSIER_DIR.exists() else []})

    @router.get("/api/fabric")
    def api_fabric(metric: str = Query("heat"), bbox: str = Query(None),
                   res: int = Query(None), agg: str = Query("max"),
                   quarter: str = Query(None), column: str = Query(None),
                   system: str = Query(None), version: str = Query(None),
                   block: str = Query(None),
                   max_features: int = Query(MAX_FEATURES)):
        params = {}
        if metric == "heat":
            if quarter:
                params["quarter"] = quarter
            if column:
                params["column"] = column
        elif metric == "prospectivity":
            if system:
                params["system"] = system
            if version:
                params["version"] = version
        elif metric == "criticality" and block:
            params["block"] = block
        layer = _guard(fabric_layer, metric, _bbox_or_400(bbox), res, agg,
                       min(int(max_features), 20000), "ON", **params)
        return JSONResponse(layer.geojson())

    @router.get("/api/land")
    def api_land(aoi: str = Query("abitibi"), bbox: str = Query(None),
                 states: str = Query(None), max_features: int = Query(MAX_FEATURES)):
        layer = _guard(land_layer, aoi, _bbox_or_400(bbox), "ON", states,
                       min(int(max_features), 20000))
        return JSONResponse(layer.geojson())

    @router.get("/api/criticality")
    def api_criticality(bbox: str = Query(None), block: str = Query(None),
                        min_score: float = Query(0.0),
                        max_features: int = Query(MAX_FEATURES)):
        layer = _guard(criticality_layer, _bbox_or_400(bbox), block, min_score,
                       min(int(max_features), 20000))
        return JSONResponse(layer.geojson())

    @router.get("/api/claims")
    def api_claims(bbox: str = Query(None), holder: str = Query(None),
                   max_features: int = Query(MAX_FEATURES)):
        layer = _guard(claims_layer, _bbox_or_400(bbox), "ON",
                       min(int(max_features), 20000), holder)
        return JSONResponse(layer.geojson())

    @router.get("/api/blocks")
    def api_blocks(bbox: str = Query(None), min_claims: int = Query(1),
                   max_features: int = Query(2000)):
        layer = _guard(blocks_layer, _bbox_or_400(bbox), "ON", min_claims,
                       min(int(max_features), 10000))
        return JSONResponse(layer.geojson())

    @router.get("/api/context")
    def api_context(bbox: str = Query(None)):
        return JSONResponse(_guard(context_layer, _bbox_or_400(bbox)).geojson())

    @router.get("/api/events")
    def api_events(bbox: str = Query(None), start: str = Query(None),
                   end: str = Query(None), event_types: str = Query(None),
                   max_features: int = Query(MAX_FEATURES)):
        layer = _guard(events_layer, _bbox_or_400(bbox), start, end, "ON",
                       event_types, min(int(max_features), 20000))
        return JSONResponse(layer.geojson())

    @router.get("/api/cell/{cell_id}")
    def api_cell(cell_id: str, juris: str = Query("ON")):
        return JSONResponse(json_safe(_guard(cell_evidence, cell_id, juris)))

    @router.get("/api/dossiers")
    def api_dossiers():
        if not DOSSIER_DIR.exists():
            return {"dossiers": []}
        out = []
        for d in sorted(DOSSIER_DIR.iterdir()):
            if d.is_dir():
                out.append(_dossier_status(d.name))
        return json_safe({"dossiers": out})

    @router.get("/api/dossier/{target_id}/{filename}")
    def api_dossier_file(target_id: str, filename: str):
        from fastapi.responses import HTMLResponse
        # Path components come off the URL; anything with a separator or a
        # parent reference is rejected rather than normalised, so the endpoint
        # cannot be walked out of the dossier directory.
        for part in (target_id, filename):
            if "/" in part or "\\" in part or part.startswith("."):
                raise HTTPException(status_code=400, detail="bad path component")
        path = (DOSSIER_DIR / target_id / filename).resolve()
        if DOSSIER_DIR.resolve() not in path.parents or not path.exists():
            raise HTTPException(status_code=404, detail="no such dossier file")
        if path.suffix == ".json":
            return JSONResponse(json.loads(path.read_text()))
        return HTMLResponse(path.read_text())

    @router.post("/api/dossier")
    def api_generate_dossier(cell_id: str = Query(...), juris: str = Query("ON"),
                             profile: str = Query("internal")):
        """Run the C4.1 assembler for a cell.

        Runs it as a subprocess rather than importing: assembly opens the
        catalog, the ownership store and the feature store and can take tens of
        seconds, and a failure inside it must not take the map server with it.
        No dossier auto-advances past draft — this only produces one."""
        if not h3.is_valid_cell(cell_id):
            raise HTTPException(status_code=400, detail=f"{cell_id!r} is not a valid H3 cell")
        if profile not in ("internal", "sales"):
            raise HTTPException(status_code=400, detail="profile must be internal|sales")
        cmd = [sys.executable, str(Path(__file__).parent / "dossier" / "assemble.py"),
               "--cell", cell_id, "--juris", juris, "--profile", profile]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                  cwd=str(Path(__file__).parent))
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="dossier assembly timed out after 600s")
        status = _dossier_status(f"{juris}-{cell_id}")
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:],
                "dossier": status}

    @router.get("/render")
    def api_render(layers: str = Query("context,claims,heat"), bbox: str = Query(None),
                   width: int = Query(900), height: int = Query(700),
                   cell: str = Query(None), pad: float = Query(8.0),
                   title: str = Query(None), aoi: str = Query("abitibi"),
                   block: str = Query(None), start: str = Query(None),
                   end: str = Query(None)):
        names = [n.strip() for n in layers.split(",") if n.strip()]
        unknown = [n for n in names if n not in RENDER_LAYERS]
        if unknown:
            raise HTTPException(status_code=400,
                                detail=f"unknown layers {unknown}; have {sorted(RENDER_LAYERS)}")
        width = max(64, min(int(width), 2400))
        height = max(64, min(int(height), 2400))
        if cell:
            if not h3.is_valid_cell(cell):
                raise HTTPException(status_code=400, detail=f"{cell!r} is not a valid H3 cell")
            box = cell_bbox(cell, pad)
            marker = tuple(reversed(h3.cell_to_latlng(cell)))
        else:
            box = _bbox_or_400(bbox)
            marker = None
        built = []
        for n in names:
            try:
                built.append(RENDER_LAYERS[n](bbox=box, aoi=aoi, block=block,
                                              start=start, end=end))
            except FileNotFoundError:
                continue
        png = render_png(built, box, width, height, title=title, marker=marker,
                         attribution="Derived from MNDM MLAS and Ontario LIO data. "
                                     "© King's Printer for Ontario.")
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    return router


# ---------------------------------------------------------------------------
# CLI smoke test — exercises every resolver against the real lake without
# starting a server, which is what you want when a nightly harvest has just
# rewritten an artifact.
# ---------------------------------------------------------------------------

def smoke(verbose: bool = True) -> int:
    box = (-81.5, 48.0, -80.0, 49.0)
    checks = [
        ("context", lambda: context_layer(bbox=box)),
        ("fabric:heat", lambda: fabric_layer("heat", bbox=box)),
        ("fabric:heat/province", lambda: fabric_layer("heat", bbox=DEFAULT_BBOX)),
        ("fabric:prospectivity", lambda: fabric_layer("prospectivity", bbox=box)),
        ("fabric:ever_staked", lambda: fabric_layer("ever_staked", bbox=box)),
        ("fabric:criticality", lambda: fabric_layer("criticality", bbox=box)),
        ("land", lambda: land_layer(bbox=box)),
        ("criticality", lambda: criticality_layer(bbox=box)),
        ("claims", lambda: claims_layer(bbox=box)),
        ("blocks", lambda: blocks_layer(bbox=box)),
        ("events", lambda: events_layer(bbox=box, start="2024-01-01")),
    ]
    failures = 0
    for name, fn in checks:
        try:
            layer = fn()
            if verbose:
                m = layer.meta
                extra = f" res={m.get('resolution')}" if m.get("resolution") else ""
                extra += " AGG" if m.get("aggregated") else ""
                extra += " TRUNC" if m.get("truncated") else ""
                print(f"  ok   {name:26s} {len(layer.features):6d} features{extra}")
        except Exception as e:
            failures += 1
            print(f"  FAIL {name:26s} {type(e).__name__}: {e}")
    try:
        ev = cell_evidence("872b96829ffffff")
        present = [k for k in ("heat", "prospectivity", "land_state", "criticality",
                               "neighbours", "features") if k in ev]
        print(f"  ok   {'cell_evidence':26s} sections: {present}")
    except Exception as e:
        failures += 1
        print(f"  FAIL {'cell_evidence':26s} {type(e).__name__}: {e}")
    try:
        png = render_cell_inset("892b968aac7ffff")
        print(f"  ok   {'render_cell_inset':26s} {len(png):,} bytes PNG")
    except Exception as e:
        failures += 1
        print(f"  FAIL {'render_cell_inset':26s} {type(e).__name__}: {e}")
    return failures


def main():
    import argparse
    ap = argparse.ArgumentParser(description="C4.2 map service resolvers")
    ap.add_argument("--smoke", action="store_true", help="exercise every resolver")
    ap.add_argument("--render", metavar="CELL", help="write a cell inset PNG to --out")
    ap.add_argument("--out", default="/tmp/cell_inset.png")
    args = ap.parse_args()
    if args.render:
        Path(args.out).write_bytes(render_cell_inset(args.render))
        print(f"wrote {args.out}")
        return
    if args.smoke:
        C.require_lake()
        print("C4.2 resolver smoke test")
        n = smoke()
        print(("all resolvers ok" if not n else f"{n} FAILURE(S)"))
        sys.exit(1 if n else 0)
    ap.print_help()


if __name__ == "__main__":
    main()
