#!/usr/bin/env python3
"""
validation.py — the validation protocol every model must go through (C2.5).

Built before the first model on purpose. Gap #8 in the register reads "No spatial
CV / PU protocol — results silently inflated — must exist before the first
model", and that is the failure mode this module exists to make impossible.

Four things, each with a reason:

  **Spatially blocked CV.** Mineral occurrences cluster, and geology is
  autocorrelated over tens of kilometres. A random split puts a cell and its
  neighbour on opposite sides of the fold boundary, so the model is scored on
  ground it effectively memorised. Blocks default to 50 km.

  **Leave-one-terrane-out.** The honest transferability estimate: can a model
  trained on the Superior province say anything about the Grenville? Note the
  Ontario fabric has only 3 terranes with one covering 83% of cells, so LOTO
  here is close to a 2-fold split — reported rather than hidden.

  **PU protocol.** Unlabelled is not negative (Master §2). Training treats
  unlabelled cells as unlabelled, and the class prior is swept rather than
  assumed, because the true prevalence of "prospective" ground is unknown and
  every headline metric moves with it.

  **Calibration.** Scores that reach C4 dossiers and C6 economics must be
  probabilities someone can reason about, not raw margins.

`random_split_scores()` exists and is deliberately awkward to use: random-split
results may be computed for curiosity but are **barred from model cards**.

Usage:
    python src/validation.py --demo ON
"""
from __future__ import annotations
import argparse, json, sys
import datetime as dt
from pathlib import Path

import config as C

#: Default spatial block edge, in metres. The plan asks for the empirical
#: autocorrelation range of key evidence layers; until a variogram is fitted this
#: is the documented fallback, and every model card records which was used.
DEFAULT_BLOCK_M = 50_000.0

#: Class priors swept on every run. The true prevalence of prospective ground is
#: unknown, so a single assumed prior would make the headline metric arbitrary.
PRIOR_SWEEP = (0.01, 0.02, 0.05, 0.10, 0.20)


def spatial_blocks(cells, block_m: float = DEFAULT_BLOCK_M):
    """Assign each cell to a square spatial block. Returns a fold label array."""
    import numpy as np
    m = cells.to_crs("EPSG:3978")
    cent = m.geometry.centroid
    bx = np.floor(cent.x.to_numpy() / block_m).astype(int)
    by = np.floor(cent.y.to_numpy() / block_m).astype(int)
    return np.array([f"b{x}_{y}" for x, y in zip(bx, by)])


def blocked_folds(block_ids, n_folds: int = 5, seed: int = 11):
    """Group blocks into folds so whole blocks move together."""
    import numpy as np
    rng = np.random.default_rng(seed)
    uniq = np.unique(block_ids)
    rng.shuffle(uniq)
    assign = {b: i % n_folds for i, b in enumerate(uniq)}
    return np.array([assign[b] for b in block_ids])


def terrane_folds(terranes):
    """Leave-one-terrane-out folds, one fold per terrane."""
    import numpy as np
    import pandas as pd
    t = pd.Series(terranes).fillna("__unknown__").to_numpy()
    uniq = {v: i for i, v in enumerate(sorted(set(t)))}
    return np.array([uniq[v] for v in t]), uniq


def _metrics(y_true, scores):
    """AUC, average precision, and lift at the top 1% / 5%."""
    import numpy as np
    from sklearn.metrics import roc_auc_score, average_precision_score
    out = {}
    try:
        out["roc_auc"] = float(roc_auc_score(y_true, scores))
        out["average_precision"] = float(average_precision_score(y_true, scores))
    except ValueError:
        out["roc_auc"] = out["average_precision"] = float("nan")
    base = float(np.mean(y_true)) or 1e-9
    for pct in (0.01, 0.05):
        k = max(1, int(len(scores) * pct))
        top = np.argsort(scores)[::-1][:k]
        out[f"lift_top{int(pct*100)}pct"] = float(np.mean(y_true[top]) / base)
    return out


