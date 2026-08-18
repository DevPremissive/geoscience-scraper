#!/usr/bin/env python3
"""
buyers.py — who would actually buy the ground, and what the evidence says (C6.2).

The deal thesis is "sell to the neighbour", so a buyer here is primarily the
owner of a block adjacent to ground we would acquire. This module answers, per
buyer: how badly does our ground matter to their position, do they have a
history of taking over other people's dropped ground, and are they currently
active enough to transact.

**Three fields are deliberately left empty.** `treasury_estimate`,
`burn_estimate` and `drill_program_status` live inside filing PDFs, not in the
filing index this repo can read, so they are emitted as null with
`missing_because` naming the exact document that would fill them. A treasury
figure with nothing behind it would break the provenance-or-fail rule the rest
of the lake runs on, and `buyer_capacity` — which is treasury versus typical
comp consideration — therefore stays null too. What is here is filed, dated and
checkable.

**Consolidator evidence is measured, not assumed.** PLAN_C6 6.2 sources
`consolidator_flag` from acquisition history. Ontario's registers do not carry
transfers: the cancelled register's revisions are boundary and administrative
changes, and across 431,735 records not one tenure shows a holder change between
revisions. What the registers *do* support, geometrically, is the behaviour that
actually matters to us — **taking over ground someone else dropped**. A current
claim whose polygon sits on a cancelled claim that terminated before the current
one was issued, held by a different party, is a pickup. Ontario has 110,206.

The first attempt matched those events on the r7 cell and produced 1.6 million
"pickups" from 533,210 expiries, because an r7 hex is 5.2 km² and co-occurrence
inside one is not co-location. The join is spatial for that reason.
Holder strings must be parsed before comparison, too: the operational register
writes `(100) NAME` and the cancelled register `(408864) NAME (100%)`, so raw
string comparison reports every single pair as a holder change, including the
16,174 cases that are one company re-staking its own ground.

Usage:
    python -m market.buyers --pickups ON
    python -m market.buyers --build ON
    python -m market.buyers --review            # unresolved / near-match issuers
    python -m market.buyers --buyer "KENORLAND"
"""
from __future__ import annotations
import argparse, json, sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from tenure_events import parse_holder
from land.ownership_graph import normalize, REVIEW_THRESHOLD

PICKUPS = C.MARKET_DIR / "pickups.parquet"
BUYERS = C.MARKET_DIR / "buyers.parquet"
ISSUERS = C.MARKET_DIR / "issuers.parquet"
UNRESOLVED = C.MARKET_DIR / "issuer_review_queue.parquet"

#: Register pairs per jurisdiction: (current claims layer, historical layer).
REGISTERS = {
    "ON": ("ON__ON_MLAS_TENURE__Operational_Cell_Claims",
           "ON__ON_MLAS_TENURE__Cancelled_Claim_Polygons",
           "ISSUE_DATE", "TERMINATIO"),
}

#: Percentile of province-wide pickup volume above which a party is treated as a
#: consolidator. Set from the observed distribution, not from a prior.
CONSOLIDATOR_PCTILE = 0.90

#: Fields that require filing CONTENT, with the document that carries each.
BLOCKED_FIELDS = {
    "treasury_estimate": "most recent audited annual or interim financial statements",
    "burn_estimate": "interim financial statements (cash used in operating activities)",
    "drill_program_status": "news release / MD&A text",
    "stated_plans_summary": "MD&A text",
    "buyer_capacity": "derived from treasury_estimate",
    "buyer_timing": "derived from drill_program_status",
}


def _holder_set(value, cache: dict):
    if value not in cache:
        cache[value] = frozenset(normalize(n) for n, _, _ in parse_holder(value) if n)
    return cache[value]


