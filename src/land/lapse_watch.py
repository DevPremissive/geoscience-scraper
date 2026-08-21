#!/usr/bin/env python3
"""
lapse_watch.py — claims about to expire on ground we care about (C1.6).

Watches published expiry dates and raises an alert when a claim nearing its due
date sits on interesting ground: a top-decile heat cell, a cell scored critical
for a watched block, or a saved screen result.

**The safety property is that lapse is not the same as open.** Ontario's active
register currently holds 12,440 claims already past their due date — they are
past due and still held, sitting in grace or pending forfeiture. Flagging that
ground as stakeable would send someone to file on a claim that is still someone
else's. So:

  * an expiry alert says "watch this", never "stake this";
  * `stakeable_after()` needs a reopening delay from `rules/<juris>.yaml`, and
    that field is currently unfilled for Ontario. Until a human fills it, this
    module **refuses to mark anything stakeable** rather than assuming zero.

That refusal is the whole point. A lapse watch that guesses the reopening
interval is worse than none: it produces confident, actionable, wrong advice.

Watch rules are configuration, not code — see WATCH_RULES.

Usage:
    python -m land.lapse_watch --scan ON
    python -m land.lapse_watch --scan ON --days 60
    python -m land.lapse_watch --stakeable ON      # refuses until rules are signed
"""
from __future__ import annotations
import argparse, json, sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from land import rules as R

OUT_PATH = C.PROCESSED_DIR / "lapse_watch.parquet"
REOPENED_PATH = C.PROCESSED_DIR / "reopened_confirmed.parquet"

#: Which attribute carries the expiry date, per jurisdiction. Where a registry
#: publishes none, the fallback is observed `expired` events from tenure_events.
EXPIRY_FIELD = {
    "ON": ("ON__ON_MLAS_TENURE__Operational_Cell_Claims", "CLAIM_DUE_"),
}

#: Watch rules — configuration, deliberately not code.
WATCH_RULES = {
    "horizon_days": 30,
    "heat_top_decile": True,        # (a) claim overlaps a top-decile heat cell
    "criticality_min": 0.5,         # (b) claim overlaps a critical cell
    "saved_screens": [],            # (c) C4 screen result sets, when they exist
}


#: Quarters of heat history that count as "recently hot".
HEAT_WINDOW_QUARTERS = 4


def _heat_threshold(juris: str, decile: float = 0.9,
                    window: int = HEAT_WINDOW_QUARTERS):
    """Top-decile cells by their best heat over the trailing `window` quarters.

    Deliberately not the latest quarter alone. The most recent quarter is
    usually PARTIAL — the harvest lands mid-quarter — so it is the least
    representative window available, and using it produced 142 hot cells that
    overlapped none of the 882 cells holding claims due within 30 days. "Hot
    lately" is the question a lapse watch actually asks.
    """
    import pandas as pd
    p = C.PROCESSED_DIR / "heat.parquet"
    if not p.exists():
        return None, set()
    h = pd.read_parquet(p)
    h = h[h["juris"] == juris]
    if h.empty:
        return None, set()
    qs = sorted(h["quarter"].unique())[-window:]
    recent = h[h["quarter"].isin(qs)]
    best = recent.groupby("cell_r7")["heat_cross_smoothed"].max()
    thr = float(best.quantile(decile))
    return thr, set(best[best >= thr].index)


def _critical_cells(min_score: float):
    import pandas as pd
    p = C.PROCESSED_DIR / "criticality.parquet"
    if not p.exists():
        return {}
    c = pd.read_parquet(p)
    c = c[c["score"] >= min_score]
    out: dict = {}
    for r in c.itertuples(index=False):
        out.setdefault(r.cell_id, []).append((r.block_id, float(r.score)))
    return out


