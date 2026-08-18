#!/usr/bin/env python3
"""
tenure_events.py — the tenure event stream (C0.7).

This is the primary business signal: C1 heat and C1.6 lapse watch both read it,
so it is built here and hardened rather than left as a C1 afterthought.

Events come from three kinds of source, and the schema hides the difference from
consumers on purpose:

  **Cancellation registers** (Ontario). `Cancelled_Claim_Polygons` retains
  431,584 records with issue and termination dates from 2018-04, so staked and
  dropped events are read directly. Not survivorship-biased.

  **Historical registers** (Yukon). `YT_HISTORICAL_CLAIMS` holds 244,703 expired
  tenures with owner and staking dates back to 1899 — Yukon never converted to
  map staking, so its register was never truncated.

  **Snapshot diffs** (everywhere else). BC, NU, NT, NS and NB publish current
  holdings only, so their history accrues forward from consecutive daily
  snapshots. C3.1 is what makes that accumulate.

Every row carries `survivorship_biased`, because a series that omits dropped
ground is descriptive context and never a training label or backtest ground
truth (MASTER §8).

Usage:
    python src/tenure_events.py --build-on        # Ontario cancellation register
    python src/tenure_events.py --build-yt        # Yukon historical register
    python src/tenure_events.py --diff ON_CLAIMS2 2026-06-12 2026-06-13
    python src/tenure_events.py --incremental     # called by run_class.py
    python src/tenure_events.py --summary
"""
from __future__ import annotations
import argparse, hashlib, re, sys
import datetime as dt
from pathlib import Path

import config as C

EVENTS_PATH = C.PROCESSED_DIR / "tenure_events.parquet"

EVENT_COLUMNS = [
    "event_id", "juris", "source_code", "claim_id", "event_type",
    "event_window_start", "event_window_end", "owner_before", "owner_after",
    "area_ha", "cell_r7", "snapshot_prev", "snapshot_next", "id_confidence",
    "survivorship_biased",
]


def cells_r7(gdf):
    """H3 r7 index per feature, from its centroid. Empty string where unknown.

    Computed here rather than in C1.3 so every consumer of the event stream
    shares one spatial key, and computed from H3 directly rather than by joining
    the fabric — an event that falls outside a built fabric is still a located
    event, and heat must not silently drop it.
    """
    import h3
    import numpy as np
    # Centroids in a projected CRS: a centroid taken in degrees is not the
    # centre of the polygon on the ground.
    cent = gdf.to_crs("EPSG:3978").geometry.centroid.to_crs(4326)
    out = []
    for pt in cent:
        if pt is None or pt.is_empty or np.isnan(pt.x) or np.isnan(pt.y):
            out.append("")
        else:
            out.append(h3.latlng_to_cell(pt.y, pt.x, 7))
    return out

#: Ontario cancellation STATUS values that are genuine abandonment. The rest are
#: administrative reorganisations and would read as false drops in C1.3 heat and
#: C2.6 labels. Verified on the 2026-08-14 bundle — note the audit's list of five
#: values is incomplete; the register also carries "Hold Special Circumstances
#: Apply" (345) and "Hold" (4).
ON_DROP_STATUSES = {"Cancelled"}
ON_ADMIN_STATUSES = {"Amalgamated", "Merged", "Leased", "Active",
                     "Hold", "Hold Special Circumstances Apply"}

#: `(49) STILLWATER CRITICAL MINERALS CORP., (51) Heritage Mining Ltd.`
#: Operational claims: the leading number IS the ownership percentage.
#:
#: Holders are split on a comma FOLLOWED BY a parenthesised number, not on any
#: comma. Splitting on commas dropped every holder whose own name contains one —
#: "(100) VCC RESOURCES, INC." and "(100) SMITH, JOHN" both fell through
#: unparsed and kept their percentage prefix as part of the name, which then
#: propagated into the ownership graph as a distinct entity.
_HOLDER_SPLIT = re.compile(r",\s*(?=\(\d)")
_PCT_PREFIX = re.compile(r"^\((\d{1,3}(?:\.\d+)?)\)\s*(.+?)\s*$")
#: `(408864) JEAN MARC GAUDREAU (100%)`
#: Cancelled claims: the leading number is a CLIENT ID and the percentage is a
#: trailing `(N%)`. Parsing this with the rule above would read client 408864 as
#: a 408,864% ownership share.
_ID_AND_PCT = re.compile(r"\((\d+)\)\s*(.+?)\s*\((\d+(?:\.\d+)?)%\)")


