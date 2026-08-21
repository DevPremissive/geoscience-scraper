#!/usr/bin/env python3
"""
holding.py — holding cost, credit position and carry (C6.5).

PLAN_C6 6.5 wants three things over `rules/<juris>.yaml`: a year-by-year
holding schedule per claim block, the banked-credit position a buyer would
inherit, and the seller-side cost of carrying a target while marketing it —
"the denominator of every deal decision and the input to walk-away timing".

The arithmetic already existed in `land/rules.compute_schedule`, unit-tested and
refusing to run against an unverified jurisdiction. This module does not
reimplement it; it applies it to real blocks and answers the two questions the
rules file cannot.

**Ontario does not publish banked credit balances, and that is the answer to a
question the plan left open.** PLAN_C6 6.5 flags it: "data question per
jurisdiction — resolve during C1.2 verification: which registries expose credit
balances on claim records vs require account access". Resolved for Ontario:
`Operational_Cell_Claims` carries `TENURE_NUM, TITLE_TYPE, TENURE_STA,
ISSUE_DATE, ANNIVERSAR, EXTENSION_, CLAIM_DUE_, HOLDER` and no credit field of
any kind. Balances live behind an MLAS account. So `credit_position()` reports
`banked_credits: None` with `missing_because`, exactly as C6.2 does for treasury.

What it CAN report is a different quantity, and the difference is the point:
**`historical_work_value`** — the assessment work approved on ground
intersecting the block, from `ON_OMEIS_TECHFILE.VALUE_WORK` (22,590 reports,
$3.17 bn province-wide). That is evidence of what was spent, not runway a buyer
inherits. Credits are consumed by the annual requirement as they are applied;
work value never decreases. Adding historical work value to a valuation as if it
were banked credit would overstate the position, so the two are returned as
separate fields and 6.4 is told which is which.

Usage:
    python -m market.holding --block ON-B001965
    python -m market.holding --cell 892b968aac7ffff --years 5
    python -m market.holding --carry ON-B001965
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from land import rules as R

OWNERSHIP_DB = C.PROCESSED_DIR / "ownership.duckdb"

#: `blocks.geometry_wkt` is EPSG:3978, not lon/lat (audit M1). Every spatial
#: query against it transforms explicitly.
BLOCKS_CRS = "EPSG:3978"

#: Seller-side horizons. PLAN_C6 6.5: "my cost to carry a target for 1/2/3 years
#: while marketing it".
CARRY_YEARS = (1, 2, 3)


def _ownership():
    import duckdb
    if not OWNERSHIP_DB.exists():
        raise FileNotFoundError(
            "ownership.duckdb missing — run land/ownership_graph.py (C1.4)")
    con = duckdb.connect(str(OWNERSHIP_DB), read_only=True)
    try:
        con.execute("LOAD spatial")
    except Exception:                                           # noqa: BLE001
        con.execute("INSTALL spatial; LOAD spatial")
    return con


def block_info(block_id: str) -> dict | None:
    con = _ownership()
    try:
        row = con.execute(
            "SELECT block_id, owner_id, owner_norm, n_claims, area_ha, "
            "CAST(first_seen AS VARCHAR), CAST(last_change AS VARCHAR), "
            "geometry_wkt FROM blocks WHERE block_id = ?", [block_id]).fetchone()
    finally:
        con.close()
    if not row:
        return None
    keys = ("block_id", "owner_id", "owner_norm", "n_claims", "area_ha",
            "first_seen", "last_change", "geometry_wkt")
    return dict(zip(keys, row))


# ---------------------------------------------------------------------------
# Holding schedule
# ---------------------------------------------------------------------------

def holding_schedule(block_id: str, years: int = 5, juris: str = "ON") -> dict:
    """Year-by-year obligation for a real block.

    Delegates the arithmetic to `rules.holding_schedule`, which refuses on an
    unverified jurisdiction — that refusal is the point and is not bypassed
    here."""
    b = block_info(block_id)
    if b is None:
        raise KeyError(f"no block {block_id!r} in ownership.duckdb")
    n = int(b["n_claims"] or 0)
    sched = R.holding_schedule(juris, n, years)
    rules = R.load(juris)
    return {
        "block_id": block_id,
        "owner": b["owner_norm"],
        "n_claims": n,
        "area_ha": round(float(b["area_ha"] or 0), 1),
        "years": years,
        "currency": sched.get("currency"),
        "grand_total": sched.get("grand_total"),
        "per_year": sched.get("per_year") or sched.get("rows"),
        "schedule": sched,
        "rules_signed_by": rules.get("verified_by"),
        "rules_signed_date": str(rules.get("verified_date")),
        "assumptions_in_play": rules.get("assumptions_scope") or [],
    }


# ---------------------------------------------------------------------------
# Credit position
# ---------------------------------------------------------------------------

def credit_position(block_id: str, juris: str = "ON") -> dict:
    """What a buyer inherits, and what we cannot see.

    Two fields that must not be confused:

      `banked_credits`        — assessment credit currently applied or in
                                reserve. This is the runway. **Not published by
                                Ontario in bulk.**
      `historical_work_value` — assessment work approved on this ground, ever.
                                Evidence of spend, not runway. Never decreases;
                                credits do.
    """
    import duckdb

    b = block_info(block_id)
    if b is None:
        raise KeyError(f"no block {block_id!r} in ownership.duckdb")
    rules = R.load(juris)
    transferable = ((rules.get("credit_banking") or {})
                    .get("transferable_with_claim"))

    # Assessment work approved on ground intersecting this block.
    work = {"reports": 0, "value": None, "year_range": None}
    try:
        con = duckdb.connect(str(C.CATALOG_DB), read_only=True)
        con.execute("LOAD spatial")
        geom = "ST_GeomFromText(?)"
        row = con.execute(f'''
            SELECT count(*), sum("VALUE_WORK"),
                   min("YEAR_FROM"), max(COALESCE("YEAR_TO", "YEAR_FROM"))
            FROM geo_ON__ON_OMEIS_TECHFILE
            WHERE ST_Intersects(
                    geom,
                    ST_Transform({geom}, '{BLOCKS_CRS}', 'EPSG:4326',
                                 always_xy := true))''',
            [b["geometry_wkt"]]).fetchone()
        con.close()
        if row:
            work = {"reports": int(row[0] or 0),
                    "value": float(row[1]) if row[1] else None,
                    "year_range": ([int(row[2]), int(row[3])]
                                   if row[2] and row[3] else None)}
    except Exception as e:                                      # noqa: BLE001
        work["error"] = f"{type(e).__name__}: {e}"

    annual = _annual_obligation(rules, int(b["n_claims"] or 0))
    return {
        "block_id": block_id,
        "owner": b["owner_norm"],
        "n_claims": int(b["n_claims"] or 0),

        # The runway. Not available for Ontario.
        "banked_credits": None,
        "years_of_obligation_covered": None,
        "missing_because": {
            "banked_credits":
                "Ontario publishes no credit balance in the bulk tenure "
                "register — Operational_Cell_Claims carries no credit field of "
                "any kind. Balances require an MLAS account. Resolving PLAN_C6 "
                "6.5's open data question for ON: account access, not bulk.",
            "years_of_obligation_covered": "derived from banked_credits",
        },
        "transferable_with_claim": transferable,
        "transferable_basis": (
            "ASSUMPTION recorded in rules/ON.yaml, not a citation — and the one "
            "worth checking first, because it lands straight in a price."),

        # Evidence of spend. A DIFFERENT quantity — do not add it as runway.
        "historical_work_value": work["value"],
        "historical_work_reports": work["reports"],
        "historical_work_years": work["year_range"],
        "historical_work_note": (
            "Assessment work APPROVED on ground intersecting this block "
            "(ON_OMEIS_TECHFILE.VALUE_WORK). Evidence of what was spent, not "
            "runway a buyer inherits: credits are consumed as they are applied, "
            "work value never decreases. C6.4 must not add this at face value "
            "the way PLAN_C6 6.5 says banked credits may be added."),
        "annual_obligation": annual,
    }


def _annual_obligation(rules: dict, n_claims: int) -> float | None:
    """One year's work requirement for n claims, year-1 band."""
    bands = rules.get("work_requirement") or []
    for b in bands:
        spec = str(b.get("years", ""))
        lo = int(spec.split("-")[0]) if spec and spec[0].isdigit() else None
        if lo == 1:
            amt = b.get("amount_per_unit")
            return float(amt) * n_claims if amt is not None else None
    return None


