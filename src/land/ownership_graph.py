#!/usr/bin/env python3
"""
ownership_graph.py — who owns what, and what sits next to it (C1.4).

Builds `processed/ownership.duckdb` with four tables:

    owners       resolved entities, with the aliases folded into each
    blocks       connected components of one owner's touching claims
    adjacency    block-to-block proximity, with shared boundary length
    frontier     every open r9 cell on a block's perimeter, with its bearing

**Entity resolution deliberately does not auto-merge.** Normalisation is
mechanical — uppercase, strip punctuation and legal suffixes, collapse
whitespace, exact match. Anything short of an exact match after that goes to a
review queue for a human, even at 0.99 similarity.

The reason is concrete rather than cautious. C1.3's 2023Q2 rush split across
`KENORLAND EXPLORATION LTD` and `Kenorland Minerals North America Ltd.` — the
same corporate family, and they normalise to *different* strings because one is
"Minerals North America" and the other is not. Auto-merging on similarity would
also have merged genuinely distinct subsidiaries elsewhere, and an ownership
graph that silently fuses two companies produces a buyer analysis about a
company that does not exist. A human decides; the machine only proposes.

Usage:
    python -m land.ownership_graph --build ON
    python -m land.ownership_graph --review           # entity queue
    python -m land.ownership_graph --owner "KENORLAND"
"""
from __future__ import annotations
import argparse, json, math, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

DB_PATH = C.PROCESSED_DIR / "ownership.duckdb"

#: Claims of one owner within this distance are one block. 100 m per PLAN_C1 1.4,
#: applied as a 50 m buffer on each side so the gap closes symmetrically.
BLOCK_GAP_M = 100.0

#: Near-matches at or above this go to the review queue. Never auto-merged.
REVIEW_THRESHOLD = 0.92

#: Legal suffixes stripped during normalisation. Bilingual because Ontario
#: registers LTÉE and LIMITÉE alongside their English forms.
_SUFFIXES = r"(LTD|LTEE|LTÉE|LIMITED|LIMITEE|LIMITÉE|INC|INCORPORATED|CORP|" \
            r"CORPORATION|CO|COMPANY|LP|LLP|LLC|ULC|NL|PLC|SA|NV|GMBH)"
_SUFFIX_RE = re.compile(rf"\b{_SUFFIXES}\b\.?", re.I)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_NUMBERED_RE = re.compile(r"^\d{5,}\s")


def normalize(name: str) -> str:
    """Mechanical normalisation. Same string in, same string out — nothing fuzzy."""
    if not name:
        return ""
    s = name.upper()
    s = _PUNCT_RE.sub(" ", s)
    s = _SUFFIX_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def entity_type_guess(name: str) -> str:
    """Cheap classification — numbered companies are the residual C6.2 problem."""
    n = (name or "").strip()
    if _NUMBERED_RE.match(n) or re.match(r"^\(?\d{5,}\)?", n):
        return "numbered_company"
    if re.search(r"\b(LTD|INC|CORP|LIMITED|LTEE|LTÉE|ULC|LLC|LP)\b", n, re.I):
        return "corporation"
    if re.search(r"\b(FIRST NATION|BAND|COUNCIL)\b", n, re.I):
        return "first_nation"
    # Two or three capitalised words with no corporate marker is usually a person.
    if 1 < len(n.split()) <= 4:
        return "individual_or_unknown"
    return "unknown"


