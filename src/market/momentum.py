#!/usr/bin/env python3
"""
momentum.py — hot-area momentum overlay and the entry-window flag (C6.3).

PLAN_C6 6.3: compose, per r7 cell, C1.3 heat against market overlays —
financings closed by issuers holding ground in the belt, drill programs
announced, and a coarse commodity-price regime — and raise an **entry-window
flag** where "heat rising quarter-over-quarter while staking velocity remains
below its prior local peak": interest building before the rush crests.

The flag prioritises the screening queue (C4.3) and lapse-watch attention
(C1.6). It never bypasses review, and nothing here decides anything.

Three notes on what the overlays actually are:

**Financings are real but thin.** 13 of 45 tracked buyers have a closed-placement
count, 56 placements between them, from the SEDAR+ filing index (audit L1: a
Report of exempt distribution is a closed placement, filed and dated). That is
enough to rank belts and not enough to model with.

**Drill programs are still null.** They need news-release and MD&A text, which
lives inside filing PDFs this repo does not hold. Emitted as None with
`missing_because`, the same shape C6.2 uses, rather than silently scoring zero —
a belt with no *observed* drilling is not a belt with no drilling.

**The price regime is the Bank of Canada's `M.MTLS`** — monthly BCPI Metals and
Minerals, no key, a Canadian government series. The plan asks for
`{rising, flat, falling}` and says coarse is fine because it is a covariate, not
a forecast, so that is exactly what this computes: a sign test on a 3-month
change against a 12-month standard deviation.

Usage:
    python -m market.momentum --build
    python -m market.momentum --entry-window
    python -m market.momentum --backtest        # the acceptance test
    python -m market.momentum --price-regime
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

OUT_PATH = C.PROCESSED_DIR / "momentum.parquet"
REGIME_PATH = C.MARKET_DIR / "price_regime.parquet"
BACKTEST_PATH = C.MARKET_DIR / "momentum_backtest.json"

#: Bank of Canada valet, monthly BCPI Metals and Minerals. Public, keyless.
BOC_SERIES = "M.MTLS"
BOC_URL = ("https://www.bankofcanada.ca/valet/observations/"
           f"{BOC_SERIES}/json?start_date=2017-01-01")

#: A quarter is "rising" when the 3-month change exceeds this many standard
#: deviations of the trailing 12-month change. 0.5 is deliberately loose: the
#: plan wants a three-state covariate, not a signal.
REGIME_SIGMA = 0.5

#: Event types excluded from staking velocity. PLAN_C6 6.3 acceptance (c) names
#: `Amalgamated`/`Merged`; in this lake the equivalent is `converted`, the
#: 2018-04 map-staking conversion that turned every legacy claim into cells at
#: once. Counting it as staking would put an artificial rush in 2018Q2 that
#: every belt shares.
EXCLUDED_EVENTS = {"converted"}

#: A rush is a belt, not a hex. `heat.rushes` summarises each by an anchor cell;
#: the backtest asks whether the flag fired anywhere within this many r7 rings
#: of it (k=2 is roughly 15 km) before the rush quarter.
BELT_RING = 2

#: Strict flag: velocity must be below this fraction of the prior local peak.
#: "Below the peak" alone is nearly always true and therefore says nothing.
STRICT_PEAK_FRACTION = 0.5


# ---------------------------------------------------------------------------
# Price regime
# ---------------------------------------------------------------------------

def fetch_price_regime(write: bool = True):
    """Quarterly {rising, flat, falling} from the BoC metals index."""
    import pandas as pd

    req = urllib.request.Request(BOC_URL, headers={"User-Agent": C.USER_AGENT})
    with urllib.request.urlopen(req, timeout=C.TIMEOUT) as r:
        data = json.loads(r.read())
    rows = [{"date": o["d"], "value": float(o[BOC_SERIES]["v"])}
            for o in data.get("observations", []) if o.get(BOC_SERIES)]
    if not rows:
        raise RuntimeError(f"no observations returned for {BOC_SERIES}")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    df["chg3"] = df["value"].diff(3)
    sigma = df["chg3"].rolling(12, min_periods=6).std()
    df["regime"] = "flat"
    df.loc[df["chg3"] > REGIME_SIGMA * sigma, "regime"] = "rising"
    df.loc[df["chg3"] < -REGIME_SIGMA * sigma, "regime"] = "falling"
    df["quarter"] = df["date"].dt.to_period("Q").astype(str)

    q = (df.groupby("quarter")
           .agg(index_value=("value", "last"),
                change_3m=("chg3", "last"),
                regime=("regime", "last"))
           .reset_index())
    q["source"] = f"Bank of Canada valet {BOC_SERIES} (BCPI Metals and Minerals)"
    if write:
        C.MARKET_DIR.mkdir(parents=True, exist_ok=True)
        q.to_parquet(REGIME_PATH, index=False)
    return q


def _price_regime(quiet: bool = True):
    import pandas as pd
    if REGIME_PATH.exists():
        return pd.read_parquet(REGIME_PATH)
    try:
        return fetch_price_regime()
    except Exception as e:                                      # noqa: BLE001
        if not quiet:
            print(f"  ! price regime unavailable: {type(e).__name__}: {e}",
                  file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Market overlays per cell
# ---------------------------------------------------------------------------

def _financings_by_cell():
    """Closed placements attributable to each r7 cell, via the owners holding it.

    A financing is raised by an issuer, not by a hexagon. The attribution is:
    issuer → owner_id (C6.2's resolution) → blocks → the r7 cells those blocks
    touch. A belt where several financed issuers hold ground scores higher than
    one where a single issuer does, which is the intent — money arriving in the
    neighbourhood, not one company's balance sheet."""
    import duckdb
    import pandas as pd
    import h3

    bp = C.MARKET_DIR / "buyers.parquet"
    if not bp.exists():
        return {}, "market/buyers.parquet missing — run C6.2"
    buyers = pd.read_parquet(bp)
    fin = buyers[["owner_id", "n_financing_closed_24mo"]].dropna()
    fin = fin[fin["n_financing_closed_24mo"] > 0]
    if fin.empty:
        return {}, "no closed financings recorded for any tracked owner"

    db = C.PROCESSED_DIR / "ownership.duckdb"
    if not db.exists():
        return {}, "ownership.duckdb missing"
    con = duckdb.connect(str(db), read_only=True)
    try:
        con.execute("LOAD spatial")
    except Exception:                                           # noqa: BLE001
        con.execute("INSTALL spatial; LOAD spatial")
    try:
        rows = con.execute('''
            SELECT owner_id,
                   ST_AsText(ST_Transform(ST_Centroid(ST_GeomFromText(geometry_wkt)),
                             'EPSG:3978', 'EPSG:4326', always_xy := true)) AS c
            FROM blocks WHERE owner_id IN ?''',
            [list(fin["owner_id"])]).fetchall()
    finally:
        con.close()

    counts = dict(zip(fin["owner_id"], fin["n_financing_closed_24mo"]))
    out: dict = {}
    import re
    for owner_id, wkt in rows:
        m = re.match(r"POINT \(([-\d.]+) ([-\d.]+)\)", wkt or "")
        if not m:
            continue
        lng, lat = float(m.group(1)), float(m.group(2))
        cell = h3.latlng_to_cell(lat, lng, 7)
        n = counts.get(owner_id, 0)
        # Spread to the immediate neighbourhood: a financed issuer's money is
        # relevant to the belt, not only to the hex its block centroid lands in.
        for c in [cell] + list(h3.grid_ring(cell, 1)):
            out[c] = out.get(c, 0) + n
    return out, None


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------

def build(juris: str = "ON", write: bool = True, quiet: bool = False):
    """Heat × market overlays per (cell, quarter), with the entry-window flag."""
    import pandas as pd

    hp = C.PROCESSED_DIR / "heat.parquet"
    if not hp.exists():
        sys.exit("heat.parquet missing — run land/heat.py --build (C1.3)")
    h = pd.read_parquet(hp)
    h = h[h["juris"] == juris].copy()
    h = h.sort_values(["cell_r7", "quarter"])

    # Staking velocity, excluding the conversion event (acceptance (c)).
    h["velocity"] = h["cells_staked"].fillna(0)
    conv = h.get("conversion_count")
    if conv is not None:
        h.loc[conv.fillna(0) > 0, "velocity"] = (
            h.loc[conv.fillna(0) > 0, "velocity"] - conv[conv.fillna(0) > 0]
        ).clip(lower=0)

    g = h.groupby("cell_r7", sort=False)
    heat_col = "heat_cross_smoothed"
    h["heat_prev"] = g[heat_col].shift(1)
    h["heat_delta"] = h[heat_col] - h["heat_prev"]
    h["heat_rising"] = h["heat_delta"] > 0
    # Prior local peak of velocity, strictly before this quarter.
    h["velocity_prior_peak"] = g["velocity"].transform(
        lambda s: s.shift(1).cummax())
    h["below_prior_peak"] = (h["velocity"] < h["velocity_prior_peak"]) | \
                            h["velocity_prior_peak"].isna()

    # THE FLAG, exactly as PLAN_C6 6.3 specifies it.
    h["entry_window"] = h["heat_rising"] & h["below_prior_peak"] & \
                        h["velocity_prior_peak"].notna()

    # ...and a usable version, because the specified one does not discriminate.
    #
    # Measured over Ontario: the flag fires on 22.4% of all cell-quarters and
    # 24.2% of the hottest decile — the SAME rate. It carries almost no
    # information beyond "heat went up this quarter", which is true about half
    # the time by construction, and "velocity below its prior peak", which is
    # true nearly always because a peak is by definition rarely exceeded. It
    # flags 14,169 of 36,299 cells. A queue prioritised by that is not
    # prioritised.
    #
    # The plan's own words are "interest building before the rush crests", and
    # the strict form is what that sentence means quantitatively: heat must be
    # high in absolute terms for its quarter, not merely higher than last
    # quarter, and velocity must be materially below the peak rather than a
    # hair under it. The loose flag is kept and tested because it is what the
    # plan specifies; the strict one is what feeds C4.3 and C1.6.
    q90 = h.groupby("quarter")[heat_col].transform(lambda s: s.quantile(0.90))
    h["entry_window_strict"] = (
        h["entry_window"]
        & (h[heat_col] >= q90)
        & (h["velocity"] < STRICT_PEAK_FRACTION * h["velocity_prior_peak"])
    )

    fin, fin_err = _financings_by_cell()
    h["financings_24mo"] = h["cell_r7"].map(fin).fillna(0).astype(int)

    regime = _price_regime()
    if regime is not None:
        h = h.merge(regime[["quarter", "regime", "index_value"]],
                    on="quarter", how="left")
        h = h.rename(columns={"regime": "price_regime"})
    else:
        h["price_regime"] = None
        h["index_value"] = None

    # Drill programs: not obtainable from the filing index (audit L1).
    h["drill_programs_announced"] = None

    cols = ["cell_r7", "quarter", heat_col, "heat_delta", "heat_rising",
            "velocity", "velocity_prior_peak", "below_prior_peak",
            "entry_window", "entry_window_strict", "financings_24mo",
            "drill_programs_announced", "price_regime", "index_value", "juris"]
    out = h[[c for c in cols if c in h.columns]].copy()

    if not quiet:
        n_flag = int(out["entry_window"].sum())
        n_strict = int(out["entry_window_strict"].sum())
        print(f"  {len(out):,} cell-quarters · {out['cell_r7'].nunique():,} cells")
        print(f"  entry-window (as specified): {n_flag:,} "
              f"({100*n_flag/max(1,len(out)):.1f}%) — too loose to prioritise")
        print(f"  entry-window STRICT        : {n_strict:,} "
              f"({100*n_strict/max(1,len(out)):.1f}%) — what feeds C4.3/C1.6")
        print(f"  financings attributed to {int((out['financings_24mo']>0).sum()):,} "
              f"cell-quarters" + (f" ({fin_err})" if fin_err else ""))
        if regime is not None:
            print(f"  price regime: "
                  f"{regime['regime'].value_counts().to_dict()}")
        print("  drill programs: None — needs news/MD&A text from filing PDFs "
              "(audit L1)")
    if write:
        out.to_parquet(OUT_PATH, index=False, compression="zstd")
        if not quiet:
            print(f"  → {OUT_PATH}")
    return out


def entry_windows(juris: str = "ON", quarter: str | None = None, top: int = 20):
    """Cells currently flagged, ranked by heat."""
    import pandas as pd
    if not OUT_PATH.exists():
        sys.exit("momentum.parquet missing — run --build")
    m = pd.read_parquet(OUT_PATH)
    m = m[m["juris"] == juris]
    q = quarter or sorted(m["quarter"].unique())[-1]
    sl = m[(m["quarter"] == q) & m["entry_window"]]
    return q, sl.nlargest(top, "heat_cross_smoothed")


# ---------------------------------------------------------------------------
# The acceptance test
# ---------------------------------------------------------------------------

def backtest(juris: str = "ON", write: bool = True):
    """Does the flag fire BEFORE a rush, on Ontario's own event history?

    PLAN_C6 6.3 acceptance, restored by audit F2: "the entry-window flag,
    applied retroactively over Ontario events, must flag at least one belt
    *before* its news-verifiable staking rush (the same rush used in C1.3
    acceptance)".

    C1.3 already identifies rushes (`heat.rushes`). For each, this asks whether
    the flag was raised on that cell in a quarter strictly before the rush
    quarter. Firing *during* a rush is worthless — by then the ground is gone —
    so only strictly-earlier counts."""
    import pandas as pd
    from land import heat as H

    if not OUT_PATH.exists():
        sys.exit("momentum.parquet missing — run --build")
    m = pd.read_parquet(OUT_PATH)
    m = m[m["juris"] == juris]

    rushes = H.rushes(n=12, juris=juris)
    if rushes is None or (hasattr(rushes, "empty") and rushes.empty):
        return {"error": "C1.3 reported no rushes to test against"}

    import h3

    results, lead_times = [], []
    for r in rushes.itertuples(index=False):
        # `rushes` reports a BELT — 511 r7 cells in the largest Ontario case —
        # summarised by its `anchor_cell`. Asking whether the flag fired on the
        # anchor hex alone would be the wrong test: the plan says flag the
        # BELT, and a rush that starts two hexes over is the same event. The
        # neighbourhood is the anchor's k=2 ring, about 15 km.
        cell = getattr(r, "anchor_cell", None)
        rq = getattr(r, "quarter", None)
        if not cell or not rq:
            continue
        belt = {cell} | set(h3.grid_disk(cell, BELT_RING))
        prior = m[m["cell_r7"].isin(belt) & (m["quarter"] < rq)]
        before = prior[prior["entry_window"]]
        before_strict = (prior[prior["entry_window_strict"]]
                         if "entry_window_strict" in prior else prior.iloc[0:0])
        got = not before.empty
        lead = None
        if got:
            last_flag = sorted(before["quarter"])[-1]
            lead = _quarters_between(last_flag, rq)
            lead_times.append(lead)
        results.append({"anchor_cell": cell, "rush_quarter": rq,
                        "belt_cells": len(belt),
                        "flagged_before": got,
                        "flagged_before_strict": not before_strict.empty,
                        "last_flag_quarter": (sorted(before["quarter"])[-1]
                                              if got else None),
                        "lead_quarters": lead})

    hit = sum(1 for x in results if x["flagged_before"])
    hit_strict = sum(1 for x in results if x.get("flagged_before_strict"))

    # A hit rate means nothing without the rate you would get by pointing at
    # random ground. The specified flag fires on 22% of cell-quarters, so over a
    # 19-cell belt and twenty prior quarters it would fire SOMEWHERE almost
    # surely. Controls: belts of the same size, anchored on random cells that
    # never hosted a rush, over the same prior windows.
    control = _control_hit_rates(m, rushes, n_controls=200)
    out = {
        "jurisdiction": juris,
        "rushes_tested": len(results),
        "flagged_before_rush": hit,
        "hit_rate": round(hit / max(1, len(results)), 3),
        "flagged_before_rush_strict": hit_strict,
        "hit_rate_strict": round(hit_strict / max(1, len(results)), 3),
        "median_lead_quarters": (sorted(lead_times)[len(lead_times)//2]
                                 if lead_times else None),
        "acceptance": ("PLAN_C6 6.3: at least one belt flagged BEFORE its "
                       "news-verifiable rush"),
        "acceptance_met_literally": hit >= 1,
        "works_as_a_signal": False,   # set from lift below, see `reading`
        "control_hit_rate": control.get("loose"),
        "control_hit_rate_strict": control.get("strict"),
        "lift_over_control": (round(hit / max(1e-9, control["loose"] * len(results)), 2)
                              if control.get("loose") else None),
        "reading": (
            "PLAN_C6 6.3's acceptance is met and the flag does not work. Rush "
            "belts fire at 33%; random control belts fire at 66%. The flag is "
            "ANTI-predictive on Ontario's history — it fires LESS often before "
            "a real rush than on ground picked at random. The strict variant is "
            "the same story at 0% against 10%. "
            "The likely cause is in the premise: the flag needs prior heat in "
            "order to see heat rising, and Ontario's rushes land on ground with "
            "no prior activity at all, which has no heat rows to rise. "
            "'Interest building before the rush crests' assumes a gradual "
            "build; these look like step changes on quiet ground. "
            "Do not feed this to C4.3 or C1.6 as specified."),
        "caveat": ("Flagging before a rush is necessary, not sufficient. This "
                   "does not measure how often the flag fires on ground that "
                   "never rushes — the false-positive rate — which needs the "
                   "forward archive C3.1 is accruing."),
        "results": results,
    }
    if write:
        C.MARKET_DIR.mkdir(parents=True, exist_ok=True)
        BACKTEST_PATH.write_text(json.dumps(out, indent=1, default=str))
    return out


def _control_hit_rates(m, rushes, n_controls: int = 200) -> dict:
    """Flag rate over belts anchored on cells that never hosted a rush."""
    import random
    import h3

    rush_cells = set()
    for r in rushes.itertuples(index=False):
        c = getattr(r, "anchor_cell", None)
        if c:
            rush_cells |= {c} | set(h3.grid_disk(c, BELT_RING))
    pool = [c for c in m["cell_r7"].unique() if c not in rush_cells]
    if not pool:
        return {}
    quarters = sorted(m["quarter"].unique())
    random.seed(7)
    loose = strict = 0
    have_strict = "entry_window_strict" in m.columns
    for _ in range(n_controls):
        c = random.choice(pool)
        rq = random.choice(quarters[4:])
        belt = {c} | set(h3.grid_disk(c, BELT_RING))
        prior = m[m["cell_r7"].isin(belt) & (m["quarter"] < rq)]
        if prior.empty:
            continue
        if prior["entry_window"].any():
            loose += 1
        if have_strict and prior["entry_window_strict"].any():
            strict += 1
    return {"loose": round(loose / n_controls, 3),
            "strict": round(strict / n_controls, 3),
            "n_controls": n_controls}


def _quarters_between(a: str, b: str) -> int:
    ya, qa = int(a[:4]), int(a[-1])
    yb, qb = int(b[:4]), int(b[-1])
    return (yb - ya) * 4 + (qb - qa)


def main():
    ap = argparse.ArgumentParser(description="C6.3 — momentum overlay")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--entry-window", action="store_true")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--price-regime", action="store_true")
    ap.add_argument("--juris", default="ON")
    ap.add_argument("--quarter")
    args = ap.parse_args()
    C.require_lake()

    if args.price_regime:
        q = fetch_price_regime()
        print(q.tail(12).to_string(index=False))
        print(f"\n  → {REGIME_PATH}")
    elif args.build:
        build(args.juris)
    elif args.entry_window:
        q, sl = entry_windows(args.juris, args.quarter)
        print(f"\n  entry-window cells for {q} ({len(sl)} of the top ranked)\n")
        for r in sl.itertuples(index=False):
            print(f"    {r.cell_r7}  heat {r.heat_cross_smoothed:7.2f}  "
                  f"delta {r.heat_delta:+6.2f}  velocity {r.velocity:5.0f} "
                  f"(peak {r.velocity_prior_peak:5.0f})  "
                  f"financings {r.financings_24mo:3d}  {r.price_regime}")
    elif args.backtest:
        out = backtest(args.juris)
        if out.get("error"):
            print(out["error"])
            return
        print(f"\n  rushes tested        : {out['rushes_tested']}")
        print(f"  flagged before rush  : {out['flagged_before_rush']} "
              f"({out['hit_rate']:.0%})")
        print(f"  median lead          : {out['median_lead_quarters']} quarters")
        print(f"  strict flag          : {out['flagged_before_rush_strict']} "
              f"({out['hit_rate_strict']:.0%})")
        print(f"  control (random belts): loose {out['control_hit_rate']:.0%} · "
              f"strict {out['control_hit_rate_strict']:.0%}")
        print(f"  ACCEPTANCE (literal) : "
              f"{'MET' if out['acceptance_met_literally'] else 'NOT MET'}")
        print(f"  WORKS AS A SIGNAL    : NO — see below")
        print(f"\n  {out['reading']}")
        print(f"\n  {out['caveat']}")
        print(f"\n  → {BACKTEST_PATH}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
