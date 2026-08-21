#!/usr/bin/env python3
"""
assemble.py — pull each dossier section from its producing component (C4.1).

Every value is stamped with `(source_id, snapshot_date)` as it is read, and a
value that cannot be stamped does not get reported. Sections whose upstream
component does not exist yet render an explicit NOT AVAILABLE block naming the
component — which is why a Phase-1 dossier legitimately shows gaps at Economics
and History rather than quietly omitting them.

**The licence gate is enforced here, not in the renderer.** Ontario's MLAS
tenure ships under MNDM Electronic Information Products terms: commercial
distribution, creation of value-added products, and reproduction of maps or
figures each require prior written permission, and a buyer-facing dossier is all
three at once (audit H). `profile="sales"` therefore refuses to assemble while
any contributing source is licence-gated and unpermissioned.

Usage:
    python -m dossier.assemble --cell 892b968dea7ffff
    python -m dossier.assemble --cell 892b968dea7ffff --profile sales
"""
from __future__ import annotations
import argparse, json, sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from dossier.schema import Dossier, Section, Fact, Figure, Provenance

#: Sources whose licence forbids redistribution without written permission.
#: Keyed by the substring that identifies them in a source_id.
LICENCE_GATED = {
    "ON_MLAS": (
        "Ontario MLAS tenure ships under MNDM Electronic Information Products "
        "terms, not an open licence. Commercial distribution, creation of "
        "value-added products, and reproduction of maps or figures each require "
        "prior written permission from MNDM (Pubsales.ndm@ontario.ca). A "
        "buyer-facing dossier is all three at once."),
}


def _prov(source_id: str, snapshot: str) -> Provenance:
    return Provenance(source_id=source_id, snapshot_date=snapshot)


def _figure(key: str, title: str, caption: str, cell_id: str,
            layers: tuple[str, ...], pad: float, snapshot: str,
            block: str | None = None, width: int = 780, height: int = 520):
    """Render one map figure through the C4.2 service.

    PLAN_C4 4.1 says dossier figures come from "the map service's render
    endpoint — one map code path, not two". This calls the resolvers directly
    rather than over HTTP: it is the same code, and a dossier that only
    assembles when a web server happens to be running is a worse artifact.

    A figure that cannot be drawn returns None and the section says so. It must
    never take the dossier down — the text is the evidence, the picture is
    navigation."""
    import base64
    try:
        import mapapi
    except Exception as e:
        return None, f"figure unavailable: map service failed to import ({e})"
    try:
        bbox = mapapi.cell_bbox(cell_id, pad)
        built = []
        for name in layers:
            fn = mapapi.RENDER_LAYERS.get(name)
            if not fn:
                continue
            try:
                built.append(fn(bbox=bbox, block=block))
            except Exception:
                continue
        if not built:
            return None, "figure unavailable: no layer resolved for this extent"
        import h3
        lat, lng = h3.cell_to_latlng(cell_id)
        png = mapapi.render_png(built, bbox, width, height, title=title,
                               marker=(lng, lat),
                               attribution="Derived from MNDM MLAS and Ontario LIO "
                                           "data. (c) King's Printer for Ontario.")
    except Exception as e:
        return None, f"figure unavailable: {type(e).__name__}: {e}"
    return Figure(
        key=key, title=title, caption=caption,
        data_base64=base64.b64encode(png).decode("ascii"),
        provenance=_prov(f"mapapi:{'+'.join(layers)}", snapshot),
        generated_by="mapapi.render_png (C4.2)",
        # Derived from MLAS. Our own cartography, but of licence-gated data, so
        # it defaults to the restrictive value and the sales render checks it.
        redistribution="permission_required",
    ), None