def parse_holder(value) -> list:
    """Split a HOLDER string into [(owner_name, percent, client_id), ...].

    Handles both MLAS conventions. The trailing-percent form is tried first
    because its `%` marker is unambiguous; only if it does not match is the
    leading number treated as a percentage.
    """
    if not value or not isinstance(value, str):
        return []
    hits = _ID_AND_PCT.findall(value)
    if hits:
        return [(name.strip(), float(pct), cid) for cid, name, pct in hits]
    parts = _HOLDER_SPLIT.split(value)
    out, matched = [], False
    for part in parts:
        m = _PCT_PREFIX.match(part.strip())
        if m:
            matched = True
            out.append((m.group(2).strip(), float(m.group(1)), None))
        elif part.strip():
            out.append((part.strip(), None, None))
    if matched:
        return out
    return [(value.strip(), None, None)]


def holder_name(value) -> str | None:
    """Primary (largest-share) owner name, for owner_before/owner_after."""
    parts = parse_holder(value)
    if not parts:
        return None
    parts = sorted(parts, key=lambda p: (p[1] is not None, p[1] or 0), reverse=True)
    return parts[0][0] or None


def _event_id(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _empty():
    import pandas as pd
    return pd.DataFrame(columns=EVENT_COLUMNS)


# --------------------------------------------------------------------------
# Ontario — from the retained cancellation register
# --------------------------------------------------------------------------

def build_ontario():
    """Ontario staking and drop events (2018-04 →), from BOTH MLAS registers.

    **Both registers are required, and using only one inverts the signal.**
    `Cancelled_Claim_Polygons` holds claims that have ENDED, so taking staking
    events from it alone counts only ground that was later given up. Every claim
    staked recently and still alive is invisible: the first build of this
    reported ~2,000 claims staked across 2025, when `Operational_Cell_Claims`
    shows **89,466**. Ontario's biggest staking year read as its quietest.

    So: `staked` comes from both registers, `expired` only from the cancellation
    register (an active claim has not expired). Together they are unbiased —
    which is the property no other jurisdiction's data has.
    """
    import geopandas as gpd
    import pandas as pd

    frames = [_ontario_cancelled(), _ontario_active()]
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["event_id"])
    return df