# ---------------------------------------------------------------------------
# Seller-side carry
# ---------------------------------------------------------------------------

def carry_cost(block_id: str, years=CARRY_YEARS, juris: str = "ON") -> dict:
    """What it costs US to hold this while we market it.

    PLAN_C6 6.5 calls this "the denominator of every deal decision and the input
    to walk-away timing". Reported per horizon rather than as one number,
    because the decision it informs is *how long* to hold out."""
    out = {"block_id": block_id, "horizons": {}}
    for y in years:
        s = holding_schedule(block_id, y, juris)
        out["horizons"][y] = {
            "total": s["grand_total"],
            "currency": s["currency"],
            "per_claim": (round(s["grand_total"] / s["n_claims"], 2)
                          if s["n_claims"] else None),
        }
    out["n_claims"] = holding_schedule(block_id, 1, juris)["n_claims"]
    out["note"] = (
        "Cost to HOLD only — registration, assessment work and licence. It "
        "excludes the acquisition cost, and it excludes consultation and permit "
        "costs, which rules/ON.yaml records as attaching to exploration ACTIVITY "
        "rather than to registration. A work programme is not budgeted here.")
    return out


def for_cell(cell_id: str, juris: str = "ON", years: int = 5) -> dict:
    """Holding view for the block nearest a target cell — the C4 section-8 shape."""
    import duckdb
    import h3

    lat, lng = h3.cell_to_latlng(cell_id)
    con = _ownership()
    try:
        row = con.execute(f'''
            SELECT block_id FROM blocks
            WHERE juris = ?
            ORDER BY ST_Distance(
                ST_GeomFromText(geometry_wkt),
                ST_Transform(ST_Point({lng},{lat}), 'EPSG:4326',
                             '{BLOCKS_CRS}', always_xy := true)) ASC
            LIMIT 1''', [juris]).fetchone()
    finally:
        con.close()
    if not row:
        return {"cell_id": cell_id, "error": "no block found"}
    bid = row[0]
    return {"cell_id": cell_id, "nearest_block": bid,
            "holding": holding_schedule(bid, years, juris),
            "credits": credit_position(bid, juris),
            "carry": carry_cost(bid, juris=juris)}


