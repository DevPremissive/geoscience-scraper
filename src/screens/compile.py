#!/usr/bin/env python3
"""
compile.py — screen AST → DuckDB SQL over the feature store (C4.3).

The feature store is long-format `(cell_id, feature, value)`, which is right for
storage and wrong for querying: a screen wants `fault__km < 5 AND
cmmi_gravity__gravity_hgm__mean >= percentile(90)` as one predicate over one
row. So the compiler pivots exactly the features a screen mentions and no
others — 299 features pivoted in full would be a 164,577 × 299 table built to
answer a question about three columns.

**Everything resolves against a NAMED snapshot.** Master §4 forbids an implicit
latest, and a screen is the artifact most likely to be re-run months later and
compared against its earlier self. The snapshot path is compiled into the SQL as
a literal, so the emitted SQL is a complete, self-contained record of what was
asked — you can read it back in a year and know exactly which bytes it read.

Percentile scopes resolve against the jurisdiction or the fabric's terrane
strata, matching C2.3's normalisation so a screen and a model mean the same
thing by "anomalous".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from screens import helpers as HLP
from screens.dsl import Percentile, Predicate, Screen

#: Point classes `count(...)` and `dist_to(...)` understand, and where they live.
POINT_CLASSES = {
    "nonbarren_holes": {
        "path": C.PROCESSED_DIR / "nonbarren_holes.parquet",
        "lon": "longitude", "lat": "latitude",
        "note": "C5.2 intercepts — drilled, hit, and the ground let go",
    },
    "negatives": {
        "path": C.PROCESSED_DIR / "negatives.parquet",
        "lon": "longitude", "lat": "latitude",
        "note": "C2.4 tier-1 barren holes",
    },
    "drillholes": {
        "table": "geo_ON__ON_OMEIS_DRILLHOLE",
        "lon": "LONGITUDE_DD", "lat": "LATITUDE_DD",
        "note": "OMEIS exploration drillholes",
    },
    "occurrences": {
        "table": "geo_ON__ON_OMEIS_MINERAL_INV",
        "lon": None, "lat": None,
        "note": "OMEIS mineral inventory (geometry column)",
    },
}


class ScreenCompileError(ValueError):
    pass


def feature_snapshot(fabric_version: str | None = None,
                     snapshot: str | None = None) -> tuple:
    """Resolve to an explicit `(fabric_version, snapshot, path)`.

    Picking the newest is allowed HERE, once, at the boundary — and the result
    is recorded on the run so the next execution can be pinned to it. What is
    forbidden is a query that says "latest" internally and therefore means
    something different each time it runs."""
    root = C.PROCESSED_DIR / "features"
    if not root.exists():
        raise ScreenCompileError("no feature store on disk")
    fabs = sorted(p.name for p in root.iterdir() if p.is_dir())
    fv = fabric_version or (fabs[-1] if fabs else None)
    if fv is None:
        raise ScreenCompileError("no fabric versions in the feature store")
    snaps = sorted(p.name for p in (root / fv).iterdir()
                   if (p / "features.parquet").exists())
    if not snaps:
        raise ScreenCompileError(f"no snapshot with features under {fv}")
    sn = snapshot or snaps[-1]
    path = root / fv / sn / "features.parquet"
    if not path.exists():
        raise ScreenCompileError(f"no features.parquet for {fv}/{sn}")
    return fv, sn, path


def _q(v) -> str:
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return repr(v)


def compile_screen(scr: Screen, fabric_version: str | None = None,
                   snapshot: str | None = None) -> dict:
    """Screen → `{sql, features_used, snapshot, ...}`."""
    fv, sn, fpath = feature_snapshot(fabric_version, snapshot)
    juris = (scr.juris or ["ON"])[0]

    feats = sorted({p.name for p in scr.where
                    if p.kind == "feature" and p.name})
    if scr.rank_by:
        for p in scr.where:
            pass
        # A ranked column must exist as a feature or be one of the derived
        # columns the compiler provides.
        for tok in _identifiers(scr.rank_by):
            if tok not in feats and tok not in _DERIVED:
                feats.append(tok)
        feats = sorted(set(feats))

    ctes, wheres, notes = [], [], []

    # --- the pivot -------------------------------------------------------
    if feats:
        cols = ",\n           ".join(
            f"max(CASE WHEN feature = {_q(f)} THEN value END) AS {_alias(f)}"
            for f in feats)
        ctes.append(f"""feat AS (
    SELECT cell_id,
           {cols}
    FROM read_parquet({_q(str(fpath))})
    WHERE feature IN ({", ".join(_q(f) for f in feats)})
    GROUP BY cell_id
)""")
    else:
        ctes.append(f"""feat AS (
    SELECT DISTINCT cell_id FROM read_parquet({_q(str(fpath))})
)""")

    base = "feat"

    # --- fabric, for terrane-scoped percentiles and geometry -------------
    cells_path = HLP.cell_centroids(juris)
    ctes.append(f"""fabric AS (
    SELECT cell_id, terrane_id, area_km2, lat, lon
    FROM read_parquet({_q(str(cells_path))})
)""")
    base = "feat JOIN fabric USING (cell_id)"

    # --- predicates ------------------------------------------------------
    for p in scr.where:
        if p.kind == "feature":
            wheres.append(_feature_where(p, fpath, juris))
        elif p.kind == "heat":
            cte, w = _heat(p, juris)
            ctes.append(cte)
            base += " LEFT JOIN heat USING (cell_id)"
            wheres.append(w)
        elif p.kind == "tenure":
            cte, w, note = _tenure(p, juris)
            ctes.append(cte)
            base += " LEFT JOIN tenure USING (cell_id)"
            wheres.append(w)
            if note:
                notes.append(note)
        elif p.kind == "count":
            cte, w = _count(p, juris)
            ctes.append(cte)
            base += f" LEFT JOIN {_alias(p.name)}_cnt USING (cell_id)"
            wheres.append(w)
        elif p.kind == "dist_to":
            wheres.append(_dist_to(p))
        elif p.kind == "no_barren":
            cte, w, note = _no_barren(p)
            ctes.append(cte)
            base += " LEFT JOIN barren USING (cell_id)"
            wheres.append(w)
            notes.append(note)
        elif p.kind == "within":
            cte, w = _within(p, juris)
            ctes.append(cte)
            base += " JOIN within_geom USING (cell_id)"
            wheres.append(w)
        else:
            raise ScreenCompileError(f"cannot compile predicate kind {p.kind!r}")

    select_cols = ["cell_id"] + [_alias(f) for f in feats]
    if any(p.kind == "heat" for p in scr.where):
        select_cols.append("heat")
    order = f"\nORDER BY {scr.rank_by} DESC" if scr.rank_by else ""
    limit = f"\nLIMIT {scr.limit}" if scr.limit else ""

    sql = ("WITH " + ",\n".join(ctes) + "\n"
           + f"SELECT {', '.join(select_cols)}\n"
           + f"FROM {base}\n"
           + ("WHERE " + "\n  AND ".join(f"({w})" for w in wheres) if wheres else "")
           + order + limit)

    return {"sql": sql, "features_used": feats, "fabric_version": fv,
            "snapshot": sn, "feature_path": str(fpath), "juris": juris,
            "notes": notes}


_DERIVED = {"heat", "cell_id"}


def _identifiers(expr: str) -> list:
    import re
    return [t for t in re.findall(r"[A-Za-z_][\w.]*", expr)
            if t not in ("and", "or", "not", "desc", "asc")]


def _alias(name: str) -> str:
    return name.replace(".", "_").replace("-", "_")


def _feature_where(p: Predicate, fpath: Path, juris: str) -> str:
    col = _alias(p.name)
    if isinstance(p.value, Percentile):
        if p.value.scope == "terrane":
            # Percentile within the cell's own terrane, matching C2.3.
            return (f"{col} {p.op} (SELECT quantile_cont({col}, "
                    f"{p.value.p/100}) FROM feat JOIN fabric USING (cell_id) f2 "
                    f"WHERE f2.terrane_id = fabric.terrane_id)")
        return (f"{col} {p.op} (SELECT quantile_cont({col}, {p.value.p/100}) "
                f"FROM feat)")
    return f"{col} {p.op} {_q(p.value)}"


def _heat(p: Predicate, juris: str):
    hp = C.PROCESSED_DIR / "heat.parquet"
    cte = f"""heat AS (
    SELECT cell_r7 AS cell_id, max(heat_cross_smoothed) AS heat
    FROM read_parquet({_q(str(hp))})
    WHERE juris = {_q(juris)}
    GROUP BY cell_r7
)"""
    if isinstance(p.value, Percentile):
        w = (f"heat {p.op} (SELECT quantile_cont(heat, {p.value.p/100}) "
             f"FROM heat)")
    else:
        w = f"heat {p.op} {_q(p.value)}"
    return cte, w


def _tenure(p: Predicate, juris: str):
    """Tenure is r9; the fabric is r7. The join is the r9 cell's `parent`.

    A screen that says `tenure == open` over an r7 cell means "some of this hex
    is open", not "all of it" — an r7 hex is 5.2 km² and will almost never be
    entirely unclaimed. `open_cells` is carried through so a caller can rank on
    how much of the hex is actually available."""
    d = C.PROCESSED_DIR / "land_state"
    parts = sorted(d.glob(f"{juris}__*.parquet")) if d.exists() else []
    if not parts:
        raise ScreenCompileError(
            f"no land_state for {juris} — run land/open_ground.py, or drop the "
            f"tenure predicate")
    src = " UNION ALL ".join(f"SELECT parent, state FROM read_parquet({_q(str(x))})"
                             for x in parts)
    cte = f"""tenure AS (
    SELECT parent AS cell_id,
           count(*) FILTER (WHERE state = 'open') AS open_cells,
           count(*) AS total_cells
    FROM ({src})
    GROUP BY parent
)"""
    note = (f"tenure resolved from {len(parts)} land_state AOI file(s); cells "
            f"outside a computed AOI have no tenure row and are excluded by "
            f"this predicate")
    if p.name == "open":
        return cte, "open_cells > 0", note
    if p.name == "claimed":
        return cte, "open_cells = 0 AND total_cells > 0", note
    if p.name == "expiring":
        lw = C.PROCESSED_DIR / "lapse_watch.parquet"
        if not lw.exists():
            raise ScreenCompileError(
                "tenure == expiring(...) needs lapse_watch.parquet — run "
                "land/lapse_watch.py --scan ON")
        days = p.args.get("days", 30)
        cte = f"""tenure AS (
    SELECT DISTINCT cell_r7 AS cell_id, 1 AS open_cells, 1 AS total_cells
    FROM read_parquet({_q(str(lw))})
    WHERE days_to_expiry <= {int(days)}
)"""
        return cte, "open_cells IS NOT NULL", (
            f"expiring({days}) resolved from lapse_watch.parquet, which is a "
            f"WATCH list — status is watch_only, never 'stakeable'")
    raise ScreenCompileError(f"unknown tenure state {p.name!r}")


def _count(p: Predicate, juris: str):
    spec = POINT_CLASSES.get(p.name)
    if spec is None:
        raise ScreenCompileError(
            f"unknown point class {p.name!r}; have {sorted(POINT_CLASSES)}")
    radius = p.args.get("radius_km") or 0.0
    alias = f"{_alias(p.name)}_cnt"
    col = f"{alias}_n"
    try:
        path = (HLP.points_with_radius(p.name, spec, radius, juris)
                if radius else HLP.points_on_fabric(p.name, spec, juris))
    except FileNotFoundError as e:
        raise ScreenCompileError(f"{p.name}: {e} — {spec.get('note','')}")
    cte = f"""{alias} AS (
    SELECT cell_id, n AS {col} FROM read_parquet({_q(str(path))})
)"""
    return cte, f"coalesce({col}, 0) {p.op} {p.value}"


def _dist_to(p: Predicate) -> str:
    """`dist_to(fault) < 5` reads the precomputed `fault__km` feature.

    Distance is expensive and C0.5's `gridify.dist_to` already computed it onto
    the fabric. Recomputing it inside a screen would be slower and could
    disagree with the model that trained on the stored version."""
    col = _alias(f"{p.name}__km")
    return f"{col} {p.op} {p.value}"


def _no_barren(p: Predicate):
    """The negatives-aware predicate, and it is commodity-CONDITIONAL.

    Master §2's doctrine: a hole barren for gold does not test a lithium thesis.
    So `no_barren(commodity=Au)` excludes cells with a barren hole tested FOR
    gold, and says nothing about cells barren for anything else."""
    commodity = p.args.get("commodity")
    min_depth = p.args.get("min_depth_m")
    try:
        path = HLP.barren_cells(commodity, min_depth)
    except FileNotFoundError as e:
        raise ScreenCompileError(str(e))
    cte = f"""barren AS (
    SELECT cell_id, 1 AS is_barren FROM read_parquet({_q(str(path))})
)"""
    note = (f"no_barren is commodity-conditional (Master §2): this excludes "
            f"cells holding a barren hole tested for "
            f"{commodity or 'ANY commodity'}"
            + (f" to at least {min_depth} m" if min_depth else "")
            + ". A hole barren for one metal does not test another.")
    return cte, "is_barren IS NULL", note


def _within(p: Predicate, juris: str):
    """Cells within `buffer_km` of a named ownership block.

    Resolved in Python at compile time rather than as a spatial join: the
    blocks store is a separate DuckDB file in EPSG:3978 (audit M1), and
    attaching it inside the screen SQL would make the emitted SQL depend on an
    ATTACH that a reader replaying it later would not have."""
    import duckdb
    import h3

    db = C.PROCESSED_DIR / "ownership.duckdb"
    if not db.exists():
        raise ScreenCompileError("ownership.duckdb missing for within()")
    km = p.args.get("buffer_km", 0.0)
    con = duckdb.connect(str(db), read_only=True)
    try:
        con.execute("LOAD spatial")
    except Exception:                                           # noqa: BLE001
        con.execute("INSTALL spatial; LOAD spatial")
    try:
        row = con.execute(
            "SELECT ST_AsText(ST_Transform(ST_Centroid(ST_GeomFromText("
            "geometry_wkt)), 'EPSG:3978','EPSG:4326', always_xy := true)) "
            "FROM blocks WHERE block_id = ?", [p.name]).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        raise ScreenCompileError(f"no block {p.name!r} in ownership.duckdb")
    import re as _re
    m = _re.match(r"POINT \(([-\d.]+) ([-\d.]+)\)", row[0])
    if not m:
        raise ScreenCompileError(f"could not read centroid for {p.name!r}")
    lng, lat = float(m.group(1)), float(m.group(2))
    anchor = h3.latlng_to_cell(lat, lng, 7)
    k = max(0, round(km / 1.4))
    cells = sorted({anchor} | set(h3.grid_disk(anchor, k)))
    cte = ("within_geom AS (\n    SELECT * FROM (VALUES "
           + ", ".join(f"({_q(c)})" for c in cells)
           + ") AS t(cell_id)\n)")
    return cte, "1=1"