def cross_validate(X, y, folds, model_fn, name: str):
    """Score a model across pre-assigned folds. Never shuffles across folds."""
    import numpy as np
    per_fold, oof = [], np.zeros(len(y), dtype=float)
    for f in sorted(set(folds)):
        te = folds == f
        tr = ~te
        if y[tr].sum() == 0 or y[te].sum() == 0:
            continue                       # a fold with no positives scores nothing
        m = model_fn()
        m.fit(X[tr], y[tr])
        s = m.predict_proba(X[te])[:, 1]
        oof[te] = s
        per_fold.append({"fold": str(f), "n_test": int(te.sum()),
                         "positives": int(y[te].sum()), **_metrics(y[te], s)})
    agg = _metrics(y, oof) if oof.any() else {}
    return {"design": name, "folds": per_fold, "pooled": agg}


def prior_sweep(y, scores, priors=PRIOR_SWEEP):
    """Precision at the top-k implied by each candidate class prior."""
    import numpy as np
    out = []
    order = np.argsort(scores)[::-1]
    for p in priors:
        k = max(1, int(len(scores) * p))
        top = order[:k]
        out.append({"prior": p, "k": int(k),
                    "precision_at_k": float(np.mean(y[top])),
                    "recall_at_k": float(y[top].sum() / max(1, y.sum()))})
    return out


def calibration_curve(y, scores, bins: int = 10):
    """Reliability curve — predicted vs observed, for the model card."""
    import numpy as np
    out = []
    edges = np.quantile(scores, np.linspace(0, 1, bins + 1))
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (scores >= lo) & (scores <= hi if i == bins - 1 else scores < hi)
        if not m.any():
            continue
        out.append({"bin": i, "n": int(m.sum()),
                    "mean_predicted": float(np.mean(scores[m])),
                    "observed_rate": float(np.mean(y[m]))})
    return out


def random_split_scores(X, y, model_fn, seed: int = 0):
    """Random-split score. BARRED FROM MODEL CARDS — see module docstring.

    Kept because the gap between this and the blocked score is itself the
    diagnostic: a large gap is the spatial leakage the blocked design exists to
    remove, and seeing it once is more convincing than being told about it.
    """
    import numpy as np
    from sklearn.model_selection import train_test_split
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25,
                                          random_state=seed, stratify=y)
    m = model_fn()
    m.fit(Xtr, ytr)
    s = m.predict_proba(Xte)[:, 1]
    r = _metrics(yte, s)
    r["_warning"] = ("RANDOM SPLIT — spatially leaky, barred from model cards; "
                     "for comparison against the blocked design only")
    return r


def model_card(system, juris, fabric_version, snapshot, feature_desc,
               blocked, loto, priors, calib, random_split=None, notes=None):
    """The record that travels with every model (Master §4 versioning rule)."""
    return {
        "system": system, "jurisdiction": juris,
        "fabric_version": fabric_version, "feature_snapshot": snapshot,
        "features": feature_desc,
        "built": dt.datetime.now().isoformat(timespec="seconds"),
        "validation": {
            "spatially_blocked": blocked,
            "leave_one_terrane_out": loto,
            "block_metres": DEFAULT_BLOCK_M,
            "block_source": "documented fallback; variogram not yet fitted",
        },
        "pu_prior_sweep": priors,
        "calibration": calib,
        "random_split_for_comparison_only": random_split,
        "caveats": notes or [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", metavar="JURIS")
    args = ap.parse_args()
    if args.demo:
        import numpy as np
        import fabric as F
        cells = F.load_r7(args.demo)
        b = spatial_blocks(cells)
        f = blocked_folds(b)
        t, uniq = terrane_folds(cells["terrane_id"])
        print(f"  {len(cells):,} cells")
        print(f"  spatial blocks at {DEFAULT_BLOCK_M/1000:.0f} km: "
              f"{len(set(b)):,} blocks → 5 folds "
              f"({np.bincount(f)} cells per fold)")
        print(f"  terranes: {len(uniq)} → {uniq}")
        if len(uniq) < 5:
            big = max(np.bincount(t)) / len(t)
            print(f"  ! leave-one-terrane-out has only {len(uniq)} folds and the "
                  f"largest holds {big*100:.0f}% of cells — weak transferability "
                  f"estimate, reported not hidden")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
