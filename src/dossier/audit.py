#!/usr/bin/env python3
"""
audit.py — reproducibility, pinning and the decision log (C4.5).

PLAN_C4 4.5 asks for four things. Three are plumbing and one is the point.

  **Provenance drawer** — every rendered value already carries
  `(source_id, snapshot_date)`, enforced in the schema since C4.1. `provenance()`
  collects them into one view so a reader can see every source a dossier rests
  on without reading every fact.

  **Pinning** — a dossier must record `(fabric_version, feature_snapshot,
  model_versions[], screen_run_ids[])`. Without it "regenerate this" has no
  defined meaning, because the inputs move underneath it.

  **Reproducibility contract** — `regenerate(dossier_id, version)` must rebuild
  byte-comparable JSON from pinned inputs. `verify_reproducible()` does the
  rebuild and the diff, and reports WHICH fields moved rather than a bare
  false, because the useful answer is "the buyer's financing count changed",
  not "not reproducible".

  **The decision log** — `dossiers/decisions.parquet`. This is the point. Every
  approve/reject with its reason accumulates the only ground truth this system
  will ever have for calibrating the deal score (C6.6) and the models (C2):
  the plan is explicit that "the system's own decisions become training signal,
  but only through this logged, human-labeled path". A decision made in a chat
  window and not written here is a decision that never happened.

**On byte-comparability.** A dossier carries `generated_at`, and two runs a
second apart differ on it. So the contract is applied to the *substance*: the
comparison excludes fields that are timestamps of the generation itself, and
names them. Pretending a timestamp is a reproducibility failure would train
everyone to ignore the check.

Usage:
    python -m dossier.audit --provenance ON-892b968aac7ffff
    python -m dossier.audit --verify ON-892b968aac7ffff
    python -m dossier.audit --log ON-892b968aac7ffff --decision reject \\
        --by "Devlen M" --note "holding cost exceeds any plausible sale"
    python -m dossier.audit --decisions
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

DOSSIER_DIR = C.PROCESSED_DIR / "dossiers"
DECISIONS = DOSSIER_DIR / "decisions.parquet"

#: Excluded from the byte comparison: these describe the act of generating, not
#: the content. Named here rather than buried so the exclusion can be argued
#: with — anything else differing IS a reproducibility failure.
VOLATILE_FIELDS = ("generated_at",)

DECISIONS_SCHEMA = (
    "target_id", "dossier_version", "decision", "decided_by", "decided_at",
    "note", "fabric_version", "feature_snapshot", "model_versions",
    "screen_run_ids", "sections_available", "holding_cost", "dossier_sha256",
)

VALID_DECISIONS = ("approve", "reject", "defer", "staked", "sold", "dropped")


def _load(target_id: str, version: str = "v1") -> dict:
    p = DOSSIER_DIR / target_id / f"{version}.json"
    if not p.exists():
        raise FileNotFoundError(f"no dossier at {p}")
    return json.loads(p.read_text())


def _sha(obj: dict) -> str:
    import hashlib
    clean = {k: v for k, v in obj.items() if k not in VOLATILE_FIELDS}
    return hashlib.sha256(
        json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------

def pins(target_id: str, version: str = "v1") -> dict:
    """What this dossier was built from — the inputs a rebuild must match."""
    d = _load(target_id, version)
    models, screens = [], []
    for s in d.get("sections", []):
        for f in s.get("facts", []):
            src = (f.get("provenance") or {}).get("source_id", "")
            if src.startswith("models/"):
                models.append(src)
        for tbl in (s.get("tables") or {}).values():
            for row in tbl or []:
                rid = row.get("screen_run_id")
                if rid:
                    screens.append(rid)
    return {
        "target_id": d.get("target_id"),
        "dossier_version": d.get("dossier_version"),
        "fabric_version": d.get("fabric_version"),
        "feature_snapshot": d.get("feature_snapshot"),
        "model_versions": sorted(set(models)),
        "screen_run_ids": sorted(set(screens)),
        "status": d.get("status"),
        "dossier_sha256": _sha(d),
    }


def provenance(target_id: str, version: str = "v1") -> dict:
    """Every source the dossier rests on, with how many values each supports."""
    d = _load(target_id, version)
    by_source: dict = {}
    for s in d.get("sections", []):
        for f in s.get("facts", []):
            pr = f.get("provenance") or {}
            key = (pr.get("source_id"), pr.get("snapshot_date"))
            e = by_source.setdefault(key, {"source_id": key[0],
                                           "snapshot_date": key[1],
                                           "values": 0, "sections": set()})
            e["values"] += 1
            e["sections"].add(s.get("key"))
        for fig in (s.get("figures") or []):
            pr = fig.get("provenance") or {}
            key = (pr.get("source_id"), pr.get("snapshot_date"))
            e = by_source.setdefault(key, {"source_id": key[0],
                                           "snapshot_date": key[1],
                                           "values": 0, "sections": set()})
            e["values"] += 1
            e["sections"].add(s.get("key"))
    rows = []
    for e in by_source.values():
        e["sections"] = sorted(e["sections"])
        rows.append(e)
    rows.sort(key=lambda r: (-r["values"], str(r["source_id"])))
    return {"target_id": target_id, "sources": rows,
            "n_sources": len(rows),
            "licence_gates": d.get("licence_gates", []),
            "pins": pins(target_id, version)}


def verify_reproducible(target_id: str, version: str = "v1",
                        quiet: bool = False) -> dict:
    """Rebuild from pinned inputs and diff against what is on disk."""
    from dossier import assemble as A

    before = _load(target_id, version)
    cell = before.get("target_id", "").split("-", 1)[-1]
    juris = before.get("jurisdiction", "ON")
    try:
        rebuilt = A.assemble(cell, juris).model_dump()
    except Exception as e:                                      # noqa: BLE001
        return {"target_id": target_id, "reproducible": False,
                "error": f"{type(e).__name__}: {e}"}

    same = _sha(before) == _sha(rebuilt)
    drift = [] if same else _drift(before, rebuilt)
    out = {"target_id": target_id, "version": version,
           "reproducible": same,
           "excluded_from_comparison": list(VOLATILE_FIELDS),
           "drift": drift,
           "sha_before": _sha(before), "sha_after": _sha(rebuilt)}
    if not quiet:
        print(f"  {target_id} reproducible: {same}")
        if drift:
            print(f"  {len(drift)} field(s) moved:")
            for d in drift[:12]:
                print(f"    {d}")
    return out


def _drift(a: dict, b: dict) -> list:
    """Which sections and facts differ — the useful form of 'not reproducible'."""
    out = []
    sa = {s["key"]: s for s in a.get("sections", [])}
    sb = {s["key"]: s for s in b.get("sections", [])}
    for k in sorted(set(sa) | set(sb)):
        if k not in sa:
            out.append(f"section {k}: added")
            continue
        if k not in sb:
            out.append(f"section {k}: removed")
            continue
        x, y = sa[k], sb[k]
        if x.get("available") != y.get("available"):
            out.append(f"section {k}: available "
                       f"{x.get('available')} → {y.get('available')}")
        fx = {f["label"]: f.get("value") for f in x.get("facts", [])}
        fy = {f["label"]: f.get("value") for f in y.get("facts", [])}
        for lbl in sorted(set(fx) | set(fy)):
            if fx.get(lbl) != fy.get(lbl):
                out.append(f"{k}.{lbl}: {fx.get(lbl)!r} → {fy.get(lbl)!r}")
    return out


# ---------------------------------------------------------------------------
# The decision log
# ---------------------------------------------------------------------------

def log_decision(target_id: str, decision: str, decided_by: str,
                 note: str = "", version: str = "v1") -> dict:
    """Append a human decision. The only path by which outcomes become signal.

    Appends, never updates: a target rejected in August and staked in November
    is two facts, and the first is the one that calibrates a model. Overwriting
    it would erase the disagreement, which is the whole training signal."""
    import pandas as pd

    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {VALID_DECISIONS}")
    if not decided_by or decided_by.strip().lower() in ("", "human", "system"):
        raise ValueError(
            "decided_by must be a person's name. The decision log is ground "
            "truth for calibrating the deal score; an anonymous row cannot be "
            "weighed against the person's later record.")

    d = _load(target_id, version)
    p = pins(target_id, version)
    hold = None
    for s in d.get("sections", []):
        if s.get("key") == "economics":
            for f in s.get("facts", []):
                if "holding cost" in (f.get("label") or "").lower():
                    hold = f.get("value")

    row = {
        "target_id": target_id, "dossier_version": version,
        "decision": decision, "decided_by": decided_by.strip(),
        "decided_at": dt.datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "fabric_version": p["fabric_version"],
        "feature_snapshot": p["feature_snapshot"],
        "model_versions": json.dumps(p["model_versions"]),
        "screen_run_ids": json.dumps(p["screen_run_ids"]),
        "sections_available": sum(1 for s in d.get("sections", [])
                                  if s.get("available")),
        "holding_cost": hold,
        "dossier_sha256": p["dossier_sha256"],
    }
    DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([row])
    if DECISIONS.exists():
        df = pd.concat([pd.read_parquet(DECISIONS), df], ignore_index=True)
    df.to_parquet(DECISIONS, index=False)
    return row


def decisions() -> "object":
    import pandas as pd
    if not DECISIONS.exists():
        return pd.DataFrame(columns=list(DECISIONS_SCHEMA))
    return pd.read_parquet(DECISIONS)


def main():
    ap = argparse.ArgumentParser(description="C4.5 — audit affordances")
    ap.add_argument("--provenance", metavar="TARGET")
    ap.add_argument("--verify", metavar="TARGET")
    ap.add_argument("--pins", metavar="TARGET")
    ap.add_argument("--log", metavar="TARGET")
    ap.add_argument("--decision", choices=VALID_DECISIONS)
    ap.add_argument("--by")
    ap.add_argument("--note", default="")
    ap.add_argument("--decisions", action="store_true")
    ap.add_argument("--version", default="v1")
    args = ap.parse_args()
    C.require_lake()

    if args.provenance:
        pr = provenance(args.provenance, args.version)
        print(f"\n  {pr['target_id']} rests on {pr['n_sources']} source(s)\n")
        for s in pr["sources"]:
            print(f"    {str(s['source_id'])[:52]:54s} "
                  f"{str(s['snapshot_date'])[:10]:12s} "
                  f"{s['values']:3d} value(s)  {','.join(s['sections'])}")
        print(f"\n  pinned: fabric {pr['pins']['fabric_version']} · "
              f"features {pr['pins']['feature_snapshot']}")
        if pr["pins"]["model_versions"]:
            print(f"  models: {', '.join(pr['pins']['model_versions'])}")
        for g in pr["licence_gates"]:
            print(f"\n  LICENCE: {g[:150]}")
    elif args.pins:
        print(json.dumps(pins(args.pins, args.version), indent=1))
    elif args.verify:
        verify_reproducible(args.verify, args.version)
    elif args.log:
        if not (args.decision and args.by):
            sys.exit("--log needs --decision and --by")
        row = log_decision(args.log, args.decision, args.by, args.note,
                           args.version)
        print(f"  logged: {row['target_id']} {row['decision']} "
              f"by {row['decided_by']} at {row['decided_at']}")
        print(f"  → {DECISIONS}")
    elif args.decisions:
        df = decisions()
        if df.empty:
            print("  no decisions logged yet. This is the ground truth C6.6 "
                  "will calibrate against — it starts accumulating the first "
                  "time someone signs off on a dossier.")
            return
        print(df[["target_id", "decision", "decided_by", "decided_at",
                  "note"]].to_string(index=False))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