def _ontario_active():
    """`staked` events for claims that are still live."""
    import geopandas as gpd
    import pandas as pd

    layer = "ON__ON_MLAS_TENURE__Operational_Cell_Claims"
    g = gpd.read_file(C.GPKG_PATH, layer=layer,
                      columns=["TENURE_NUM", "ISSUE_DATE", "HOLDER"])
    g["ISSUE_DATE"] = pd.to_datetime(g["ISSUE_DATE"], errors="coerce")
    g = g[g["ISSUE_DATE"].notna()]
    print(f"  {layer}: {len(g):,} active claims")

    area_ha = g.to_crs("EPSG:6933").area / 10_000.0
    owner = g["HOLDER"].map(holder_name)
    cell = cells_r7(g)
    rows = []
    for pos, idx in enumerate(g.index):
        d = g.at[idx, "ISSUE_DATE"]
        tn = g.at[idx, "TENURE_NUM"]
        rows.append((_event_id("ON", tn, "staked", d), "ON", "ON_MLAS_TENURE", tn,
                     "staked", d, d, None, owner.at[idx], float(area_ha.at[idx]),
                     cell[pos], None, None, "exact", False))
    print(f"  staked {len(rows):,} (from active claims)")
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def _ontario_cancelled():
    """`staked`, `expired` and `converted` events for claims that have ended."""
    import geopandas as gpd
    import pandas as pd

    layer = "ON__ON_MLAS_TENURE__Cancelled_Claim_Polygons"
    cols = ["TENURE_NUM", "STATUS", "ISSUE_DATE", "TERMINATIO", "HOLDER"]
    g = gpd.read_file(C.GPKG_PATH, layer=layer, columns=cols)
    print(f"  {layer}: {len(g):,} records")

    unknown = set(g["STATUS"].dropna().unique()) - ON_DROP_STATUSES - ON_ADMIN_STATUSES
    if unknown:
        print(f"  ! unrecognised STATUS values, treated as non-drops: {sorted(unknown)}")

    area_ha = g.to_crs("EPSG:6933").area / 10_000.0
    owner = g["HOLDER"].map(holder_name)
    cell = cells_r7(g)
    rows = []

    # staked — every record's issue date, regardless of how it ended
    issued = g["ISSUE_DATE"].notna()
    for idx in g.index[issued]:
        d = g.at[idx, "ISSUE_DATE"]
        tn = g.at[idx, "TENURE_NUM"]
        rows.append((_event_id("ON", tn, "staked", d), "ON", "ON_MLAS_TENURE", tn,
                     "staked", d, d, None, owner.at[idx], float(area_ha.at[idx]),
                     cell[g.index.get_loc(idx)], None, None, "exact", False))

    # expired — only genuine abandonment
    drop = g["TERMINATIO"].notna() & g["STATUS"].isin(ON_DROP_STATUSES)
    for idx in g.index[drop]:
        d = g.at[idx, "TERMINATIO"]
        tn = g.at[idx, "TENURE_NUM"]
        rows.append((_event_id("ON", tn, "expired", d), "ON", "ON_MLAS_TENURE", tn,
                     "expired", d, d, owner.at[idx], None, float(area_ha.at[idx]),
                     cell[g.index.get_loc(idx)], None, None, "exact", False))

    # converted — administrative reorganisation, kept but NOT a drop
    conv = g["TERMINATIO"].notna() & g["STATUS"].isin(ON_ADMIN_STATUSES)
    for idx in g.index[conv]:
        d = g.at[idx, "TERMINATIO"]
        tn = g.at[idx, "TENURE_NUM"]
        rows.append((_event_id("ON", tn, "converted", d), "ON", "ON_MLAS_TENURE", tn,
                     "converted", d, d, owner.at[idx], None, float(area_ha.at[idx]),
                     cell[g.index.get_loc(idx)], None, None, "exact", False))

    df = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    print(f"  staked {int(issued.sum()):,} · expired {int(drop.sum()):,} "
          f"(genuine drops) · converted {int(conv.sum()):,} (administrative)")
    return df


# --------------------------------------------------------------------------
# Yukon — from the historical register
# --------------------------------------------------------------------------