def pickups(juris: str = "ON", write: bool = True):
    """Current claims standing on ground a different party let go."""
    import geopandas as gpd
    import pandas as pd

    reg = REGISTERS.get(juris)
    if not reg:
        sys.exit(f"no register pair registered for {juris}")
    cur_layer, hist_layer, issue_col, term_col = reg

    cur = gpd.read_file(C.GPKG_PATH, layer=cur_layer,
                        columns=["TENURE_NUM", "HOLDER", issue_col])
    hist = gpd.read_file(C.GPKG_PATH, layer=hist_layer,
                         columns=["TENURE_NUM", "HOLDER", term_col, "STATUS"])
    print(f"  {len(cur):,} current claims, {len(hist):,} historical records")

    # representative_point() rather than centroid: a claim cell is convex here,
    # but representative_point is guaranteed inside the polygon either way.
    pts = cur.set_geometry(cur.representative_point())
    j = gpd.sjoin(pts, hist, predicate="within", how="inner")
    j[issue_col] = pd.to_datetime(j[issue_col])
    j[term_col] = pd.to_datetime(j[term_col])
    j = j[j[term_col].notna() & (j[issue_col] > j[term_col])]
    print(f"  {len(j):,} current claims sit on previously-terminated ground")

    cache: dict = {}
    now = j["HOLDER_left"].astype(object).map(lambda v: _holder_set(v, cache))
    was = j["HOLDER_right"].astype(object).map(lambda v: _holder_set(v, cache))
    same = now.eq(was)
    shared = pd.Series([bool(a & b) for a, b in zip(now, was)], index=j.index)

    j = j.assign(
        picker=[sorted(s)[0] if s else None for s in now],
        dropper=[sorted(s)[0] if s else None for s in was],
        relation=pd.Series("pickup", index=j.index).mask(
            shared & ~same, "partner_change").mask(same, "self_restake"),
        gap_days=(j[issue_col] - j[term_col]).dt.days,
    )
    print(f"    self re-stake   {int(same.sum()):>8,}")
    print(f"    partner change  {int((shared & ~same).sum()):>8,}")
    print(f"    true pickup     {int((~shared).sum()):>8,}")

    out = pd.DataFrame({
        "juris": juris,
        "claim_id": j["TENURE_NUM_left"].astype(str),
        "prior_claim_id": j["TENURE_NUM_right"].astype(str),
        "picker": j["picker"], "dropper": j["dropper"],
        "relation": j["relation"],
        "prior_terminated": j[term_col], "issued": j[issue_col],
        "gap_days": j["gap_days"], "prior_status": j["STATUS"],
    })
    if write:
        C.MARKET_DIR.mkdir(parents=True, exist_ok=True)
        out.to_parquet(PICKUPS, index=False, compression="zstd")
        print(f"  → {PICKUPS}")
    return out


def _seed(juris: str):
    """Owners whose position our candidate ground touches.

    `criticality` is already exactly this join — it scores open cells against the
    block whose frontier they sit on — so its owners are the adjacent owners,
    with the strength of the adjacency attached rather than a flat 5 km buffer.
    """
    import duckdb
    import pandas as pd

    db = C.PROCESSED_DIR / "ownership.duckdb"
    crit = pd.read_parquet(C.PROCESSED_DIR / "criticality.parquet")
    con = duckdb.connect(str(db), read_only=True)
    owners = con.execute(
        "SELECT owner_id, name_raw, entity_type_guess, claims, historical_claims, "
        "status FROM owners").df()
    blocks = con.execute(
        "SELECT owner_id, block_id, n_claims, area_ha FROM blocks WHERE juris = ?",
        [juris]).df()
    con.close()

    agg = crit.groupby("owner_id").agg(
        critical_cells=("cell_id", "nunique"),
        max_criticality=("score", "max"),
        mean_criticality=("score", "mean"),
        blocks_at_risk=("block_id", "nunique")).reset_index()
    seed = owners.merge(agg, on="owner_id", how="inner")
    seed["why_tracked"] = "adjacent_owner"

    lw = pd.read_parquet(C.PROCESSED_DIR / "lapse_watch.parquet")
    lw_names = {normalize(n) for n in lw["owner"].dropna()}
    seed["on_lapse_watch"] = seed["name_raw"].map(
        lambda n: normalize(n) in lw_names)

    ba = blocks.groupby("owner_id").agg(
        n_blocks=("block_id", "nunique"),
        held_area_ha=("area_ha", "sum")).reset_index()
    return seed.merge(ba, on="owner_id", how="left")