def _land_section(cell_id: str, juris: str):
    import geopandas as gpd
    import pandas as pd
    from land import rules as R

    facts, tables, notes = [], {}, []
    hits = []
    for p in sorted((C.PROCESSED_DIR / "land_state").glob(f"{juris}__*.parquet")):
        d = gpd.read_parquet(p)
        row = d[d["cell_id"] == cell_id]
        if not row.empty:
            hits.append((p, row.iloc[0]))
    if not hits:
        return Section(key="land", title="Identity & land", available=False,
                       unavailable_reason=(
                           f"cell {cell_id} is not in any computed land_state AOI; "
                           f"run land.open_ground for an AOI containing it"))
    p, r = hits[0]
    snap = str(r.get("as_of_snapshot", "unknown"))
    src = f"land_state:{p.stem}"
    facts += [
        Fact(label="Cell (r9)", value=cell_id, provenance=_prov(src, snap)),
        Fact(label="Jurisdiction", value=juris, provenance=_prov(src, snap)),
        Fact(label="Land state", value=str(r["state"]),
             provenance=_prov(src, snap),
             note=("OPEN means nothing KNOWN blocks staking; see the "
                   "known-missing encumbrances note below")
             if r["state"] == "open" else None),
        Fact(label="Blocking layer", value=(str(r["blocking_layer"])
                                            if pd.notna(r["blocking_layer"]) else "none"),
             provenance=_prov(src, snap)),
        Fact(label="Area", value=round(float(r["area_km2"]), 4), unit="km²",
             provenance=_prov(src, snap)),
    ]
    meta_p = p.with_suffix(".json")
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
        for m in meta.get("missing_encumbrances", []):
            notes.append(f"KNOWN-MISSING ENCUMBRANCE: {m['type']} — {m['note']}")
        for pend in meta.get("pending_legal_review", []):
            notes.append(f"PENDING LEGAL REVIEW ({pend['features']} features): "
                         f"{pend['layer'].split('__')[-1]} — {pend['why']}")

    # Two different claims, and they stopped being the same thing when Ontario
    # was signed on 2026-08-21:
    #
    #   "here is what it costs to hold this"  needs the fee schedule attested
    #   "you may decide to stake this"        needs the WHOLE rules gate
    #
    # The fees are signed, so the cost is quotable. `stakeable_now` is still
    # False because duty-to-consult and exempt lands are unreviewed, so the
    # dossier still must not read as clearance to acquire.
    rules = R.load(juris)
    signed = R.is_verified(rules)
    ok, missing = R.stakeable_now(juris)
    if signed:
        sched = R.holding_schedule(juris, 1, 5)
        facts.append(Fact(
            label="5-year holding cost (1 cell)",
            value=sched["grand_total"], unit=sched["currency"],
            provenance=_prov(f"rules/{juris}.yaml",
                             str(rules.get("verified_date") or "verified")),
            note=f"Signed by {rules.get('verified_by')}. Registration, "
                 f"assessment work and licence only — this is the cost to HOLD, "
                 f"not an acquisition price."))
        mech = rules.get("expiry_mechanics") or {}
        if isinstance(mech, dict) and mech.get("reopening_delay_days") is not None:
            facts.append(Fact(
                label="Ground reopens after expiry",
                value=mech["reopening_delay_days"], unit="days",
                provenance=_prov(f"rules/{juris}.yaml",
                                 str(rules.get("verified_date") or "verified")),
                note=f"Measured from {mech.get('reopening_delay_measured_from')}; "
                     f"grace period {mech.get('grace_period_days')} days; opens "
                     f"{mech.get('reopens_at_local_time')}."))
            rf = mech.get("relief_from_forfeiture") or {}
            if rf.get("exists"):
                notes.append(
                    "NOT CLEAN TITLE ON DAY ONE — a forfeited claim can be "
                    "reinstated to its status at the time of forfeiture. "
                    "Registering on the cells bars the Minister's route, but the "
                    "Recorder's route survives a new registration where the "
                    "forfeiture came from a Crown administrative error, and our "
                    "claim may then carry terms or go to the Mining and Lands "
                    "Tribunal (Mining Act s.49(1)).")
    if not ok:
        notes.append(
            "NOT CLEARED FOR A STAKING DECISION. rules/" + juris + ".yaml is "
            "signed for the fee schedule and forfeiture timing, but "
            "stakeable_now() is still False on: " + "; ".join(missing) +
            ". The holding cost above is real; clearance to acquire is not.")
    else:
        # The gate opened on assumptions, not on citations. Say which, and say
        # it here rather than in the rules file where nobody reading a dossier
        # will look. A cleared gate that hides what cleared it is worse than a
        # shut one.
        scope = rules.get("assumptions_scope") or []
        if scope:
            rn = rules.get("notes") or {}
            notes.append(
                "CLEARED ON ASSUMPTIONS, NOT CITATIONS. "
                f"{rules.get('assumptions_accepted_by')} accepted on "
                f"{rules.get('assumptions_accepted_date')} that these fields "
                f"should not block: " + ", ".join(scope) + ". They are "
                "defensible readings that no qualified person has checked.")
            for f in scope:
                txt = (rn.get(f) or "").strip()
                if txt:
                    notes.append(f"ASSUMED — {f}: {txt}")
    figures = []
    fig, why = _figure(
        "land_inset", f"{cell_id} - land context",
        "The target cell (red marker) against current land state and the "
        "claim register. Green is open ground, red is claimed; the grid is "
        "individual cell claims.",
        cell_id, ("context", "land", "claims"), pad=8.0, snapshot=snap)
    if fig:
        figures.append(fig)
    else:
        notes.append(f"MAP INSET NOT RENDERED - {why}")
    return Section(key="land", title="Identity & land", facts=facts,
                   tables=tables, figures=figures, notes=notes)