def scan(juris: str = "ON", days: int | None = None, write: bool = True):
    """Claims expiring within `days` that overlap ground we care about."""
    import geopandas as gpd
    import h3
    import pandas as pd
    import tenure_events as TE

    days = days or WATCH_RULES["horizon_days"]
    spec = EXPIRY_FIELD.get(juris)
    if not spec:
        sys.exit(f"no published expiry field registered for {juris}")
    layer, field = spec

    g = gpd.read_file(C.GPKG_PATH, layer=layer,
                      columns=["TENURE_NUM", field, "HOLDER"])
    g[field] = pd.to_datetime(g[field], errors="coerce")
    today = pd.Timestamp(dt.date.today())
    horizon = today + pd.Timedelta(days=days)

    due = g[(g[field] >= today) & (g[field] <= horizon)].copy()
    overdue = int((g[field] < today).sum())
    print(f"  {juris}: {len(due):,} claims due within {days}d; "
          f"{overdue:,} already past due (in grace / pending forfeiture)")
    if due.empty:
        return pd.DataFrame()

    thr, hot = _heat_threshold(juris)
    crit = _critical_cells(WATCH_RULES["criticality_min"])
    print(f"  heat top-decile cells: {len(hot):,} (threshold {thr:.2f})"
          if thr is not None else "  heat unavailable")
    print(f"  critical cells (>= {WATCH_RULES['criticality_min']}): {len(crit):,}")

    due["cell_r7"] = TE.cells_r7(due)
    # r9 for criticality, which is scored at claim scale
    cent = due.to_crs("EPSG:3978").geometry.centroid.to_crs(4326)
    due["cell_r9"] = [h3.latlng_to_cell(p.y, p.x, 9) for p in cent]
    due["owner"] = due["HOLDER"].map(TE.holder_name)

    rows = []
    for r in due.itertuples(index=False):
        reasons = []
        if r.cell_r7 in hot:
            reasons.append("heat_top_decile")
        # Criticality is scored on OPEN ground, and an expiring claim sits on
        # CLAIMED ground — its own cell can never be in that set. What matters
        # is whether the ground this claim would RELEASE adjoins ground a
        # neighbour is already reaching for, so the claim's r9 neighbours are
        # checked too.
        hit = crit.get(r.cell_r9)
        via = "cell"
        if not hit:
            for nb in h3.grid_ring(r.cell_r9, 1):
                if nb in crit:
                    hit = crit[nb]
                    via = "adjacent"
                    break
        if hit:
            block, score = max(hit, key=lambda t: t[1])
            reasons.append(f"criticality>={WATCH_RULES['criticality_min']}"
                           f":{via}:{block}:{score:.2f}")
        if not reasons:
            continue
        exp = getattr(r, field)
        rows.append({
            "juris": juris,
            "claim_id": str(r.TENURE_NUM),
            "expiry_date": exp,
            "days_to_expiry": int((exp - today).days),
            "owner": r.owner,
            "cell_r7": r.cell_r7,
            "cell_r9": r.cell_r9,
            "reasons": json.dumps(reasons),
            # Deliberately NOT "stakeable". See stakeable_after().
            "status": "watch_only",
            "scanned_at": dt.datetime.now().isoformat(timespec="seconds"),
        })

    df = pd.DataFrame(rows)
    print(f"\n  {len(df):,} watch alert(s) on interesting ground")
    if not df.empty:
        print(f"    heat-driven        {df['reasons'].str.contains('heat').sum():,}")
        print(f"    criticality-driven {df['reasons'].str.contains('criticality').sum():,}")
    if write and not df.empty:
        df.to_parquet(OUT_PATH, index=False, compression="zstd")
        print(f"  → {OUT_PATH}")
    return df


def reopening_delay_days(juris: str):
    """Days after expiry before ground is stakeable, or None if unknown.

    Read from `rules/<juris>.yaml` `expiry_mechanics`. Returns None when the
    rules are unsigned or the field is still prose rather than a number — which
    is the current Ontario state, and which callers must treat as "do not know".
    """
    try:
        rules = R.load(juris)
    except FileNotFoundError:
        return None
    if not R.is_verified(rules):
        return None
    mech = rules.get("expiry_mechanics")
    if isinstance(mech, dict):
        v = mech.get("reopening_delay_days")
        return int(v) if isinstance(v, (int, float)) else None
    return None