def jaro_winkler(a: str, b: str) -> float:
    """Jaro-Winkler similarity. Implemented here to avoid a dependency for ~30 lines."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    window = max(la, lb) // 2 - 1
    if window < 0:
        window = 0
    a_match = [False] * la
    b_match = [False] * lb
    matches = 0
    for i in range(la):
        lo, hi = max(0, i - window), min(i + window + 1, lb)
        for j in range(lo, hi):
            if b_match[j] or a[i] != b[j]:
                continue
            a_match[i] = b_match[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    k = trans = 0
    for i in range(la):
        if not a_match[i]:
            continue
        while not b_match[k]:
            k += 1
        if a[i] != b[k]:
            trans += 1
        k += 1
    trans //= 2
    jaro = (matches / la + matches / lb + (matches - trans) / matches) / 3
    prefix = 0
    for i in range(min(4, la, lb)):
        if a[i] != b[i]:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


# --------------------------------------------------------------------------

def _load_claims(juris: str):
    """Active claims with a parsed primary holder."""
    import geopandas as gpd
    import tenure_events as TE

    layer = {"ON": "ON__ON_MLAS_TENURE__Operational_Cell_Claims"}[juris]
    g = gpd.read_file(C.GPKG_PATH, layer=layer,
                      columns=["TENURE_NUM", "HOLDER", "ISSUE_DATE"])
    g["owner_raw"] = g["HOLDER"].map(TE.holder_name)
    g = g[g["owner_raw"].notna()].copy()
    g["owner_norm"] = g["owner_raw"].map(normalize)
    print(f"  {layer}: {len(g):,} claims, {g['owner_norm'].nunique():,} normalised owners")
    return g


def _historical_owners(juris: str):
    """Owners appearing only in the cancellation register.

    PLAN_C1 1.4 calls for historical ownership from Cancelled_Claim_Polygons
    (326,212 populated HOLDER values). Without it the entity table describes only
    who holds ground TODAY, and a company that sold or dropped everything
    vanishes — including `Kenorland Minerals North America`, which drove part of
    the 2023Q2 rush and holds no active claims at all. A buyer graph that cannot
    see a recent seller is missing half the market.
    """
    import geopandas as gpd
    import pandas as pd
    import tenure_events as TE
    if juris != "ON":
        return pd.DataFrame(columns=["owner_norm", "owner_raw", "n"])
    g = gpd.read_file(C.GPKG_PATH,
                      layer="ON__ON_MLAS_TENURE__Cancelled_Claim_Polygons",
                      columns=["HOLDER"], read_geometry=False)
    s = g["HOLDER"].map(TE.holder_name).dropna()
    df = pd.DataFrame({"owner_raw": s})
    df["owner_norm"] = df["owner_raw"].map(normalize)
    out = df.groupby(["owner_norm", "owner_raw"]).size().reset_index(name="n")
    print(f"  historical register: {out['owner_norm'].nunique():,} owners")
    return out


def build_owners(g, juris: str = "ON"):
    """One row per normalised entity, active and historical, with aliases."""
    import pandas as pd
    hist = _historical_owners(juris)
    hist_alias = {}
    hist_count = {}
    for r in hist.itertuples(index=False):
        hist_alias.setdefault(r.owner_norm, set()).add(r.owner_raw)
        hist_count[r.owner_norm] = hist_count.get(r.owner_norm, 0) + int(r.n)

    rows = []
    active = {n: grp for n, grp in g.groupby("owner_norm")}
    for norm in sorted(set(active) | set(hist_alias)):
        grp = active.get(norm)
        aliases = set(grp["owner_raw"].unique()) if grp is not None else set()
        aliases |= hist_alias.get(norm, set())
        aliases = sorted(aliases)
        rows.append({
            "owner_id": f"ON-{abs(hash(norm)) % (10**9):09d}",
            "name_raw": aliases[0],
            "name_normalized": norm,
            "entity_type_guess": entity_type_guess(aliases[0]),
            "sedar_issuer_id": None,          # joined by C6.2
            "aliases": json.dumps(aliases),
            "n_aliases": len(aliases),
            "claims": int(len(grp)) if grp is not None else 0,
            "historical_claims": int(hist_count.get(norm, 0)),
            "status": ("active" if grp is not None and norm in hist_alias else
                       "active_only" if grp is not None else "former_holder"),
        })
    return pd.DataFrame(rows)


#: A shared leading token this rare across the register is a family signal.
RARE_TOKEN_MAX_OWNERS = 5
RARE_TOKEN_MIN_LEN = 5


def _shared_token_candidates(names, types=None):
    """Pairs sharing a distinctive first word.

    Added because string similarity alone MISSED the one pair known to be real:
    `KENORLAND EXPLORATION` vs `KENORLAND MINERALS NORTH AMERICA` scores 0.851,
    below the 0.92 threshold, because the strings diverge completely after the
    first word. That is the normal shape of a corporate family — a shared
    distinctive name followed by a different division — and whole-string
    similarity is structurally blind to it.

    Restricted to tokens that are rare across the register and reasonably long,
    so "GOLD", "NORTH" and "LAKE" do not flood the queue — and to non-individuals,
    because the first run surfaced AARON/ALLAN/ANDRE pairs by the dozen. Two
    people sharing a forename are not a corporate family, and that noise alone
    pushed the queue from 19 rows to 252, past the acceptance target.
    """
    from collections import defaultdict
    types = types or {}
    first = defaultdict(list)
    for n in names:
        if types.get(n) == "individual_or_unknown":
            continue
        parts = n.split()
        if parts and len(parts[0]) >= RARE_TOKEN_MIN_LEN:
            first[parts[0]].append(n)
    out = []
    for tok, group in first.items():
        if not (1 < len(group) <= RARE_TOKEN_MAX_OWNERS):
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                out.append((group[i], group[j], tok))
    return out


def review_queue(owners):
    """Near-matches for a human to rule on. Nothing here is merged automatically."""
    import pandas as pd
    names = owners["name_normalized"].tolist()
    # Block on first letter to avoid an O(n^2) sweep over 1,400 names.
    buckets: dict = {}
    for n in names:
        buckets.setdefault(n[:1], []).append(n)
    rows = []
    for _k, group in buckets.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                s = jaro_winkler(a, b)
                if s >= REVIEW_THRESHOLD:
                    rows.append({"name_a": a, "name_b": b, "similarity": round(s, 4)})
    types = dict(zip(owners["name_normalized"], owners["entity_type_guess"]))
    for a, b, tok in _shared_token_candidates(names, types):
        rows.append({"name_a": a, "name_b": b,
                     "similarity": round(jaro_winkler(a, b), 4),
                     "reason": f"shared rare leading token: {tok}"})
    for r in rows:
        r.setdefault("reason", f"string similarity >= {REVIEW_THRESHOLD}")
    if not rows:
        return pd.DataFrame(columns=["name_a", "name_b", "similarity", "reason"])
    df = pd.DataFrame(rows).drop_duplicates(subset=["name_a", "name_b"])
    return df.sort_values("similarity", ascending=False)


def build_blocks(g, juris: str):
    """Connected components of one owner's claims, touching or within 100 m."""
    import geopandas as gpd
    import pandas as pd

    metric = g.to_crs("EPSG:3978")
    rows = []
    for norm, grp in metric.groupby("owner_norm"):
        # Buffer each claim by half the gap, dissolve, then explode: any two
        # claims within BLOCK_GAP_M end up in one part.
        merged = grp.geometry.buffer(BLOCK_GAP_M / 2).union_all()
        parts = list(getattr(merged, "geoms", [merged]))
        part_gdf = gpd.GeoDataFrame(geometry=parts, crs=metric.crs)
        joined = gpd.sjoin(grp[["TENURE_NUM", "ISSUE_DATE", "geometry"]],
                           part_gdf, predicate="intersects", how="left")
        for pid, sub in joined.groupby("index_right"):
            geom = parts[int(pid)].buffer(-BLOCK_GAP_M / 2)
            if geom.is_empty:
                continue
            issue = pd.to_datetime(sub["ISSUE_DATE"], errors="coerce")
            rows.append({
                "owner_norm": norm,
                "claim_ids": json.dumps(sub["TENURE_NUM"].astype(str).tolist()),
                "n_claims": int(len(sub)),
                "area_ha": float(geom.area / 10_000.0),
                "first_seen": issue.min(),
                "last_change": issue.max(),
                "geometry": geom,
            })
    blocks = gpd.GeoDataFrame(rows, crs=metric.crs)
    blocks["block_id"] = [f"{juris}-B{i:06d}" for i in range(len(blocks))]
    blocks["juris"] = juris
    print(f"  {len(blocks):,} blocks from {g['owner_norm'].nunique():,} owners "
          f"(largest {blocks['n_claims'].max():,} claims)")
    return blocks