def _activity_section(cell_id: str, juris: str):
    import h3
    import pandas as pd
    p = C.PROCESSED_DIR / "heat.parquet"
    if not p.exists():
        return Section(key="activity", title="Activity", available=False,
                       unavailable_reason="heat.parquet not built (C1.3)")
    parent = h3.cell_to_parent(cell_id, 7)
    h = pd.read_parquet(p)
    h = h[(h["juris"] == juris) & (h["cell_r7"] == parent)].sort_values("quarter")
    if h.empty:
        return Section(key="activity", title="Activity", available=False,
                       unavailable_reason=(
                           f"no staking events recorded in r7 cell {parent} — the "
                           f"ground has no observed tenure activity, which is "
                           f"information, not absence of data"))
    snap = str(h["quarter"].iloc[-1])
    src = "heat.parquet"
    last8 = h.tail(8)
    facts = [
        Fact(label="r7 cell", value=parent, provenance=_prov(src, snap)),
        Fact(label="Quarters observed", value=int(len(h)), provenance=_prov(src, snap)),
        Fact(label="Ever staked (episodes)", value=int(h["ever_staked_count"].iloc[-1]),
             provenance=_prov(src, snap),
             note="WEAK POSITIVE PRIOR only (Master §2): repeated staking means "
                  "repeated independent hypotheses, never evidence of geology"),
        Fact(label="Heat (cross-sectional, latest)",
             value=round(float(last8["heat_cross_smoothed"].iloc[-1]), 2),
             provenance=_prov(src, snap)),
    ]
    tables = {"heat_by_quarter": [
        {"quarter": r.quarter, "staked": int(r.cells_staked),
         "expired": int(r.expiry_count), "new_owners": int(r.unique_new_owners),
         "heat_self": round(float(r.heat_self_smoothed), 2),
         "heat_cross": round(float(r.heat_cross_smoothed), 2)}
        for r in last8.itertuples(index=False)]}
    notes = ["Both normalisations are shown because they answer different "
             "questions: heat_self is 'unusual for this cell', heat_cross is "
             "'hot compared to everywhere'."]
    if bool(last8["survivorship_biased"].any()):
        notes.append("SURVIVORSHIP BIASED — this series omits dropped ground.")
    return Section(key="activity", title="Activity", facts=facts, tables=tables,
                   notes=notes)