def _resolve(seed, issuers):
    """Owner name → SEDAR+ issuer, mechanically. Near matches never auto-merge.

    Same doctrine as C1.4: normalise, match exactly, and send anything short of
    that to a human. A buyer profile attached to the wrong issuer is a treasury
    figure for a company that does not own the neighbouring ground.
    """
    import pandas as pd
    from difflib import SequenceMatcher

    lookup: dict = {}
    for r in issuers.itertuples(index=False):
        for nm in [r.name, *r.aliases]:
            k = normalize(nm)
            if k:
                lookup.setdefault(k, r.issuer_id)
    keys = list(lookup)

    ids, near = [], []
    for name, etype in zip(seed["name_raw"], seed["entity_type_guess"]):
        k = normalize(name)
        if k in lookup:
            ids.append(lookup[k])
            continue
        ids.append(None)
        # Individuals are not issuers; proposing matches for them floods the
        # queue with forename collisions, which is the C1.4 lesson.
        if etype != "corporation" or not k:
            continue
        best, score = None, 0.0
        for cand in keys:
            if abs(len(cand) - len(k)) > 8 or cand[:3] != k[:3]:
                continue
            s = SequenceMatcher(None, k, cand).ratio()
            if s > score:
                best, score = cand, s
        if best and score >= REVIEW_THRESHOLD:
            near.append({"owner_name": name, "candidate_issuer_name": best,
                         "candidate_issuer_id": lookup[best],
                         "similarity": round(score, 4),
                         "reason": "normalized near-match, NOT auto-merged"})
    seed = seed.copy()
    seed["sedar_issuer_id"] = ids
    return seed, pd.DataFrame(near)



def _universe():
    """The exchange/ticker list the scraper project keeps, used for two things:
    filling `exchange`/`tickers` for buyers whose filings we have not captured,
    and telling a genuinely-absent issuer apart from a name that is not an
    issuer at all."""
    import pandas as pd
    if not C.SEDAR_UNIVERSE.exists():
        return pd.DataFrame(columns=["clean_name", "ticker", "exchange_code", "norm_name"])
    # Company names in this file contain unquoted commas ("Abacus Mining &
    # Exploration Corporation, formerly ..."), so a plain read_csv fails on the
    # field count. The trailing six columns are well-formed, so split from the
    # right and let the name keep its commas.
    lines = C.SEDAR_UNIVERSE.read_text(encoding="utf-8", errors="replace").splitlines()
    head = lines[0].split(",")
    rows = [r.rsplit(",", len(head) - 1) for r in lines[1:] if r.strip()]
    u = pd.DataFrame([r for r in rows if len(r) == len(head)], columns=head)
    u["norm_name"] = u["clean_name"].map(normalize)
    return u


def _explain_unresolved(seed, near):
    """Label every owner with why it did or did not resolve to an issuer."""
    import pandas as pd
    u = _universe()
    by_norm = {r.norm_name: r for r in u.itertuples(index=False) if r.norm_name}
    queued = set(near["owner_name"]) if len(near) else set()

    status, ticker, exch = [], [], []
    for name, etype, iid in zip(seed["name_raw"], seed["entity_type_guess"],
                                seed["sedar_issuer_id"]):
        k = normalize(name)
        hit = by_norm.get(k)
        ticker.append(getattr(hit, "ticker", None))
        exch.append(getattr(hit, "exchange_code", None))
        # pandas turns the unmatched None into NaN, and float('nan') is truthy —
        # a bare `if iid` reported every owner as resolved.
        if pd.notna(iid) and iid:
            status.append("resolved")
        elif name in queued:
            status.append("near_match_queued")
        elif etype in ("individual_or_unknown", "numbered_company"):
            status.append("not_an_issuer")
        elif hit is not None:
            status.append("listed_but_filings_not_captured")
        else:
            status.append("absent_from_local_corpus")
    seed = seed.copy()
    seed["resolution_status"] = status
    seed["ticker"] = ticker
    seed["exchange"] = exch
    counts = pd.Series(status).value_counts()
    for k, v in counts.items():
        print(f"    {k:<34}{v:>4}")
    gaps = seed[seed["resolution_status"].isin(
        ["listed_but_filings_not_captured", "absent_from_local_corpus"])][
        ["name_raw", "ticker", "exchange", "claims", "resolution_status"]].copy()
    gaps["wanted_for"] = "C6.2 buyer profile (treasury, financings, programs)"
    return seed, gaps