def build_yukon():
    """Staked/expired events from YT_HISTORICAL_CLAIMS.

    Dates are epoch MILLISECONDS in float columns; naive parsing silently
    collapses the whole series to 1970 (audit G3).
    """
    import geopandas as gpd
    import pandas as pd

    layer = "YT__YT_HISTORICAL_CLAIMS"
    cols = ["TENURE_HISTORICAL_ID", "CLAIM_NAME", "OWNER_NAME",
            "STAKING_DATE", "EXPIRY_DATE", "TENURE_STATUS"]
    g = gpd.read_file(C.GPKG_PATH, layer=layer, columns=cols)
    print(f"  {layer}: {len(g):,} records")

    staked = pd.to_datetime(g["STAKING_DATE"], unit="ms", errors="coerce")
    expiry = pd.to_datetime(g["EXPIRY_DATE"], unit="ms", errors="coerce")
    # TENURE_STATUS is not clean: alongside the documented values it carries one
    # 'EXPIRED' case-variant and one 'P'. Normalise before splitting.
    status = g["TENURE_STATUS"].astype(str).str.strip().str.upper()
    status = status.replace({"P": "PENDING"})

    area_ha = g.to_crs("EPSG:6933").area / 10_000.0
    owner = g["OWNER_NAME"]
    cell = cells_r7(g)
    rows = []

    for idx in g.index[staked.notna()]:
        d = staked.at[idx]
        cid = g.at[idx, "TENURE_HISTORICAL_ID"]
        rows.append((_event_id("YT", cid, "staked", d), "YT", "YT_HISTORICAL_CLAIMS",
                     cid, "staked", d, d, None, owner.at[idx],
                     float(area_ha.at[idx]), cell[g.index.get_loc(idx)],
                     None, None, "exact", False))

    gone = expiry.notna() & status.isin(["EXPIRED", "LAPSED"])
    for idx in g.index[gone]:
        d = expiry.at[idx]
        cid = g.at[idx, "TENURE_HISTORICAL_ID"]
        rows.append((_event_id("YT", cid, "expired", d), "YT", "YT_HISTORICAL_CLAIMS",
                     cid, "expired", d, d, owner.at[idx], None,
                     float(area_ha.at[idx]), cell[g.index.get_loc(idx)],
                     None, None, "exact", False))

    df = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    print(f"  staked {int(staked.notna().sum()):,} · expired/lapsed {int(gone.sum()):,}")
    print(f"  status values: {sorted(v for v in status.unique() if isinstance(v, str))}")
    return df


# --------------------------------------------------------------------------
# Snapshot diffing — for registries with no retained history
# --------------------------------------------------------------------------

def diff_snapshots(juris: str, code: str, prev_date: str, next_date: str,
                   id_field: str | None = None):
    """Diff two dated snapshots of one tenure source into events.

    Full-file re-issues are handled before diffing: if the two snapshots are
    byte-identical there is nothing to diff, and comparing them anyway would
    burn minutes to produce zero rows.
    """
    import geopandas as gpd
    import pandas as pd
    import process as P

    base = C.RAW_DIR / juris / code
    if not base.exists():
        sys.exit(f"no snapshots for {juris}/{code}")

    # Compare *effective states*, not raw directories. A snapshot directory is a
    # delta, so diffing 2026-06-12 (complete, 394 tiles) against 2026-06-13
    # (partial, 106 tiles) reported 12,098 alienations "expired" in one day —
    # entirely phantom, and phantom drops are the worst possible error here
    # because dropped ground is exactly the signal this system sells.
    def _load(as_of):
        import tempfile
        _eff, files = P.resolve_snapshot(base, as_of=as_of)
        files = {k: v for k, v in files.items()
                 if Path(k).name not in ("_source.json", P.RUN_MANIFEST)}
        if not files:
            return None
        with tempfile.TemporaryDirectory() as td:
            work = P.expand(files, Path(td) / "w")
            frames = []
            for ext in (".kmz", ".geojson", ".shp", ".gpkg"):
                for f in sorted(work.rglob(f"*{ext}")):
                    try:
                        frames.append(gpd.read_file(f))
                    except Exception:                           # noqa: BLE001
                        pass
                if frames:
                    break
        return pd.concat(frames, ignore_index=True) if frames else None

    a, b = _load(prev_date), _load(next_date)
    if a is None or b is None:
        sys.exit("could not read one of the snapshots")

    # Pick an identifier: a stable native id if one exists, else a geometry hash.
    # KMZ tiles have no usable id, so the fallback is flagged rather than hidden.
    # Native identifiers, most specific first. A stable id matters: with the
    # geometry-hash fallback a claim whose outline is nudged by a survey
    # correction reads as a drop plus a stake, which is a false lapse in exactly
    # the signal C1.6 watches.
    cand = id_field or next((c for c in ("TENURE_NUM", "TENURE_NUMBER_ID",
                                         "CLAIM_NUMBER", "Claim Number",
                                         "TENURE_HISTORICAL_ID", "TITLE_NUMBER",
                                         "GRANT_NUMBER", "LEGACY_CLAIM")
                             if c in a.columns and c in b.columns), None)
    if cand:
        a_ids = a[cand].astype(str)
        b_ids = b[cand].astype(str)
        conf = "exact"
    else:
        a_ids = a.geometry.to_wkb().map(lambda w: hashlib.sha256(w).hexdigest()[:16])
        b_ids = b.geometry.to_wkb().map(lambda w: hashlib.sha256(w).hexdigest()[:16])
        conf = "geometry_hash"

    sa, sb = set(a_ids), set(b_ids)
    gone, new = sa - sb, sb - sa
    print(f"  {code} {prev_date}→{next_date}: {len(sa):,} → {len(sb):,} "
          f"(+{len(new):,} / -{len(gone):,}) id={cand or 'geometry hash'}")

    a_cell = dict(zip(a_ids, cells_r7(a)))
    b_cell = dict(zip(b_ids, cells_r7(b)))
    rows = []
    for cid in sorted(new):
        rows.append((_event_id(juris, code, cid, "staked", next_date), juris, code,
                     cid, "staked", prev_date, next_date, None, None, None,
                     b_cell.get(cid, ""), prev_date, next_date, conf, False))
    for cid in sorted(gone):
        rows.append((_event_id(juris, code, cid, "expired", next_date), juris, code,
                     cid, "expired", prev_date, next_date, None, None, None,
                     a_cell.get(cid, ""), prev_date, next_date, conf, False))
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