def _neighbours_section(cell_id: str, juris: str):
    import duckdb
    p = C.PROCESSED_DIR / "ownership.duckdb"
    if not p.exists():
        return Section(key="neighbours", title="Neighbours", available=False,
                       unavailable_reason="ownership.duckdb not built (C1.4)")
    con = duckdb.connect(str(p), read_only=True)
    rows = con.execute("""
        SELECT f.block_id, o.name_raw AS owner, o.entity_type_guess,
               b.n_claims, b.area_ha, f.frontier_bearing
        FROM frontier f JOIN blocks b USING (block_id)
        JOIN owners o ON b.owner_id = o.owner_id
        WHERE f.open_cell_id = ? ORDER BY b.n_claims DESC LIMIT 10""",
        [cell_id]).fetchdf()
    con.close()
    if rows.empty:
        return Section(key="neighbours", title="Neighbours", available=False,
                       unavailable_reason=(
                           "no block has this cell on its open frontier — either "
                           "the cell is not adjacent to a mapped block, or the "
                           "frontier was computed for a different AOI"))
    src, snap = "ownership.duckdb", dt.date.today().isoformat()
    return Section(
        key="neighbours", title="Neighbours",
        facts=[Fact(label="Adjacent blocks", value=int(len(rows)),
                    provenance=_prov(src, snap))],
        tables={"adjacent_blocks": rows.to_dict("records")},
        notes=["Owners are entity-resolved conservatively: near-matches go to a "
               "human review queue and are never auto-merged, so two rows may be "
               "one corporate family."])


def _criticality_section(cell_id: str):
    import pandas as pd
    p = C.PROCESSED_DIR / "criticality.parquet"
    if not p.exists():
        return Section(key="criticality", title="Criticality", available=False,
                       unavailable_reason="criticality.parquet not built (C1.5)")
    c = pd.read_parquet(p)
    c = c[c["cell_id"] == cell_id].sort_values("score", ascending=False)
    if c.empty:
        return Section(key="criticality", title="Criticality", available=False,
                       unavailable_reason=(
                           "no neighbour block scores this cell as critical — it "
                           "is not on anyone's computed trend, gap or chokepoint"))
    src = "criticality.parquet"
    snap = str(c["computed_at"].iloc[0])[:10]
    top_block = str(c["block_id"].iloc[0])
    notes = ["Scores combine sub-scores by MAX with reason codes, never by "
             "weighted blending — every score states why it is what it is."]
    figures = []
    # PLAN_C4 4.1 section 4 asks for "a rendered corridor map figure". Wider
    # than the land inset: a trend corridor is a kilometres-long feature and a
    # frame tight on the cell shows none of it.
    fig, why = _figure(
        "criticality_corridor", f"{cell_id} - criticality corridor",
        f"Criticality scored on open cells around {top_block}. Brighter cells "
        f"sit further along the neighbour's computed trend; the red marker is "
        f"the target. A claim's own footprint carries no score by construction.",
        cell_id, ("context", "criticality", "blocks"), pad=30.0, snapshot=snap,
        block=top_block)
    if fig:
        figures.append(fig)
    else:
        notes.append(f"CORRIDOR FIGURE NOT RENDERED - {why}")
    return Section(
        key="criticality", title="Criticality",
        facts=[Fact(label="Best score", value=round(float(c["score"].max()), 3),
                    provenance=_prov(src, snap)),
               Fact(label="Trend source", value=str(c["trend_source"].iloc[0]),
                    provenance=_prov(src, snap),
                    note="'none' means no defensible trend was found; it is not "
                         "a guessed bearing")],
        tables={"per_block": [
            {"block_id": r.block_id, "score": round(float(r.score), 3),
             "trend": r.trend_source,
             "azimuth": r.trend_azimuth, "distance_m": r.distance_m,
             "reasons": ", ".join(json.loads(r.reason_codes))}
            for r in c.head(8).itertuples(index=False)]},
        figures=figures,
        notes=notes)


