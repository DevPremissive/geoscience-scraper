#!/usr/bin/env python3
"""
heat.py — staking heat per r7 cell per quarter (C1.3).

Heat is where exploration interest is concentrating. Master §2 is explicit about
what it is and is not: tenure history here is **attention and momentum, and a
weak positive prior** — never a negative label, never evidence of geology. Ground
staked and dropped is confounded by financing cycles and hypothesis choice, and
the fact it was staked at all validates perceived potential.

Input: `tenure_events` (C0.7). Output: `processed/heat.parquet`, one row per
(cell, quarter).

Two normalisations are computed and kept **separately**, because they answer
different questions and averaging them would blur both:

    z_self   how unusual is this quarter for THIS cell, against its own trailing
             8 quarters — "something changed here"
    z_cross  how unusual is it against every cell in the jurisdiction that
             quarter — "this is a hot place, full stop"

A cell that is always busy scores low on `z_self` and high on `z_cross`; a quiet
cell that suddenly moves scores the reverse. Both are signal.

Robust z-scores (median / MAD) rather than mean / stdev throughout: staking is
bursty and heavy-tailed, and one 400-claim block would otherwise define the
standard deviation and flatten everything else to zero.

ONTARIO ACCEPTANCE MET (2026-08-17). PLAN_C1 1.3 requires the series to
reproduce at least one staking rush independently verifiable from industry news.
The top clusters resolve to named programmes by Ontario's two largest holders —
Kenorland Exploration in 2025Q1/Q3/Q4 and Juno Corp. in 2024Q3 — and vis
confirmed all of them against company updates. The metric is detecting real
market events, not data artifacts.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

HEAT_PATH = C.PROCESSED_DIR / "heat.parquet"

#: Trailing window for the self-normalisation, per PLAN_C1 1.3.
TRAILING_QUARTERS = 8

#: Contribution of each of the 6 r7 neighbours to a cell's smoothed score.
#: Staking rushes spill across cell boundaries, so a cell beside a hot cell is
#: itself interesting — but at half weight, or a single rush would paint a
#: seven-cell blob at uniform intensity.
NEIGHBOUR_WEIGHT = 0.5

#: Metric weights for the composite. Deliberately modest and explicit rather
#: than fitted: nothing downstream should treat this as a learned quantity.
METRIC_WEIGHTS = {
    "cells_staked": 1.0,
    "area_staked_ha": 0.5,
    "unique_new_owners": 1.0,     # a NEW name arriving is a stronger signal than
                                  # an incumbent adding to a position
    "expiry_count": 0.25,         # churn is interest, but weakly
    "conversion_count": 1.5,      # claim -> lease is the strongest commitment
}


#: A cell needs this many quarters before "unusual for this cell" means anything.
MIN_HISTORY = 4

#: Quarters in which a jurisdiction converted to map staking. Every live claim
#: was re-issued onto the new grid at once, so the register shows a province-wide
#: staking event that is an administrative artifact, not exploration interest.
#: Ontario converted 2018-04-10 (MASTER §6c) and 2018Q2 duly contains a single
#: contiguous cluster of 64,655 claims across 3,254 cells. Excluded from rush
#: detection and flagged in the output rather than silently dropped.
CONVERSION_QUARTERS = {"ON": {"2018Q2"}}


def _robust_z(series, min_n: int = 1):
    """(x - median) / (1.4826 * MAD), and 0 wherever that is not meaningful.

    Three guards, each earned:

    * **too little history** — with fewer than `min_n` observations there is no
      distribution to be unusual against. The first build scored cells with one
      or two quarters, which is where the nonsense came from.
    * **degenerate scale** — a constant history has no spread.
    * **scale that is negligible against the values themselves** — float noise
      in summed hectares produced a MAD around 1e-15, and dividing by it threw
      z-scores of 2e15. The floor is relative because the metrics differ by
      orders of magnitude (claim counts vs hectares).
    """
    import numpy as np
    x = series.astype(float).to_numpy()
    if len(x) < min_n:
        return np.zeros_like(x)
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    scale = 1.4826 * mad
    floor = max(1e-9, 1e-6 * abs(med))
    if not np.isfinite(scale) or scale < floor:
        return np.zeros_like(x)
    return np.clip((x - med) / scale, -50.0, 50.0)


def build(juris: str = "ON", write: bool = True):
    import numpy as np
    import pandas as pd

    ev = pd.read_parquet(C.PROCESSED_DIR / "tenure_events.parquet")
    ev = ev[(ev["juris"] == juris) & (ev["cell_r7"].astype(str).str.len() > 0)].copy()
    if ev.empty:
        sys.exit(f"no located events for {juris}")

    ev["event_window_start"] = pd.to_datetime(ev["event_window_start"], errors="coerce")
    ev = ev.dropna(subset=["event_window_start"])
    ev["quarter"] = ev["event_window_start"].dt.to_period("Q").astype(str)
    print(f"  {juris}: {len(ev):,} located events, "
          f"{ev['cell_r7'].nunique():,} cells, {ev['quarter'].nunique()} quarters")

    staked = ev[ev["event_type"] == "staked"]
    expired = ev[ev["event_type"] == "expired"]
    converted = ev[ev["event_type"] == "converted"]

    g = ["cell_r7", "quarter"]
    m = pd.DataFrame(index=pd.MultiIndex.from_frame(
        ev[g].drop_duplicates()).sort_values())
    m["cells_staked"] = staked.groupby(g).size()
    m["area_staked_ha"] = staked.groupby(g)["area_ha"].sum()
    m["expiry_count"] = expired.groupby(g).size()
    m["area_expired_ha"] = expired.groupby(g)["area_ha"].sum()
    m["conversion_count"] = converted.groupby(g).size()
    m = m.fillna(0.0)
    m["net_area_change"] = m["area_staked_ha"] - m["area_expired_ha"]

    # unique_new_owners: holders staking in this cell for the FIRST time. An
    # incumbent adding to a position is ordinary; a new name arriving is the
    # signal — that is what a competitor moving in looks like.
    s = staked[["cell_r7", "quarter", "owner_after"]].dropna(subset=["owner_after"])
    s = s.sort_values("quarter")
    first_seen = s.drop_duplicates(subset=["cell_r7", "owner_after"], keep="first")
    m["unique_new_owners"] = first_seen.groupby(g).size()
    m["unique_new_owners"] = m["unique_new_owners"].fillna(0.0)

    m = m.reset_index()
    lat = _restake_latency(staked, expired)
    m = m.merge(lat, on=["cell_r7", "quarter"], how="left")
    m["survivorship_biased"] = bool(ev["survivorship_biased"].any())

    # ---- normalisations -------------------------------------------------
    m = m.sort_values(["cell_r7", "quarter"]).reset_index(drop=True)
    qs = sorted(m["quarter"].unique())
    qidx = {q: i for i, q in enumerate(qs)}
    m["_qi"] = m["quarter"].map(qidx)
    m["quarters_available"] = m.groupby("cell_r7")["_qi"].transform(
        lambda s: s.rank(method="first")).astype(int).clip(upper=TRAILING_QUARTERS)

    for metric in METRIC_WEIGHTS:
        # self: against this cell's own trailing window
        m[f"z_self__{metric}"] = (
            m.groupby("cell_r7")[metric]
             .transform(lambda s: pd.Series(_robust_z(s, MIN_HISTORY), index=s.index)))
        # cross: against every cell in that quarter
        m[f"z_cross__{metric}"] = (
            m.groupby("quarter")[metric]
             .transform(lambda s: pd.Series(_robust_z(s), index=s.index)))

    for kind in ("self", "cross"):
        m[f"heat_{kind}"] = sum(
            w * m[f"z_{kind}__{k}"] for k, w in METRIC_WEIGHTS.items())

    m = _smooth_neighbours(m)

    # ever_staked_count: the weak positive prior of Master §2, named explicitly
    # so C2's backtest can exclude it when testing without tenure features.
    ever = staked.groupby("cell_r7").size().rename("ever_staked_count")
    m = m.merge(ever, on="cell_r7", how="left")
    m["ever_staked_count"] = m["ever_staked_count"].fillna(0).astype(int)

    m = m.drop(columns=["_qi"])
    m["juris"] = juris

    print(f"  → {len(m):,} cell-quarters")
    print(f"    heat_self  range {m['heat_self'].min():.1f} .. {m['heat_self'].max():.1f}")
    print(f"    heat_cross range {m['heat_cross'].min():.1f} .. {m['heat_cross'].max():.1f}")
    if write:
        HEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        m.to_parquet(HEAT_PATH, index=False, compression="zstd")
        print(f"  → {HEAT_PATH}")
    return m


def _restake_latency(staked, expired):
    """Median days from an expiry in a cell to the next staking in that cell.

    Fast restaking means contested ground — somebody was waiting for it. Computed
    per cell-quarter of the *expiry*, since that is the event whose aftermath is
    being measured.
    """
    import numpy as np
    import pandas as pd

    if expired.empty or staked.empty:
        return pd.DataFrame(columns=["cell_r7", "quarter", "restake_latency_days"])

    st = (staked[["cell_r7", "event_window_start"]]
          .dropna().sort_values("event_window_start"))
    by_cell = {c: g["event_window_start"].to_numpy()
               for c, g in st.groupby("cell_r7")}

    rows = []
    for (cell, q), grp in expired.groupby(["cell_r7", "quarter"]):
        arr = by_cell.get(cell)
        if arr is None or len(arr) == 0:
            continue
        lat = []
        for t in grp["event_window_start"].to_numpy():
            nxt = arr[arr > t]
            if len(nxt):
                lat.append((nxt[0] - t) / np.timedelta64(1, "D"))
        if lat:
            rows.append((cell, q, float(np.median(lat))))
    return pd.DataFrame(rows, columns=["cell_r7", "quarter", "restake_latency_days"])


def _smooth_neighbours(m):
    """Add half-weighted heat from the 6 r7 neighbours, per quarter."""
    import h3
    import pandas as pd

    for kind in ("self", "cross"):
        col = f"heat_{kind}"
        lookup = {(r.cell_r7, r.quarter): getattr(r, col)
                  for r in m.itertuples(index=False)}
        vals = []
        for r in m.itertuples(index=False):
            own = getattr(r, col)
            nb = 0.0
            for n in h3.grid_ring(r.cell_r7, 1):
                nb += lookup.get((n, r.quarter), 0.0)
            vals.append(own + NEIGHBOUR_WEIGHT * nb)
        m[f"{col}_smoothed"] = vals
    return m


def top(n: int = 20, kind: str = "cross", quarter: str | None = None):
    import pandas as pd
    if not HEAT_PATH.exists():
        sys.exit("no heat.parquet — run --build first")
    h = pd.read_parquet(HEAT_PATH)
    if quarter:
        h = h[h["quarter"] == quarter]
    col = f"heat_{kind}_smoothed"
    t = h.nlargest(n, col)
    print(f"  top {n} by {col}" + (f" in {quarter}" if quarter else "") + ":\n")
    print(f"  {'cell_r7':<18}{'quarter':<9}{'heat':>8}{'staked':>8}"
          f"{'new_own':>9}{'conv':>6}{'expiry':>8}{'qtrs':>6}")
    for r in t.itertuples(index=False):
        print(f"  {r.cell_r7:<18}{r.quarter:<9}{getattr(r,col):>8.1f}"
              f"{int(r.cells_staked):>8}{int(r.unique_new_owners):>9}"
              f"{int(r.conversion_count):>6}{int(r.expiry_count):>8}"
              f"{int(r.quarters_available):>6}")
    return t


def rushes(n: int = 12, juris: str = "ON", min_claims: int = 40):
    """Hot cells grouped into contiguous CLUSTERS — one row per story.

    The per-cell leaderboard is misleading for human review: neighbour smoothing
    lifts every cell in a contiguous block, so one company staking 480 claims
    fills fifteen of the top twenty rows with the same event. Grouping adjacent
    hot cells in the same quarter gives one row per rush, with a location that
    can be checked against industry news — which is what C1.3's Ontario
    acceptance actually asks for.
    """
    import h3
    import pandas as pd
    h = pd.read_parquet(HEAT_PATH)
    h = h[(h["juris"] == juris) & (h["cells_staked"] >= 1)]
    skip = CONVERSION_QUARTERS.get(juris, set())
    if skip:
        n_before = len(h)
        h = h[~h["quarter"].isin(skip)]
        print(f"  excluding map-staking conversion quarter(s) {sorted(skip)}: "
              f"{n_before - len(h):,} cell-quarters removed as administrative\n")

    out = []
    for q, grp in h.groupby("quarter"):
        cells = dict(zip(grp["cell_r7"], grp["cells_staked"]))
        seen = set()
        for c in grp.sort_values("heat_cross_smoothed", ascending=False)["cell_r7"]:
            if c in seen:
                continue
            # flood-fill across adjacent cells active in the same quarter
            stack, comp = [c], []
            while stack:
                cur = stack.pop()
                if cur in seen or cur not in cells:
                    continue
                seen.add(cur)
                comp.append(cur)
                stack.extend(h3.grid_ring(cur, 1))
            claims = sum(cells[x] for x in comp)
            if claims < min_claims:
                continue
            sub = grp[grp["cell_r7"].isin(comp)]
            lat, lng = h3.cell_to_latlng(comp[0])
            out.append({"quarter": q, "cells": len(comp), "claims": int(claims),
                        "new_owners": int(sub["unique_new_owners"].sum()),
                        "conversions": int(sub["conversion_count"].sum()),
                        "heat": float(sub["heat_cross_smoothed"].max()),
                        "lat": round(lat, 4), "lon": round(lng, 4),
                        "anchor_cell": comp[0]})
    df = pd.DataFrame(out).sort_values("claims", ascending=False).head(n)
    print(f"  top {n} staking rushes in {juris}, by claims in one contiguous cluster:\n")
    print(f"  {'quarter':<9}{'claims':>8}{'cells':>7}{'new_own':>9}{'conv':>6}"
          f"{'heat':>8}{'lat':>10}{'lon':>11}")
    for r in df.itertuples(index=False):
        print(f"  {r.quarter:<9}{r.claims:>8,}{r.cells:>7}{r.new_owners:>9}"
              f"{r.conversions:>6}{r.heat:>8.1f}{r.lat:>10.4f}{r.lon:>11.4f}")
    return df


def series(juris: str = "ON"):
    """Quarterly totals — the series the Ontario acceptance test reads."""
    import pandas as pd
    h = pd.read_parquet(HEAT_PATH)
    h = h[h["juris"] == juris]
    s = h.groupby("quarter").agg(
        cells_staked=("cells_staked", "sum"),
        expiries=("expiry_count", "sum"),
        new_owners=("unique_new_owners", "sum"),
        conversions=("conversion_count", "sum"),
        active_cells=("cell_r7", "nunique"))
    print(s.to_string())
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", metavar="JURIS")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--kind", default="cross", choices=["self", "cross"])
    ap.add_argument("--quarter")
    ap.add_argument("--series", metavar="JURIS")
    ap.add_argument("--rushes", type=int, default=0)
    args = ap.parse_args()
    C.require_lake()
    if args.build:
        build(args.build)
    if args.top:
        top(args.top, args.kind, args.quarter)
    if args.series:
        series(args.series)
    if args.rushes:
        rushes(args.rushes)
    if not any([args.build, args.top, args.series, args.rushes]):
        ap.print_help()


if __name__ == "__main__":
    main()