def build_adjacency(blocks, max_km: float = 5.0, min_claims: int = 2):
    """Block-to-block proximity with shared boundary length.

    Restricted to blocks of `min_claims` or more: single-claim blocks are
    overwhelmingly individuals holding one cell, and including them turns a
    useful operator-adjacency graph into millions of uninformative pairs.
    """
    import geopandas as gpd
    import pandas as pd

    b = blocks[blocks["n_claims"] >= min_claims].copy()
    if b.empty:
        return pd.DataFrame(columns=["block_id_a", "block_id_b", "relation",
                                     "shared_boundary_m"])
    print(f"  adjacency over {len(b):,} blocks with >= {min_claims} claims")
    rows = []
    for label, dist in (("touching", 0.0), ("within_1km", 1000.0),
                        ("within_5km", max_km * 1000.0)):
        probe = b.copy()
        probe["geometry"] = b.geometry.buffer(dist) if dist else b.geometry
        pairs = gpd.sjoin(probe[["block_id", "geometry"]],
                          b[["block_id", "geometry"]],
                          predicate="intersects", how="inner")
        pairs = pairs[pairs["block_id_left"] < pairs["block_id_right"]]
        for r in pairs.itertuples(index=False):
            rows.append({"block_id_a": r.block_id_left,
                         "block_id_b": r.block_id_right,
                         "relation": label, "_d": dist})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Keep the tightest relation per pair — touching beats within_1km.
    order = {"touching": 0, "within_1km": 1, "within_5km": 2}
    df["_o"] = df["relation"].map(order)
    df = df.sort_values("_o").drop_duplicates(subset=["block_id_a", "block_id_b"])

    geom = dict(zip(b["block_id"], b.geometry))
    shared = []
    for r in df.itertuples(index=False):
        if r.relation == "touching":
            try:
                shared.append(float(geom[r.block_id_a].intersection(
                    geom[r.block_id_b].buffer(1.0)).length))
            except Exception:                                   # noqa: BLE001
                shared.append(0.0)
        else:
            shared.append(0.0)
    df["shared_boundary_m"] = shared
    return df.drop(columns=["_o", "_d"])