def _geology_section(cell_id: str, juris: str):
    import h3
    import pandas as pd
    models = sorted((C.PROCESSED_DIR / "models").glob("*/*/card.json"))
    if not models:
        return Section(key="geology", title="Geology & prospectivity",
                       available=False,
                       unavailable_reason="no trained model (C2.1)")
    card_p = models[-1]
    card = json.loads(card_p.read_text())
    scores_p = card_p.parent / "oof_scores.parquet"
    parent = h3.cell_to_parent(cell_id, 7)
    facts = [
        Fact(label="Model", value=f"{card['system']} {card_p.parent.name}",
             provenance=_prov(str(card_p), card["feature_snapshot"])),
        Fact(label="Blocked CV ROC AUC",
             value=round(card["validation"]["spatially_blocked"]["pooled"]["roc_auc"], 3),
             provenance=_prov(str(card_p), card["feature_snapshot"])),
        Fact(label="Leave-one-terrane-out ROC AUC",
             value=round(card["validation"]["leave_one_terrane_out"]["pooled"]["roc_auc"], 3),
             provenance=_prov(str(card_p), card["feature_snapshot"]),
             note="the honest transferability estimate; lower than the blocked "
                  "score, and that gap is the point"),
    ]
    notes = list(card.get("caveats", []))
    if scores_p.exists():
        s = pd.read_parquet(scores_p)
        row = s[s["cell_id"] == parent]
        if not row.empty:
            v = float(row["score"].iloc[0])
            pct = float((s["score"] < v).mean() * 100)
            facts.append(Fact(label="Prospectivity score (r7, out-of-fold)",
                              value=round(v, 4),
                              provenance=_prov(str(scores_p), card["feature_snapshot"]),
                              note=f"{pct:.1f}th percentile province-wide"))
        else:
            notes.append("This cell's r7 parent has no out-of-fold score.")
    return Section(key="geology", title="Geology & prospectivity", facts=facts,
                   notes=notes)


def _drilling_section(cell_id: str, radius_km: float = 2.0):
    import h3
    import numpy as np
    import pandas as pd
    p = C.PROCESSED_DIR / "negatives.parquet"
    if not p.exists():
        return Section(key="drilling", title="Drilling", available=False,
                       unavailable_reason="negatives.parquet not built (C2.4)")
    lat, lng = h3.cell_to_latlng(cell_id)
    n = pd.read_parquet(p)
    d = np.sqrt(((n["latitude"] - lat) * 111.0) ** 2 +
                ((n["longitude"] - lng) * 111.0 * np.cos(np.radians(lat))) ** 2)
    near = n[d <= radius_km].copy()
    near["distance_km"] = d[d <= radius_km].round(2)
    if near.empty:
        return Section(key="drilling", title="Drilling", available=False,
                       unavailable_reason=(
                           f"no tier-1 barren hole within {radius_km} km. This is "
                           f"NOT evidence of prospectivity — it means the ground "
                           f"is untested, or that nearby holes were excluded "
                           f"because an occurrence or follow-up drilling exists"))
    src, snap = "negatives.parquet", str(near["snapshot"].iloc[0])
    known = near[near["commodities_tested"].astype(str).str.strip() != ""]
    return Section(
        key="drilling", title="Drilling",
        facts=[Fact(label=f"Tier-1 barren holes within {radius_km} km",
                    value=int(len(near)), provenance=_prov(src, snap)),
               Fact(label="…with commodities recorded", value=int(len(known)),
                    provenance=_prov(src, snap),
                    note="a hole with unknown commodities tested is a WEAKER "
                         "negative: it constrains location and depth only")],
        tables={"holes": near.nsmallest(10, "distance_km")[
            ["hole_id", "distance_km", "depth_to_m", "commodities_tested",
             "year_drilled", "tier"]].to_dict("records")},
        notes=["A barren hole vetoes only the commodities it tested, over the "
               "interval it tested (Master §2). 'Barren for Au to 120 m' does "
               "not test a Li thesis."])


