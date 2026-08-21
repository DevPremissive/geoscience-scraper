#!/usr/bin/env python3
"""
runner.py — execute a screen against a named snapshot and keep the run (C4.3).

PLAN_C4 4.3: "executes against a **named feature snapshot** (no implicit latest
— Master §4), persists `screens/<name>/<run_id>/` = {screen text, compiled SQL,
snapshot refs, result cells, run date}. Screens are therefore versioned,
diffable ('what entered/left this screen since last month' is a standing
report), and feed lapse-watch rule (c) in C1.6."

Two properties that only exist because of how the run is stored:

**Re-runnable identically.** The run directory holds the screen text, the exact
SQL, the fabric version, the snapshot name and the source path. `rerun()`
replays that SQL — not a recompilation of the text — so the acceptance
criterion "re-run identically on the same snapshot" is a byte comparison of the
result set rather than a hope about determinism.

**Diffable.** `diff()` answers "what entered and left this screen since last
month", which is the standing report the plan asks for, and is the thing that
turns a screen from a query into a monitor.

Usage:
    python -m screens.runner --run screens/library/nonbarren_open_ground.screen
    python -m screens.runner --list
    python -m screens.runner --show <name>
    python -m screens.runner --diff <name>
    python -m screens.runner --rerun <name>/<run_id>
    python -m screens.runner --acceptance
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from screens import compile as SC
from screens import dsl

RUNS_DIR = C.PROCESSED_DIR / "screens"
LIBRARY = Path(__file__).resolve().parent / "library"


def _run_id(sql: str, snapshot: str) -> str:
    h = hashlib.blake2b(f"{sql}|{snapshot}".encode(), digest_size=6).hexdigest()
    return f"{dt.date.today().isoformat()}-{h}"


def run(screen_text: str, fabric_version: str | None = None,
        snapshot: str | None = None, write: bool = True,
        quiet: bool = False) -> dict:
    """Parse, compile, execute, persist."""
    import duckdb
    import pandas as pd

    scr = dsl.parse(screen_text)
    comp = SC.compile_screen(scr, fabric_version, snapshot)
    rid = _run_id(comp["sql"], comp["snapshot"])

    con = duckdb.connect()
    try:
        con.execute("SET autoinstall_known_extensions=1; "
                    "SET autoload_known_extensions=1")
        try:
            con.execute("LOAD spatial")
        except Exception:                                       # noqa: BLE001
            pass
        df = con.execute(comp["sql"]).fetchdf()
    finally:
        con.close()

    out = {
        "screen": scr.name,
        "run_id": rid,
        "run_date": dt.datetime.now().isoformat(timespec="seconds"),
        "fabric_version": comp["fabric_version"],
        "snapshot": comp["snapshot"],
        "feature_path": comp["feature_path"],
        "juris": comp["juris"],
        "commodity": scr.commodity,
        "features_used": comp["features_used"],
        "notes": comp["notes"],
        "n_results": int(len(df)),
        "sql_sha256": hashlib.sha256(comp["sql"].encode()).hexdigest(),
    }
    if write:
        d = RUNS_DIR / _safe(scr.name) / rid
        d.mkdir(parents=True, exist_ok=True)
        (d / "screen.txt").write_text(screen_text)
        (d / "query.sql").write_text(comp["sql"])
        (d / "run.json").write_text(json.dumps(out, indent=1))
        df.to_parquet(d / "results.parquet", index=False)
        out["path"] = str(d)
    if not quiet:
        print(f"  {scr.name}: {len(df):,} cells  "
              f"[{comp['fabric_version']}/{comp['snapshot']}]  run {rid}")
        for n in comp["notes"]:
            print(f"    note: {n}")
        if write:
            print(f"    → {out['path']}")
    out["_df"] = df
    return out


def rerun(screen_name: str, run_id: str, quiet: bool = False) -> dict:
    """Replay a stored run's SQL and compare to what it recorded.

    Replays the SQL rather than recompiling the text on purpose: this is the
    acceptance check for "re-run identically", and recompiling would test the
    compiler rather than the run's reproducibility."""
    import duckdb
    import pandas as pd

    d = RUNS_DIR / _safe(screen_name) / run_id
    if not d.exists():
        raise FileNotFoundError(f"no run at {d}")
    sql = (d / "query.sql").read_text()
    meta = json.loads((d / "run.json").read_text())
    before = pd.read_parquet(d / "results.parquet")

    con = duckdb.connect()
    try:
        con.execute("SET autoinstall_known_extensions=1; "
                    "SET autoload_known_extensions=1")
        after = con.execute(sql).fetchdf()
    finally:
        con.close()

    same_rows = len(before) == len(after)
    same_cells = (set(before["cell_id"]) == set(after["cell_id"])
                  if "cell_id" in before and "cell_id" in after else False)
    identical = same_rows and same_cells
    if not quiet:
        print(f"  {screen_name}/{run_id}: {len(before):,} → {len(after):,} cells")
        print(f"  identical: {identical}")
    return {"screen": screen_name, "run_id": run_id, "identical": identical,
            "rows_before": len(before), "rows_after": len(after),
            "snapshot": meta.get("snapshot")}