# --------------------------------------------------------------------------

def write_events(frames, append: bool = False):
    import pandas as pd
    usable = [f for f in frames if f is not None and not f.empty]
    new = pd.concat(usable, ignore_index=True) if usable else _empty()
    if append and EVENTS_PATH.exists():
        old = pd.read_parquet(EVENTS_PATH)
        new = pd.concat([old, new], ignore_index=True)
    # Normalise across sources: Ontario's TENURE_NUM is a string and Yukon's
    # TENURE_HISTORICAL_ID an integer, and a mixed-type column cannot be written
    # to Parquet. The schema promises one comparable identifier per event.
    new["claim_id"] = new["claim_id"].astype("string")
    for c in ("event_window_start", "event_window_end"):
        new[c] = pd.to_datetime(new[c], errors="coerce")
    new["survivorship_biased"] = new["survivorship_biased"].astype(bool)
    new = new.drop_duplicates(subset=["event_id"]).sort_values(
        ["juris", "event_window_start", "event_id"], kind="mergesort")
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(EVENTS_PATH, index=False, compression="zstd")
    print(f"\n  → {EVENTS_PATH}  {len(new):,} events")
    return EVENTS_PATH


def summary():
    import pandas as pd
    if not EVENTS_PATH.exists():
        sys.exit("no tenure_events.parquet yet")
    df = pd.read_parquet(EVENTS_PATH)
    print(f"  {len(df):,} events")
    print(df.groupby(["juris", "event_type"]).size().to_string())
    d = pd.to_datetime(df["event_window_start"], errors="coerce")
    print(f"\n  window: {d.min()} .. {d.max()}")
    print(f"  survivorship_biased: {int(df['survivorship_biased'].sum()):,}")
    yr = d.dt.year
    print("\n  events per year (last 10):")
    print(yr.value_counts().sort_index().tail(10).to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-on", action="store_true")
    ap.add_argument("--build-yt", action="store_true")
    ap.add_argument("--diff", nargs=3, metavar=("CODE", "PREV", "NEXT"))
    ap.add_argument("--juris", default="ON")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--append", action="store_true")
    args = ap.parse_args()
    C.require_lake()

    frames = []
    if args.build_on or args.incremental:
        frames.append(build_ontario())
    if args.build_yt or args.incremental:
        frames.append(build_yukon())
    if args.diff:
        code, prev, nxt = args.diff
        frames.append(diff_snapshots(args.juris, code, prev, nxt))
    if frames:
        write_events(frames, append=args.append)
    if args.summary:
        summary()


if __name__ == "__main__":
    main()