def _buyers_section(cell_id: str, juris: str):
    """Who would buy this cell, and what is actually known about them (C6.2).

    A buyer here is the owner of a block this cell sits on the frontier of. The
    section states capacity and timing as unknown rather than estimating them:
    both need figures that live inside filing PDFs, and a treasury number with no
    filing behind it is exactly the kind of value the provenance rule exists to
    keep out of a dossier.
    """
    import duckdb
    import pandas as pd
    bp = C.MARKET_DIR / "buyers.parquet"
    if not bp.exists():
        return Section(key="buyers", title="Buyers", available=False,
                       unavailable_reason="market/buyers.parquet not built (C6.2)")
    odb = C.PROCESSED_DIR / "ownership.duckdb"
    if not odb.exists():
        return Section(key="buyers", title="Buyers", available=False,
                       unavailable_reason="ownership.duckdb not built (C1.4)")
    con = duckdb.connect(str(odb), read_only=True)
    nb = con.execute("""
        SELECT DISTINCT b.owner_id, f.block_id
        FROM frontier f JOIN blocks b USING (block_id)
        WHERE f.open_cell_id = ?""", [cell_id]).fetchdf()
    con.close()
    if nb.empty:
        return Section(key="buyers", title="Buyers", available=False,
                       unavailable_reason=(
                           "this cell is not on any mapped block's frontier, so "
                           "it has no adjacent owner to sell to"))
    b = pd.read_parquet(bp)
    hit = b[b["owner_id"].isin(set(nb["owner_id"]))].sort_values(
        "buyer_propensity", ascending=False)
    if hit.empty:
        return Section(key="buyers", title="Buyers", available=False,
                       unavailable_reason=(
                           "the adjacent owners are not in the buyer graph — the "
                           "graph is seeded from criticality, so a neighbour with "
                           "no critical cell is not profiled"))
    snap = str(hit["profile_as_of"].iloc[0])
    src = "market/buyers.parquet"

    def n(v):
        return None if v is None or pd.isna(v) else int(v)

    rows = []
    for r in hit.head(6).itertuples(index=False):
        resolved = pd.notna(r.sedar_issuer_id) and r.sedar_issuer_id
        rows.append({
            "owner": r.name_raw,
            "sedar_issuer_id": r.sedar_issuer_id if resolved else None,
            "resolution": r.resolution_status,
            "claims_held": int(r.claims),
            "critical_cells": int(r.critical_cells),
            "pickups_from_others": int(r.pickup_claims),
            "pickup_counterparties": int(r.pickup_counterparties),
            "consolidator": bool(r.consolidator_flag),
            "material_change_24mo": n(r.n_material_change_24mo),
            "closed_financings_24mo": n(r.n_financing_closed_24mo),
            "news_per_quarter": (None if pd.isna(r.news_cadence_per_quarter)
                                 else float(r.news_cadence_per_quarter)),
            "propensity": float(r.buyer_propensity),
            "capacity": None,
            "timing": None,
        })
    top = hit.iloc[0]
    top_thin = str(top["resolution_status"]) == "resolved_but_profile_thin"
    facts = [
        Fact(label="Natural buyers", value=int(len(hit)),
             provenance=_prov(src, snap),
             note="owners of blocks this cell is on the frontier of"),
        Fact(label="Most likely buyer", value=str(top["name_raw"]),
             provenance=_prov(src, snap),
             note=(f"propensity {float(top['buyer_propensity']):.3f} = "
                   f"0.45 adjacency + 0.35 pickup history + 0.20 disclosure "
                   f"activity; each term is in the table") +
                  (" — WARNING: the captured SEDAR+ profile for this issuer is a "
                   "stub, so its disclosure term is understated and its filing "
                   "counts below mean 'not captured', not 'not filed'"
                   if top_thin else "")),
        Fact(label="Buyer capacity", value="UNKNOWN",
             provenance=_prov(src, snap),
             note="needs treasury from the issuer's most recent financial "
                  "statements; the filing index gives dates and types, not the "
                  "figures inside the PDF"),
        Fact(label="Buyer timing", value="UNKNOWN",
             provenance=_prov(src, snap),
             note="needs drill-program status from news release text; every news "
                  "release in the SEDAR+ index is titled 'News release - "
                  "English.pdf', so the index alone cannot supply it"),
    ]
    return Section(
        key="buyers", title="Buyers", facts=facts,
        tables={"candidate_buyers": rows},
        notes=[
            "'Pickups' counts current claims standing on ground a DIFFERENT party "
            "let go, matched on claim geometry. Self re-stakes and partner "
            "changes are excluded.",
            "Consolidator is the province-wide 90th percentile of pickup volume, "
            "not a fixed count — the median Ontario picker has taken 17 claims.",
            "An unresolved owner means its filings are not in the local corpus; "
            "it does not mean the company is inactive.",
        ])