def build(juris: str = "ON", write: bool = True):
    import pandas as pd
    import market.sedar_local as S

    if not PICKUPS.exists():
        print("  pickups not built yet — building")
        pickups(juris)
    pk = pd.read_parquet(PICKUPS)

    seed = _seed(juris)
    print(f"  {len(seed):,} adjacent owners seeded from criticality")

    idf, fdf, _ = S.load_corpus()
    print(f"  {len(idf):,} SEDAR+ issuer profiles available locally")
    seed, near = _resolve(seed, idf)
    matched = int(seed["sedar_issuer_id"].notna().sum())
    corp = int((seed["entity_type_guess"] == "corporation").sum())
    print(f"  resolved {matched:,} of {corp:,} corporate owners to an issuer "
          f"({100*matched/max(1,corp):.0f}%); {len(near):,} near-matches queued")

    # Why an owner is unresolved is actionable, and the two reasons need
    # different work. A name absent from the local corpus is a capture gap and
    # the fix is to harvest that issuer; an ambiguous name is a human decision.
    # Reporting one number for both would hide which.
    seed, gaps = _explain_unresolved(seed, near)
    if write and len(gaps):
        C.MARKET_DIR.mkdir(parents=True, exist_ok=True)
        gaps.to_parquet(C.MARKET_DIR / "corpus_gaps.parquet", index=False,
                        compression="zstd")
        print(f"  → {C.MARKET_DIR/'corpus_gaps.parquet'}  ({len(gaps)} issuers "
              f"worth capturing next)")

    act = S.activity(fdf)
    fin = S.latest_financials(fdf)
    prof = seed.merge(act, left_on="sedar_issuer_id", right_on="issuer_id",
                      how="left").merge(fin, on="issuer_id", how="left")

    # Pickup history, on normalised names so it works for owners with no issuer.
    real = pk[pk["relation"] == "pickup"]
    ph = real.groupby("picker").agg(
        pickup_claims=("claim_id", "nunique"),
        pickup_counterparties=("dropper", "nunique"),
        last_pickup=("issued", "max"),
        median_gap_days=("gap_days", "median")).reset_index()
    prof["norm_name"] = prof["name_raw"].map(normalize)
    prof = prof.merge(ph, left_on="norm_name", right_on="picker", how="left")
    for c in ("pickup_claims", "pickup_counterparties"):
        prof[c] = prof[c].fillna(0).astype(int)

    # consolidator_flag: has this party demonstrably taken over ground dropped by
    # more than one other party. One pickup is opportunism; a pattern across
    # counterparties is a strategy.
    #
    # The threshold is the province-wide 90th percentile of pickup volume, not a
    # constant. A flat ">= 10 claims" was tried first and flagged 26 of 27 seeded
    # buyers, because the seed set is by construction the large holders and the
    # province-wide median picker has taken over 17 claims — a flag that is true
    # of everyone ranks nothing.
    thresh = float(ph["pickup_claims"].quantile(CONSOLIDATOR_PCTILE)) if len(ph) else 0.0
    prof["consolidator_flag"] = ((prof["pickup_claims"] >= thresh) &
                                 (prof["pickup_counterparties"] >= 2))
    prof["consolidator_threshold"] = thresh
    print(f"  consolidator threshold: p{int(CONSOLIDATOR_PCTILE*100)} of "
          f"{len(ph):,} province-wide pickers = {thresh:.0f} claims "
          f"→ {int(prof['consolidator_flag'].sum())} of {len(prof)} buyers flagged")

    # buyer_propensity — a transparent 0–1 blend of things that are filed or
    # measured. Each term is exported alongside so any one can be disputed.
    import numpy as np
    def unit(s):
        s = pd.to_numeric(s, errors="coerce").fillna(0).astype(float)
        s = np.log1p(s)
        return (s / s.max()).fillna(0) if s.max() > 0 else s
    prof["t_adjacency"] = pd.to_numeric(prof["max_criticality"],
                                        errors="coerce").fillna(0).clip(0, 1)
    prof["t_pickup"] = unit(prof["pickup_claims"])
    prof["t_disclosure"] = unit(prof.get("n_material_change_24mo", 0).fillna(0)
                                + prof.get("n_technical_report_24mo", 0).fillna(0))
    prof["buyer_propensity"] = (0.45 * prof["t_adjacency"] +
                                0.35 * prof["t_pickup"] +
                                0.20 * prof["t_disclosure"]).round(4)

    for f, why in BLOCKED_FIELDS.items():
        prof[f] = None
    prof["missing_because"] = json.dumps(BLOCKED_FIELDS)
    prof["profile_as_of"] = dt.date.today().isoformat()
    prof["juris"] = juris
    prof = prof.drop(columns=["norm_name", "picker"], errors="ignore")
    prof = prof.sort_values("buyer_propensity", ascending=False)

    if write:
        C.MARKET_DIR.mkdir(parents=True, exist_ok=True)
        prof.to_parquet(BUYERS, index=False, compression="zstd")
        # The tracking list C3.5 consumes to scope pulls — resolved issuers only,
        # never the whole exchange.
        tracked = prof[prof["sedar_issuer_id"].notna()][
            ["sedar_issuer_id", "name_raw", "why_tracked", "juris"]].rename(
            columns={"sedar_issuer_id": "issuer_id", "name_raw": "name"})
        tracked["tracked_since"] = dt.date.today().isoformat()
        tracked.drop_duplicates("issuer_id").to_parquet(
            ISSUERS, index=False, compression="zstd")
        if len(near):
            near.to_parquet(UNRESOLVED, index=False, compression="zstd")
        print(f"  → {BUYERS}  ({len(prof):,} buyers)")
        print(f"  → {ISSUERS}  ({tracked['issuer_id'].nunique():,} tracked issuers)")
        if len(near):
            print(f"  → {UNRESOLVED}  ({len(near):,} awaiting a human)")
    return prof