def main():
    ap = argparse.ArgumentParser(description="C6.5 — holding cost and credits")
    ap.add_argument("--block")
    ap.add_argument("--cell")
    ap.add_argument("--carry", metavar="BLOCK")
    ap.add_argument("--juris", default="ON")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    C.require_lake()

    if args.carry:
        out = carry_cost(args.carry, juris=args.juris)
    elif args.block:
        out = {"holding": holding_schedule(args.block, args.years, args.juris),
               "credits": credit_position(args.block, args.juris)}
    elif args.cell:
        out = for_cell(args.cell, args.juris, args.years)
    else:
        ap.print_help()
        return

    if args.json:
        print(json.dumps(out, indent=1, default=str))
        return

    h = out.get("holding") or out
    if "grand_total" in h:
        print(f"\n  {h['block_id']}  {h['owner']}")
        print(f"  {h['n_claims']:,} claims · {h['area_ha']:,} ha")
        print(f"  {h['years']}-year holding cost: "
              f"{h['grand_total']:,.2f} {h['currency']}")
        print(f"  rules signed by {h['rules_signed_by']} on {h['rules_signed_date']}")
        if h.get("assumptions_in_play"):
            print(f"  cleared on assumptions: {', '.join(h['assumptions_in_play'])}")
    if "horizons" in out:
        print(f"\n  carry cost for {out['block_id']} ({out['n_claims']:,} claims)")
        for y, v in out["horizons"].items():
            print(f"    {y} year(s): {v['total']:>12,.2f} {v['currency']}"
                  f"   ({v['per_claim']:,.2f}/claim)")
        print(f"\n  {out['note']}")
    c = out.get("credits")
    if c:
        print(f"\n  credit position")
        print(f"    banked credits        : {c['banked_credits']} "
              f"— {c['missing_because']['banked_credits'][:60]}...")
        print(f"    transferable w/ claim : {c['transferable_with_claim']} "
              f"({c['transferable_basis'][:40]}...)")
        hv = c["historical_work_value"]
        print(f"    historical work value : "
              f"{('$%s' % format(hv, ',.0f')) if hv else 'none'} across "
              f"{c['historical_work_reports']} report(s) "
              f"{c['historical_work_years'] or ''}")
        print(f"    annual obligation     : "
              f"{c['annual_obligation']:,.2f}" if c["annual_obligation"] else "")


if __name__ == "__main__":
    main()