def _history_section(cell_id: str, juris: str):
    """Dossier section 7 — the C5.1 due-diligence answers, with their citations.

    Reads the stored `answers.json` rather than running the question set: a
    dossier regeneration must reproduce the same answers, and re-querying a
    model would quietly change the document. Run
    `python -m dd.ingest --cell X && python -m dd.rag --cell X` to refresh."""
    tid = f"{juris}-{cell_id}"
    p = C.PROCESSED_DIR / "dd" / f"{tid}.answers.json"
    if not p.exists():
        return Section(
            key="history", title="History (reports)", available=False,
            unavailable_reason=(
                "no due-diligence corpus for this target. Build one with "
                f"`python -m dd.ingest --cell {cell_id}` (C3.4 fetch + C5.1 "
                f"ingest) then `python -m dd.rag --cell {cell_id}`"))
    d = json.loads(p.read_text())
    corpus = d.get("corpus", {})
    cov = d.get("coverage") or {}
    src = f"dd:{d.get('collection')}"
    snap = str(p.stat().st_mtime_ns)[:0] or dt.date.today().isoformat()

    facts = [
        Fact(label="Reports ingested", value=corpus.get("reports"),
             provenance=_prov(src, snap)),
        Fact(label="Pages", value=corpus.get("pages"), provenance=_prov(src, snap)),
        Fact(label="Chunks embedded", value=corpus.get("chunks"),
             provenance=_prov(src, snap)),
        Fact(label="Answered", value=f"{sum(1 for a in d['answers'] if a.get('found'))} of "
                                     f"{len(d['answers'])} questions",
             provenance=_prov(src, snap)),
    ]
    if cov.get("missing"):
        yr = cov.get("missing_year_range")
        facts.append(Fact(
            label="Reports NOT retrieved", value=cov["missing"],
            provenance=_prov(src, snap),
            note=("Ontario's blob store serves the pre-2000 scanned era; these "
                  + (f"are dated {yr[0]}-{yr[1]}" if yr else "could not be fetched")
                  + ". Their absence is a retrieval gap, not evidence that "
                    "nothing happened: "
                  + ", ".join(cov.get("missing_ids", [])[:8]))))

    rows = []
    for a in d["answers"]:
        rows.append({
            "question": a.get("key"),
            "answer": (a.get("answer") or "").replace("\n", " "),
            "citations": ", ".join(f"{c['report_id']} p.{c['page']}"
                                   for c in (a.get("citations") or [])) or "—",
            "quotes_unverified": a.get("quotes_unverified", 0),
        })

    notes = ["Every claim above is generated from the retrieved report text under "
             "a cite-or-say-not-found contract, and every citation is checked "
             "against the chunks actually retrieved — an invented report id or "
             "page is stripped before it reaches this page."]
    qbad = sum(a.get("quotes_unverified", 0) for a in d["answers"])
    qok = sum(a.get("quotes_verified", 0) for a in d["answers"])
    if qbad:
        notes.append(
            f"{qok} quoted figures were found verbatim on the page cited; "
            f"**{qbad} were not** and are flagged in the table. Those are "
            f"typically read off badly-OCR'd map or table pages. Check them "
            f"against the PDF before repeating them to anyone.")
    else:
        notes.append(f"All {qok} quoted figures were found verbatim on the page cited.")
    notes.append("Model: " + str(d.get("model")) + ". Answers are stored, not "
                 "regenerated, so this section is reproducible.")

    return Section(key="history", title="History (reports)", facts=facts,
                   tables={"due_diligence": rows}, notes=notes)