def runs(screen_name: str) -> list:
    d = RUNS_DIR / _safe(screen_name)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "run.json").exists())


def diff(screen_name: str, a: str | None = None, b: str | None = None) -> dict:
    """What entered and left this screen between two runs.

    The standing report PLAN_C4 4.3 asks for. Ground entering a screen is the
    interesting direction — it is either newly open, newly hot, or newly
    understood — and ground leaving usually means someone else got there."""
    import pandas as pd

    rr = runs(screen_name)
    if len(rr) < 2 and not (a and b):
        return {"error": f"{screen_name} has {len(rr)} run(s); need two to diff"}
    a = a or rr[-2]
    b = b or rr[-1]
    da = pd.read_parquet(RUNS_DIR / _safe(screen_name) / a / "results.parquet")
    db = pd.read_parquet(RUNS_DIR / _safe(screen_name) / b / "results.parquet")
    sa, sb = set(da["cell_id"]), set(db["cell_id"])
    return {"screen": screen_name, "from": a, "to": b,
            "entered": sorted(sb - sa), "left": sorted(sa - sb),
            "n_entered": len(sb - sa), "n_left": len(sa - sb),
            "stable": len(sa & sb)}


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:60]


# ---------------------------------------------------------------------------
# The acceptance set
# ---------------------------------------------------------------------------

def acceptance(quiet: bool = False) -> dict:
    """The three screens PLAN_C4 4.3 names, run and re-run.

    "three screens written by hand — a lithology+structure+geochem screen, a
    'non-barren hole on open ground' screen ... and an expiring-claims screen —
    all compile, run, render, and re-run identically on the same snapshot"."""
    results = {}
    for path in sorted(LIBRARY.glob("*.screen")):
        name = path.stem
        try:
            out = run(path.read_text(), quiet=quiet)
            rr = rerun(out["screen"], out["run_id"], quiet=True)
            results[name] = {"compiled": True, "ran": True,
                             "n_results": out["n_results"],
                             "rerun_identical": rr["identical"],
                             "snapshot": out["snapshot"]}
        except Exception as e:                                  # noqa: BLE001
            results[name] = {"compiled": False, "error":
                             f"{type(e).__name__}: {e}"}
    ok = all(r.get("ran") and r.get("rerun_identical") for r in results.values())
    if not quiet:
        print("\n  C4.3 acceptance")
        for n, r in results.items():
            if r.get("ran"):
                print(f"    {n:34s} {r['n_results']:>7,} cells  "
                      f"re-run identical: {r['rerun_identical']}")
            else:
                print(f"    {n:34s} FAILED — {r['error']}")
        print(f"\n  ACCEPTANCE: {'MET' if ok else 'NOT MET'} "
              f"({len(results)} screens)")
    return {"screens": results, "acceptance_met": ok}


def main():
    ap = argparse.ArgumentParser(description="C4.3 — run a screen")
    ap.add_argument("--run", metavar="FILE")
    ap.add_argument("--text", help="inline screen text")
    ap.add_argument("--snapshot", help="pin to a feature snapshot")
    ap.add_argument("--fabric", help="pin to a fabric version")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", metavar="SCREEN")
    ap.add_argument("--diff", metavar="SCREEN")
    ap.add_argument("--rerun", metavar="SCREEN/RUN_ID")
    ap.add_argument("--acceptance", action="store_true")
    ap.add_argument("--csv", metavar="OUT")
    args = ap.parse_args()
    C.require_lake()

    if args.list:
        print("\n  library:")
        for p in sorted(LIBRARY.glob("*.screen")):
            print(f"    {p.stem}")
        print("\n  runs:")
        if RUNS_DIR.exists():
            for d in sorted(RUNS_DIR.iterdir()):
                if d.is_dir() and not d.name.startswith("_"):
                    print(f"    {d.name}: {len(runs(d.name))} run(s)")
        return
    if args.acceptance:
        acceptance()
        return
    if args.show:
        for r in runs(args.show):
            m = json.loads((RUNS_DIR / _safe(args.show) / r / "run.json").read_text())
            print(f"    {r}  {m['n_results']:>7,} cells  "
                  f"[{m['fabric_version']}/{m['snapshot']}]")
        return
    if args.diff:
        d = diff(args.diff)
        if d.get("error"):
            print(d["error"])
            return
        print(f"\n  {d['screen']}: {d['from']} → {d['to']}")
        print(f"    entered {d['n_entered']}  left {d['n_left']}  "
              f"stable {d['stable']}")
        for c in d["entered"][:10]:
            print(f"      + {c}")
        for c in d["left"][:10]:
            print(f"      - {c}")
        return
    if args.rerun:
        name, rid = args.rerun.rsplit("/", 1)
        rerun(name, rid)
        return
    text = args.text or (Path(args.run).read_text() if args.run else None)
    if not text:
        ap.print_help()
        return
    out = run(text, args.fabric, args.snapshot)
    if args.csv:
        out["_df"].to_csv(args.csv, index=False)
        print(f"    → {args.csv}")


if __name__ == "__main__":
    main()
