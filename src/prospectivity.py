#!/usr/bin/env python3
"""
prospectivity.py — the text→prospectivity model (C2.1), through C2.5's protocol.

Trains on the per-cell embeddings from `corpus.py` against the positive labels
from `labels.py`, and reports **only** spatially-blocked and
leave-one-terrane-out scores. Every run writes a model card to
`models/<system>/<version>/`.

Three things this deliberately does not do:

  * **It does not invent negatives.** Unlabelled cells are used as unlabelled
    (PU), and the class prior is swept rather than assumed, because the true
    prevalence of prospective ground is unknown and every headline number moves
    with it.
  * **It does not report a random split as a result.** That figure is computed
    once, labelled, and kept beside the blocked score purely so the leakage gap
    is visible.
  * **It does not claim a deposit-scale prediction.** Master §2: regional cell
    scale, shortlists for desktop diligence, never "stake this cell".

Usage:
    python src/prospectivity.py --train ON --system orogenic_au
    python src/prospectivity.py --card models/orogenic_au/<version>/card.json
"""
from __future__ import annotations
import argparse, json, sys
import datetime as dt
from pathlib import Path

import config as C
import validation as V

MODEL_DIR = C.PROCESSED_DIR / "models"


def _load(juris: str, system: str):
    import numpy as np
    import pandas as pd
    import fabric as F

    meta = json.loads((F.FABRIC_DIR / f"r7_{juris}.json").read_text())
    ver = meta["fabric_version"]
    feat_root = C.PROCESSED_DIR / "features" / ver
    snaps = sorted(p for p in feat_root.glob("*") if (p / "embeddings.parquet").exists())
    if not snaps:
        sys.exit(f"no embeddings under {feat_root} — run corpus.py --embed first")
    snap = snaps[-1]
    emb = pd.read_parquet(snap / "embeddings.parquet")

    lab_p = C.PROCESSED_DIR / "labels" / f"{juris}_{system}_r7.parquet"
    if not lab_p.exists():
        sys.exit(f"no labels — run labels.py --build {juris} --system {system}")
    lab = pd.read_parquet(lab_p)

    cells = F.load_r7(juris)
    df = cells[["cell_id", "terrane_id", "geometry"]].merge(emb, on="cell_id",
                                                            how="inner")
    df["label"] = df["cell_id"].isin(set(lab["cell_id"] if "cell_id" in lab.columns
                                         else lab["cell_r7"])).astype(int)
    print(f"  {len(df):,} cells with embeddings; {int(df['label'].sum()):,} positive "
          f"({100*df['label'].mean():.2f}%)")
    return df, ver, snap.name, meta


def train(juris: str = "ON", system: str = "orogenic_au", write: bool = True):
    import numpy as np
    import pandas as pd
    import geopandas as gpd
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    df, fabric_version, snapshot, meta = _load(juris, system)
    if df["label"].sum() < 50:
        sys.exit("too few positives to train honestly")

    emb_cols = [c for c in df.columns if c.startswith("textemb_")]
    X = df[emb_cols].to_numpy(dtype="float32")
    y = df["label"].to_numpy()
    cells = gpd.GeoDataFrame(df[["cell_id", "geometry"]], geometry="geometry",
                             crs=4326)

    def model_fn():
        # Matches the published setup the pathway is drawn from: a linear model
        # on embeddings. class_weight balances the 2% positive rate without
        # inventing negatives.
        return make_pipeline(
            StandardScaler(with_mean=True),
            LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0))

    print("\n  spatially blocked CV (50 km blocks)…")
    blocks = V.spatial_blocks(cells)
    folds = V.blocked_folds(blocks)
    blocked = V.cross_validate(X, y, folds, model_fn, "spatially_blocked_5fold")
    print(f"    ROC AUC {blocked['pooled'].get('roc_auc', float('nan')):.3f}  "
          f"AP {blocked['pooled'].get('average_precision', float('nan')):.3f}  "
          f"lift@1% {blocked['pooled'].get('lift_top1pct', float('nan')):.1f}x")

    print("  leave-one-terrane-out…")
    tfolds, uniq = V.terrane_folds(df["terrane_id"])
    loto = V.cross_validate(X, y, tfolds, model_fn, "leave_one_terrane_out")
    print(f"    ROC AUC {loto['pooled'].get('roc_auc', float('nan')):.3f}  "
          f"({len(uniq)} terranes)")

    print("  random split (comparison only, barred from the card headline)…")
    rnd = V.random_split_scores(X, y, model_fn)
    print(f"    ROC AUC {rnd['roc_auc']:.3f}   "
          f"gap vs blocked: {rnd['roc_auc']-blocked['pooled'].get('roc_auc',0):+.3f}")

    # Out-of-fold scores from the blocked design are the ones that ship.
    oof = np.zeros(len(y))
    for f in sorted(set(folds)):
        te = folds == f
        tr = ~te
        if y[tr].sum() == 0:
            continue
        m = model_fn(); m.fit(X[tr], y[tr])
        oof[te] = m.predict_proba(X[te])[:, 1]

    priors = V.prior_sweep(y, oof)
    calib = V.calibration_curve(y, oof)
    print("\n  PU class-prior sweep (precision at top-k):")
    for p in priors:
        print(f"    prior {p['prior']:.2f}  k={p['k']:>7,}  "
              f"precision {p['precision_at_k']:.3f}  recall {p['recall_at_k']:.3f}")

    card = V.model_card(
        system, juris, fabric_version, snapshot,
        {"kind": "text embeddings (mxbai 1024d, mean-pooled chunks)",
         "n_features": len(emb_cols), "model": "logistic regression, balanced"},
        blocked, loto, priors, calib, random_split=rnd,
        notes=[
            "Regional cell scale. Master §2 forbids deposit-scale claims: this "
            "produces shortlists for desktop diligence, never a stake-this-cell "
            "verdict.",
            "Unlabelled cells are unlabelled, not negative. The only true "
            "negative is barren drilling (C2.4).",
            f"Leave-one-terrane-out uses {len(uniq)} terranes with the largest "
            f"holding a large majority of cells — a weak transferability "
            f"estimate, reported rather than hidden.",
            "Corpus is provincial bedrock attribute text only. The CGMC legend "
            "and MRDS pillars named in PLAN_C2 2.1 do not exist as described "
            "(audit I6; MRDS is 87% US with 1,551 Canadian records).",
        ])

    if write:
        ver = dt.datetime.now().strftime("v%Y%m%dT%H%M%S")
        d = MODEL_DIR / system / ver
        d.mkdir(parents=True, exist_ok=True)
        (d / "card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
        scores = pd.DataFrame({"cell_id": df["cell_id"], "score": oof,
                               "label": y})
        scores.to_parquet(d / "oof_scores.parquet", index=False,
                          compression="zstd")
        print(f"\n  → {d}/card.json")
        print(f"  → {d}/oof_scores.parquet")
    return card


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", metavar="JURIS")
    ap.add_argument("--system", default="orogenic_au")
    args = ap.parse_args()
    C.require_lake()
    if args.train:
        train(args.train, args.system)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