def _unavailable(key, title, reason):
    return Section(key=key, title=title, available=False,
                   unavailable_reason=reason)


def assemble(cell_id: str, juris: str = "ON", profile: str = "internal"):
    import fabric as F
    meta = json.loads((F.FABRIC_DIR / f"r7_{juris}.json").read_text())
    snaps = sorted((C.PROCESSED_DIR / "features" / meta["fabric_version"]).glob("*"))
    snapshot = snaps[-1].name if snaps else "none"

    sections = [
        _land_section(cell_id, juris),
        _activity_section(cell_id, juris),
        _neighbours_section(cell_id, juris),
        _criticality_section(cell_id),
        _buyers_section(cell_id, juris),
        _geology_section(cell_id, juris),
        _drilling_section(cell_id),
        _history_section(cell_id, juris),
        _unavailable("economics", "Economics",
                     "C6.2 buyer profiles are built (see Buyers), but pricing is "
                     "not: no comps database (6.1), no valuation model (6.4), and "
                     "rules/ON.yaml is unsigned so even the holding schedule "
                     "cannot be quoted"),
        _unavailable("recommendation", "Recommendation & signoff",
                     "a named buyer now exists (C6.2) but a price range does "
                     "not; with no comps and no valuation, any recommendation "
                     "would be an opinion dressed as an analysis"),
    ]

    gates = []
    for s in sections:
        for f in s.facts:
            for k, why in LICENCE_GATED.items():
                if k in f.provenance.source_id and why not in gates:
                    gates.append(why)
    # Land facts come from land_state, which is derived from MLAS.
    if juris == "ON":
        g = LICENCE_GATED["ON_MLAS"]
        if g not in gates:
            gates.append(g)

    if profile == "sales" and gates:
        raise PermissionError(
            "REFUSING to assemble a sales-profile dossier.\n\n" +
            "\n\n".join(gates) +
            "\n\nUntil written permission is on file, buyer-facing output must "
            "carry derived figures we generate rather than reproduced MNDM "
            "cartography, plus a Crown-copyright attribution block. Assemble "
            "with profile='internal' for decision-making use, which the licence "
            "does permit.")

    return Dossier(
        target_id=f"{juris}-{cell_id}",
        dossier_version="v1",
        fabric_version=meta["fabric_version"],
        feature_snapshot=snapshot,
        jurisdiction=juris,
        sections=sections,
        licence_gates=gates,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", required=True)
    ap.add_argument("--juris", default="ON")
    ap.add_argument("--profile", default="internal", choices=["internal", "sales"])
    ap.add_argument("--out")
    args = ap.parse_args()
    C.require_lake()
    try:
        d = assemble(args.cell, args.juris, args.profile)
    except PermissionError as e:
        print(f"\n{e}\n")
        sys.exit(2)
    avail = sum(1 for s in d.sections if s.available)
    print(f"  {d.target_id}  status={d.status}  "
          f"{avail}/{len(d.sections)} sections available")
    for s in d.sections:
        mark = "ok " if s.available else "N/A"
        detail = f"{len(s.facts)} facts" if s.available else s.unavailable_reason[:64]
        print(f"    [{mark}] {s.title:<28}{detail}")
    out = Path(args.out) if args.out else (
        C.PROCESSED_DIR / "dossiers" / d.target_id / f"{d.dossier_version}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(d.model_dump_json(indent=2), encoding="utf-8")
    print(f"  → {out}")


if __name__ == "__main__":
    main()