def show(pattern: str):
    import pandas as pd

    def n(v):
        return 0 if v is None or pd.isna(v) else int(v)

    b = pd.read_parquet(BUYERS)
    hit = b[b["name_raw"].str.contains(pattern, case=False, na=False)]
    if hit.empty:
        sys.exit(f"no buyer matching {pattern!r}")
    for r in hit.head(3).itertuples(index=False):
        resolved = pd.notna(r.sedar_issuer_id) and r.sedar_issuer_id
        print(f"\n  {r.name_raw}")
        print(f"    issuer            "
              f"{r.sedar_issuer_id if resolved else '— ' + r.resolution_status + ' —'}")
        print(f"    holds             {r.claims:,} claims in {n(r.n_blocks)} blocks"
              f"  ({r.held_area_ha:,.0f} ha)")
        print(f"    adjacency         {r.critical_cells:,} critical open cells, "
              f"max score {r.max_criticality:.3f}")
        print(f"    pickups           {r.pickup_claims:,} claims taken over from "
              f"{r.pickup_counterparties:,} other parties"
              f"{'  [CONSOLIDATOR]' if r.consolidator_flag else ''}")
        if resolved:
            print(f"    filed 24mo        {n(r.n_material_change_24mo)} material "
                  f"change, {n(r.n_financing_closed_24mo)} closed financing, "
                  f"{n(r.n_technical_report_24mo)} technical report")
            print(f"    news cadence      {r.news_cadence_per_quarter}/quarter")
            src = r.treasury_source_doc if pd.notna(r.treasury_source_doc) else "financials"
            when = (f" filed {r.treasury_source_date.date()}"
                    if pd.notna(r.treasury_source_date) else "")
            print(f"    treasury          UNKNOWN — needs {src}{when}")
        else:
            print(f"    filings           none captured locally "
                  f"({r.resolution_status})")
        print(f"    propensity        {r.buyer_propensity}  "
              f"(adjacency {r.t_adjacency:.2f} / pickup {r.t_pickup:.2f} / "
              f"disclosure {r.t_disclosure:.2f})")
        print(f"    capacity, timing  NULL — blocked on filing content")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pickups", metavar="JURIS")
    ap.add_argument("--build", metavar="JURIS")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--buyer", metavar="PATTERN")
    a = ap.parse_args()
    C.require_lake()
    if a.pickups:
        pickups(a.pickups)
    if a.build:
        build(a.build)
    if a.review:
        import pandas as pd
        if not UNRESOLVED.exists():
            sys.exit("no review queue")
        q = pd.read_parquet(UNRESOLVED)
        print(q.sort_values("similarity", ascending=False).to_string(index=False))
    if a.buyer:
        show(a.buyer)
    if not any([a.pickups, a.build, a.review, a.buyer]):
        ap.print_help()


if __name__ == "__main__":
    main()