def build_frontier(blocks, aoi_id: str = "abitibi", juris: str = "ON"):
    """Open r9 cells on each block's perimeter, with bearing from the centroid.

    Consumed by C1.5 criticality: an open cell on the frontier in the direction
    of a trend is the ground a neighbour eventually needs.
    """
    import geopandas as gpd
    import pandas as pd

    state = C.PROCESSED_DIR / "land_state" / f"{juris}__{aoi_id}.parquet"
    if not state.exists():
        print(f"  no land_state for {aoi_id}; skipping frontier")
        return pd.DataFrame(columns=["block_id", "open_cell_id", "frontier_bearing"])
    cells = gpd.read_parquet(state)
    cells = cells[cells["state"] == "open"].set_crs(4326, allow_override=True)
    if cells.empty:
        return pd.DataFrame(columns=["block_id", "open_cell_id", "frontier_bearing"])

    b = blocks.to_crs(4326)
    b = b[b.geometry.intersects(cells.union_all().envelope)]
    if b.empty:
        print("  no blocks intersect the AOI; skipping frontier")
        return pd.DataFrame(columns=["block_id", "open_cell_id", "frontier_bearing"])

    cells_m = cells.to_crs("EPSG:3978")
    b_m = b.to_crs("EPSG:3978")
    ring = b_m.copy()
    ring["geometry"] = b_m.geometry.buffer(500.0)   # perimeter reach
    hit = gpd.sjoin(cells_m[["cell_id", "geometry"]], ring[["block_id", "geometry"]],
                    predicate="intersects", how="inner")
    cent = dict(zip(b_m["block_id"], b_m.geometry.centroid))
    rows = []
    for r in hit.itertuples(index=False):
        c = cent[r.block_id]
        p = r.geometry.centroid
        brg = (math.degrees(math.atan2(p.x - c.x, p.y - c.y)) + 360) % 360
        rows.append({"block_id": r.block_id, "open_cell_id": r.cell_id,
                     "frontier_bearing": round(brg, 1)})
    print(f"  frontier: {len(rows):,} (block, open cell) pairs in {aoi_id}")
    return pd.DataFrame(rows)


