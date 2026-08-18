#!/usr/bin/env python3
"""
labels.py — positive labels per commodity system, on the r7 fabric (C2.1/C2.8).

Positives are known occurrences of a commodity system, assigned to the r7 cell
containing them. Everything else is **unlabelled, not negative** — Master §2 is
explicit that the only true negative is barren drilling, and that tenure history
is never a negative label. This module therefore emits positives and a count of
unlabelled cells, and never manufactures a negative class.

**System choice for Ontario, decided on the evidence.** PLAN_C2 2.1 says to start
with Zn-Pb MVT "solely because two published Canadian studies used it — giving an
external answer key". That reasoning is sound and the jurisdiction is wrong:

    Ontario occurrences    gold 7,011      zinc AND lead 765
    past/producing         gold   415      zinc AND lead  95

and Ontario is not an MVT province — Canadian MVT is Pine Point (NWT),
Nanisivik and Polaris (NU), Gays River (NS). Running MVT here would train on a
few hundred labels of a deposit type the province does not host, and the
published answer key would not apply to the result anyway.

So Phase 1 runs **orogenic Au**, which PLAN_C2 itself names as the second system
and describes as "business-relevant, label-rich". Zn-Pb MVT stays as the
validation system to run when an MVT jurisdiction is in scope, which is where
its external answer key has any force. `SYSTEMS` below is data, so switching is
a one-line change.

Usage:
    python src/labels.py --build ON --system orogenic_au
    python src/labels.py --systems
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

import config as C

LABEL_DIR = C.PROCESSED_DIR / "labels"

#: Commodity systems as data. `any` matches if ANY term appears; `all` requires
#: every term — which is what distinguishes a Zn-Pb system from an occurrence
#: that merely mentions zinc.
SYSTEMS = {
    "orogenic_au": {
        "any": ["gold"],
        "all": [],
        "note": "Ontario's dominant system and the label-rich one. Includes all "
                "gold occurrences; deposit-model filtering is not possible "
                "because the Ontario inventory carries no model field.",
    },
    "znpb_mvt": {
        "any": [],
        "all": ["zinc", "lead"],
        "note": "Retained as the external-answer-key system from the published "
                "Canadian studies. Only meaningful in an MVT jurisdiction "
                "(NWT/NU/NS); Ontario has 765 zinc+lead occurrences and is not "
                "an MVT province.",
    },
    "magmatic_ni_cu": {
        "any": ["nickel"],
        "all": [],
        "note": "Sudbury / Ring of Fire relevance.",
    },
    "lithium_peg": {
        "any": ["lithium"],
        "all": [],
        "note": "Small label set (177); included because it drove real 2023+ "
                "staking and is therefore business-relevant despite being thin.",
    },
}

#: Occurrence layers per jurisdiction, with their commodity columns.
OCCURRENCE_LAYERS = {
    "ON": [("ON__ON_OMEIS_MINERAL_INV",
            ["PRIMARY_COMMODITIES", "SECONDARY_COMMODITIES"], "STATUS")],
}

#: Statuses that indicate something was actually found and worked, used to
#: weight rather than to filter — a showing is still a positive.
DEVELOPED = ("Producing", "Past Producing", "Prospect", "Developed")


def build(juris: str = "ON", system: str = "orogenic_au", write: bool = True):
    import geopandas as gpd
    import h3
    import pandas as pd
    import fabric as F

    spec = SYSTEMS.get(system)
    if not spec:
        sys.exit(f"unknown system {system}; known: {sorted(SYSTEMS)}")
    layers = OCCURRENCE_LAYERS.get(juris)
    if not layers:
        sys.exit(f"no occurrence layer registered for {juris}")

    cells = F.load_r7(juris)
    meta = json.loads((F.FABRIC_DIR / f"r7_{juris}.json").read_text())
    cell_ids = set(cells["cell_id"])

    rows = []
    for layer, cols, status_col in layers:
        g = gpd.read_file(C.GPKG_PATH, layer=layer)
        have = [c for c in cols if c in g.columns]
        text = g[have].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        m = pd.Series(True, index=g.index)
        if spec["all"]:
            for t in spec["all"]:
                m &= text.str.contains(t, na=False)
        if spec["any"]:
            m &= text.str.contains("|".join(spec["any"]), na=False)
        hits = g[m].copy()
        if hits.empty:
            continue
        cent = hits.to_crs("EPSG:3978").geometry.centroid.to_crs(4326)
        hits["cell_r7"] = [h3.latlng_to_cell(p.y, p.x, 7) for p in cent]
        hits["developed"] = hits[status_col].astype(str).str.contains(
            "|".join(DEVELOPED), na=False) if status_col in hits.columns else False
        rows.append(hits[["cell_r7", "developed"]])
        print(f"   {layer.split('__')[-1]:<26}{len(hits):>7,} occurrences match "
              f"'{system}'")

    if not rows:
        sys.exit(f"no occurrences matched {system} in {juris}")
    occ = pd.concat(rows, ignore_index=True)
    occ = occ[occ["cell_r7"].isin(cell_ids)]

    lab = occ.groupby("cell_r7").agg(
        n_occurrences=("developed", "size"),
        n_developed=("developed", "sum")).reset_index()
    lab["label"] = 1
    lab["system"] = system
    lab["juris"] = juris
    lab["fabric_version"] = meta["fabric_version"]

    pos = len(lab)
    print(f"\n  {system}: {len(occ):,} occurrences → {pos:,} positive cells "
          f"({100*pos/len(cells):.2f}% of {len(cells):,})")
    print(f"  {int(lab['n_developed'].sum()):,} occurrences are developed "
          f"(producing/past-producing/prospect)")
    print(f"  UNLABELLED cells: {len(cells)-pos:,} — these are NOT negatives. "
          f"Master §2: the only true negative is barren drilling.")

    if write:
        LABEL_DIR.mkdir(parents=True, exist_ok=True)
        p = LABEL_DIR / f"{juris}_{system}_r7.parquet"
        lab.to_parquet(p, index=False, compression="zstd")
        print(f"  → {p}")
    return lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", metavar="JURIS")
    ap.add_argument("--system", default="orogenic_au")
    ap.add_argument("--systems", action="store_true")
    args = ap.parse_args()
    C.require_lake()
    if args.systems:
        for k, v in SYSTEMS.items():
            print(f"  {k:<16}any={v['any']} all={v['all']}\n      {v['note']}")
        return
    if args.build:
        build(args.build, args.system)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