def confirm_reopened(juris: str = "ON", write: bool = True,
                     as_of: str | None = None):
    """Ground from expired claims that has provably reopened (C1.6, final step).

    PLAN_C1 1.6: "lapse != instantly open in every jurisdiction (grace periods,
    pending-forfeiture states ...); the alert pipeline must re-check
    `land_state` after the rules-defined reopening delay before flagging ground
    as stakeable."

    Three conditions, all required, and the third is the one that does the work:

      1. the claim's due date is at least `reopening_delay_days` in the past —
         3 for Ontario, measured from the due date (`CLAIM_DUE_`), sourced from
         the MNDM relief-from-forfeiture policy;
      2. the claim is GONE from the current operational register, which is the
         register's own statement that it forfeited rather than our inference
         from a date;
      3. `land_state` now calls the ground **open**.

    Condition 2 matters because Ontario's active register holds thousands of
    claims past their due date and still held — extensions, exclusions of time,
    and relief from forfeiture all keep a claim alive past a date that looks
    terminal. Reading the date alone would point a buyer at owned ground, which
    is the specific error this function exists to avoid.

    Even all three together is NOT clean title: the Recorder can relieve a
    forfeiture caused by Crown administrative error after someone else has
    registered, and impose terms or refer the matter to the Tribunal (Mining Act
    s.49(1)). Every row carries that caveat.
    """
    import geopandas as gpd
    import pandas as pd

    delay = reopening_delay_days(juris)
    if delay is None:
        stakeable_after(juris)
        return None

    spec = EXPIRY_FIELD.get(juris)
    if not spec:
        sys.exit(f"no published expiry field registered for {juris}")
    layer, field = spec
    today = pd.Timestamp(as_of or dt.date.today())
    cutoff = today - pd.Timedelta(days=delay)

    # The register as it stands now. A claim still in here has not forfeited,
    # whatever its due date says.
    live = gpd.read_file(C.GPKG_PATH, layer=layer,
                         columns=["TENURE_NUM", field])
    live[field] = pd.to_datetime(live[field], errors="coerce")
    live_ids = set(live["TENURE_NUM"].astype(str))
    past_due_but_held = int((live[field] < today).sum())

    # Claims we last saw expiring, from the watch list.
    if not OUT_PATH.exists():
        print(f"  no lapse-watch scan on disk — run --scan first")
        return None
    watch = pd.read_parquet(OUT_PATH)
    watch = watch[watch["juris"] == juris].copy()
    watch["expiry_date"] = pd.to_datetime(watch["expiry_date"], errors="coerce")

    eligible = watch[watch["expiry_date"] <= cutoff].copy()
    gone = eligible[~eligible["claim_id"].astype(str).isin(live_ids)].copy()

    print(f"  {juris}: reopening delay {delay}d from the due date")
    print(f"  {len(watch):,} watched · {len(eligible):,} past due+delay · "
          f"{len(gone):,} also gone from the register")
    print(f"  ({past_due_but_held:,} claims in the register are past due and "
          f"STILL HELD — extensions, exclusions, or relief)")
    if gone.empty:
        print("  nothing confirmed reopened")
        return pd.DataFrame()

    # Third condition: land_state must actually call it open.
    states = _land_state_for(gone["cell_r9"].tolist(), juris)
    gone["land_state"] = gone["cell_r9"].map(states)
    confirmed = gone[gone["land_state"] == "open"].copy()
    unknown = gone[gone["land_state"].isna()]
    blocked = gone[gone["land_state"].notna() & (gone["land_state"] != "open")]

    print(f"  land_state: {len(confirmed):,} open · {len(blocked):,} still "
          f"blocked · {len(unknown):,} outside any computed AOI")
    if confirmed.empty:
        return pd.DataFrame()

    confirmed["status"] = "reopened_confirmed"
    confirmed["confirmed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    confirmed["reopening_delay_days"] = delay
    confirmed["title_caveat"] = (
        "Not clean title. A forfeiture caused by Crown administrative error can "
        "be relieved by the Recorder after a new registration, with terms or a "
        "Tribunal referral (Mining Act s.49(1)).")
    if write:
        confirmed.to_parquet(REOPENED_PATH, index=False, compression="zstd")
        print(f"  → {REOPENED_PATH}")
    return confirmed


def _land_state_for(cells_r9, juris: str = "ON") -> dict:
    """`{cell_r9: state}` from whichever computed AOI covers each cell."""
    import pandas as pd
    out: dict = {}
    want = set(cells_r9)
    d = C.PROCESSED_DIR / "land_state"
    if not d.exists():
        return out
    for p in sorted(d.glob(f"{juris}__*.parquet")):
        df = pd.read_parquet(p, columns=["cell_id", "state"])
        hit = df[df["cell_id"].isin(want)]
        for r in hit.itertuples(index=False):
            out.setdefault(r.cell_id, r.state)
    return out


def stakeable_after(juris: str = "ON"):
    """Expired claims whose ground has provably reopened. Refuses when unknown."""
    delay = reopening_delay_days(juris)
    if delay is None:
        ok, missing = R.stakeable_now(juris)
        print(f"  REFUSING to flag any ground stakeable in {juris}.")
        print(f"  The reopening delay is not established: rules/{juris.upper()}.yaml")
        print(f"  is {'unsigned' if not ok else 'signed'} and `expiry_mechanics` does not")
        print(f"  carry a numeric `reopening_delay_days`.")
        print()
        print(f"  This is not a missing feature. Ontario's active register holds")
        print(f"  thousands of claims already past their due date and still held —")
        print(f"  in grace or pending forfeiture. Guessing the interval would point")
        print(f"  someone at ground that is still owned.")
        print()
        print(f"  To enable: fill `expiry_mechanics.reopening_delay_days` in")
        print(f"  rules/{juris.upper()}.yaml and sign the file.")
        return None
    print(f"  reopening delay for {juris}: {delay} days "
          f"(from the due date) — see confirm_reopened() for the ground itself")
    return delay


def notify(df, limit: int = 10):
    """Hand the top alerts to C3.6's choke point."""
    from alerting import alert
    if df is None or df.empty:
        return 0
    sent = 0
    for r in df.nsmallest(limit, "days_to_expiry").itertuples(index=False):
        alert("lapse-watch",
              f"{r.claim_id} expires in {r.days_to_expiry}d",
              f"owner {r.owner}\ncell {r.cell_r7}\nreasons "
              f"{', '.join(json.loads(r.reasons))}\nWATCH ONLY — not confirmed "
              f"stakeable; reopening delay unestablished",
              key=f"lapse-{r.claim_id}", severity="warning")
        sent += 1
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", metavar="JURIS")
    ap.add_argument("--days", type=int)
    ap.add_argument("--stakeable", metavar="JURIS",
                    help="report the reopening delay gate")
    ap.add_argument("--confirm-reopened", metavar="JURIS",
                    help="ground from expired claims that has provably reopened")
    ap.add_argument("--as-of", help="evaluate the delay as of this date")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args()
    C.require_lake()
    if args.scan:
        df = scan(args.scan, args.days)
        if args.notify:
            n = notify(df)
            print(f"  {n} alert(s) handed to C3.6")
    if args.stakeable:
        stakeable_after(args.stakeable)
    if args.confirm_reopened:
        df = confirm_reopened(args.confirm_reopened, as_of=args.as_of)
        if df is not None and not df.empty:
            print(f"\n  {len(df)} parcel(s) confirmed reopened:")
            for r in df.head(10).itertuples(index=False):
                print(f"    {r.claim_id:10s} due {str(r.expiry_date)[:10]}  "
                      f"{r.owner[:38]}")
            print(f"\n  {df['title_caveat'].iloc[0]}")
    if not (args.scan or args.stakeable or args.confirm_reopened):
        ap.print_help()


if __name__ == "__main__":
    main()