def build(juris: str = "ON", aoi_id: str = "abitibi"):
    import duckdb
    import pandas as pd

    g = _load_claims(juris)
    owners = build_owners(g, juris)
    queue = review_queue(owners)
    blocks = build_blocks(g, juris)
    owner_id = dict(zip(owners["name_normalized"], owners["owner_id"]))
    blocks["owner_id"] = blocks["owner_norm"].map(owner_id)
    adjacency = build_adjacency(blocks)
    frontier = build_frontier(blocks, aoi_id, juris)

    blocks_out = blocks.drop(columns=["geometry"]).copy()
    blocks_out["geometry_wkt"] = blocks.geometry.to_wkt()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE TABLE owners AS SELECT * FROM owners")
    con.execute("CREATE TABLE blocks AS SELECT * FROM blocks_out")
    con.execute("CREATE TABLE adjacency AS SELECT * FROM adjacency")
    con.execute("CREATE TABLE frontier AS SELECT * FROM frontier")
    con.execute("CREATE TABLE entity_review_queue AS SELECT * FROM queue")

    # Standard queries the plan asks to ship as views.
    con.execute("""
        CREATE VIEW multi_jurisdiction_owners AS
        SELECT o.owner_id, o.name_raw, COUNT(DISTINCT b.juris) AS jurisdictions,
               SUM(b.n_claims) AS claims
        FROM owners o JOIN blocks b USING (owner_id)
        GROUP BY 1, 2 HAVING COUNT(DISTINCT b.juris) >= 2""")
    con.execute("""
        CREATE VIEW block_summary AS
        SELECT b.block_id, o.name_raw AS owner, o.entity_type_guess,
               b.n_claims, b.area_ha, b.first_seen, b.last_change
        FROM blocks b JOIN owners o USING (owner_id)
        ORDER BY b.n_claims DESC""")
    con.close()

    print(f"\n  owners        {len(owners):,}")
    print(f"  blocks        {len(blocks):,}")
    print(f"  adjacency     {len(adjacency):,}")
    print(f"  frontier      {len(frontier):,}")
    print(f"  review queue  {len(queue):,}  "
          f"({'PASS' if len(queue) < 200 else 'OVER'} the <200 acceptance target)")
    print(f"  → {DB_PATH}")
    return owners, blocks, adjacency, queue


def _con():
    import duckdb
    if not DB_PATH.exists():
        sys.exit("no ownership.duckdb — run --build first")
    return duckdb.connect(str(DB_PATH), read_only=True)


def show_review(n: int = 40):
    con = _con()
    df = con.execute("SELECT * FROM entity_review_queue ORDER BY similarity DESC "
                     f"LIMIT {n}").fetchdf()
    print(f"  {len(df)} near-match pair(s) for human review "
          f"(nothing merged automatically):\n")
    for r in df.itertuples(index=False):
        print(f"  {r.similarity:.3f}  {r.name_a[:44]:<46}{r.name_b[:44]}")
    con.close()
    return df


def show_owner(pattern: str):
    con = _con()
    df = con.execute(
        "SELECT block_id, owner, n_claims, area_ha, first_seen, last_change "
        "FROM block_summary WHERE upper(owner) LIKE ? ORDER BY n_claims DESC LIMIT 25",
        [f"%{pattern.upper()}%"]).fetchdf()
    print(df.to_string(index=False) if len(df) else "  no match")
    con.close()
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", metavar="JURIS")
    ap.add_argument("--aoi", default="abitibi")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--owner", metavar="PATTERN")
    args = ap.parse_args()
    C.require_lake()
    if args.build:
        build(args.build, args.aoi)
    if args.review:
        show_review()
    if args.owner:
        show_owner(args.owner)
    if not any([args.build, args.review, args.owner]):
        ap.print_help()


if __name__ == "__main__":
    main()
