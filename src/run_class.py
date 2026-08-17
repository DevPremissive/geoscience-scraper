#!/usr/bin/env python3
"""
run_class.py — the unattended harvest pipeline for one cadence class (C3.1).

This is what the systemd timers invoke. It exists so that a scheduled run is one
supervised pipeline rather than several timers racing each other:

    heartbeat(start) → harvest --class=X → verify_harvest → process → [post-harvest] → heartbeat(finish)

Every run writes a heartbeat row whether it succeeds or fails, because the
failure this design is built against is not a crash — it is a run that quietly
does nothing and reports success. `watchdog()` is what turns silence into an
alert: a missed run, a non-zero exit, a verify rejection, or a tenure source
that has gone unchanged for longer than it plausibly could.

Usage:
    python src/run_class.py tenure
    python src/run_class.py geoscience --no-process
    python src/run_class.py --watchdog          # check health, alert, exit
"""
from __future__ import annotations
import argparse, datetime as dt, json, sqlite3, subprocess, sys, time
from pathlib import Path

import config as C
import sources as S
from alerting import alert

HEALTH_DB = C.INDEX_ROOT / "health.sqlite"
SRC = Path(__file__).resolve().parent
PYTHON = sys.executable

#: How long after its due time a class may go unseen before the watchdog alerts.
EXPECTED_INTERVAL = {
    "tenure": dt.timedelta(days=1),
    "geoscience": dt.timedelta(days=7),
    "rasters": dt.timedelta(days=31),
}
GRACE = dt.timedelta(hours=6)

#: Consecutive zero-change runs before a daily source is treated as silently
#: broken. Tenure registries move constantly; three still days means the fetch
#: is succeeding against something that is no longer the real feed.
ZERO_CHANGE_STREAK = 3


def _con():
    HEALTH_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(HEALTH_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, class TEXT, started TEXT, finished TEXT,
        exit INTEGER, bytes INTEGER, changed_sources INTEGER,
        fetched INTEGER, failed INTEGER, notes TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS runs_class ON runs(class, started)")
    con.commit()
    return con


def _lake_bytes() -> int:
    try:
        return sum(f.stat().st_size for f in C.RAW_DIR.rglob("*") if f.is_file())
    except OSError:
        return 0


def _run(cmd: list[str], log: Path) -> tuple[int, str]:
    """Run a step, tee-ing its output to the run log. Returns (exit, tail)."""
    with open(log, "ab") as fh:
        fh.write(f"\n$ {' '.join(cmd)}\n".encode())
        fh.flush()
        p = subprocess.run(cmd, cwd=SRC, stdout=fh, stderr=subprocess.STDOUT)
    tail = ""
    try:
        tail = log.read_text(errors="replace")[-4000:]
    except OSError:
        pass
    return p.returncode, tail


def run(cls: str, do_process: bool = True) -> int:
    started = dt.datetime.now()
    run_id = f"{cls}-{started:%Y%m%dT%H%M%S}"
    log = C.LOG_DIR / f"{run_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    con = _con()
    con.execute("INSERT OR REPLACE INTO runs (run_id, class, started) VALUES (?,?,?)",
                (run_id, cls, started.isoformat(timespec="seconds")))
    con.commit()

    before = _lake_bytes()
    rc, tail = _run([PYTHON, "-u", "harvest.py", "--class", cls], log)
    fetched = failed = 0
    changed_codes: set[str] = set()
    for line in tail.splitlines():
        # "  + ON    ON_MLAS_TENURE        zip   208.1MB" — one per changed source.
        if line.startswith("  + "):
            parts = line.split()
            if len(parts) >= 3:
                changed_codes.add(parts[2])
        elif line.startswith("Done. fetched="):
            for part in line.replace("Done. ", "").split():
                k, _, v = part.partition("=")
                if k == "fetched" and v.isdigit():
                    fetched = int(v)
                if k == "failed" and v.isdigit():
                    failed = int(v)

    notes = []
    if rc != 0:
        notes.append(f"harvest exit {rc}")

    # Integrity gate. A harvest that "succeeded" while writing HTML error pages
    # or a truncated archive is the exact failure mode C0.1 was built around, so
    # it blocks the pipeline rather than being reported afterwards.
    #
    # Scoped to this class's codes on purpose. A lake-wide check would block the
    # daily tenure pipeline on an unrelated broken geoscience payload, and a
    # gate that fails for reasons the run cannot fix is a gate that gets
    # disabled.
    class_codes = sorted({c for _j, c in _class_pairs(cls)})
    vrc, vtail = _run([PYTHON, "-u", "verify_harvest.py", "--only", *class_codes], log)
    if vrc != 0:
        notes.append("verify_harvest rejected payloads")

    if do_process and rc == 0 and vrc == 0 and changed_codes:
        # Reprocess only what changed. Rebuilding every jurisdiction in the class
        # because one tenure file moved would cost hours a day and re-expand
        # gigabytes of unrelated archives; process.py replaces layers in place,
        # so a targeted run leaves the rest of geo.gpkg untouched.
        prc, _ = _run([PYTHON, "-u", "process.py", "--only",
                       *sorted(changed_codes)], log)
        if prc != 0:
            notes.append(f"process exit {prc}")
        else:
            irc, _ = _run([PYTHON, "-u", "build_index.py"], log)
            if irc != 0:
                notes.append(f"build_index exit {irc}")
        # Post-harvest hook: tenure feeds the event stream, which feeds C1.
        # tenure_events.py is C0.7 and not built yet; when it lands it chains
        # here rather than becoming a second timer racing this one.
        if cls == "tenure" and (SRC / "tenure_events.py").exists():
            trc, _ = _run([PYTHON, "-u", "tenure_events.py", "--incremental"], log)
            if trc != 0:
                notes.append(f"tenure_events exit {trc}")

    finished = dt.datetime.now()
    delta = _lake_bytes() - before
    exit_code = rc or vrc
    con.execute("UPDATE runs SET finished=?, exit=?, bytes=?, changed_sources=?, "
                "fetched=?, failed=?, notes=? WHERE run_id=?",
                (finished.isoformat(timespec="seconds"), exit_code, delta,
                 fetched, fetched, failed, "; ".join(notes), run_id))
    con.commit()
    con.close()

    dur = (finished - started).total_seconds()
    print(f"\n{run_id}: exit={exit_code} fetched={fetched} failed={failed} "
          f"+{delta/1e6:.1f}MB in {dur:.0f}s")

    if exit_code or notes:
        alert("harvest-health", f"{cls} harvest needs attention",
              f"run {run_id}\n" + ("; ".join(notes) or f"exit {exit_code}") +
              f"\nfetched={fetched} failed={failed}",
              key=cls, link=str(log), severity="error")
    return exit_code


def _class_pairs(cls: str):
    """(jurisdiction, code) pairs belonging to a class, from the registry."""
    for juris, _key, spec in S.iter_connectors():
        t = spec.get("type")
        codes = []
        if t == "ckan":
            codes = list(spec.get("match") or {})
        elif t == "arcgis":
            codes = list(spec.get("layers") or {}) + list(spec.get("items") or {})
        elif t in ("wfs", "ogsearth"):
            codes = list(spec.get("layers") or {})
        elif t == "direct":
            codes = list(spec.get("resources") or {})
        elif t == "es_scroll":
            codes = list(spec.get("indexes") or {})
        for code in codes:
            if S.class_of(code) == cls:
                yield juris, code


def watchdog() -> int:
    """Alert on silence, failure, or a daily feed that has stopped moving."""
    con = _con()
    problems = 0
    now = dt.datetime.now()

    for cls, interval in EXPECTED_INTERVAL.items():
        row = con.execute(
            "SELECT run_id, started, finished, exit, changed_sources, notes FROM runs "
            "WHERE class=? ORDER BY started DESC LIMIT 1", (cls,)).fetchone()
        if not row:
            print(f"  {cls:<12} never run")
            continue
        run_id, started, finished, exit_code, changed, notes = row
        age = now - dt.datetime.fromisoformat(started)

        if age > interval + GRACE:
            problems += 1
            alert("harvest-health", f"{cls} harvest has not run",
                  f"last run {run_id} was {age.days}d {age.seconds//3600}h ago; "
                  f"expected every {interval.days}d",
                  key=f"missed-{cls}", severity="error")
        elif finished is None:
            problems += 1
            alert("harvest-health", f"{cls} harvest did not finish",
                  f"run {run_id} started {started} and never recorded completion",
                  key=f"unfinished-{cls}", severity="error")
        elif exit_code:
            problems += 1
            alert("harvest-health", f"{cls} harvest failed",
                  f"run {run_id} exit {exit_code}: {notes or 'no detail'}",
                  key=f"exit-{cls}", severity="error")
        else:
            print(f"  {cls:<12} ok — {run_id}, {age.days}d{age.seconds//3600:02d}h ago")

    # A daily feed that stops changing is the quietest failure of all: every run
    # succeeds, nothing is wrong in the log, and the data is silently frozen.
    streak = con.execute(
        "SELECT COUNT(*) FROM (SELECT changed_sources FROM runs WHERE class='tenure' "
        "AND exit=0 ORDER BY started DESC LIMIT ?)"
        " WHERE changed_sources=0", (ZERO_CHANGE_STREAK,)).fetchone()[0]
    if streak >= ZERO_CHANGE_STREAK:
        problems += 1
        alert("harvest-health", "tenure feed has gone quiet",
              f"{streak} consecutive successful tenure runs changed nothing. "
              f"Tenure registries do not stand still — suspect a silently broken "
              f"endpoint rather than a quiet week.",
              key="tenure-quiet", severity="error")

    con.close()
    print(f"\nwatchdog: {problems} problem(s)")
    return 1 if problems else 0


def status(n: int = 10):
    con = _con()
    print(f"{'run_id':<26}{'exit':>5}{'fetched':>9}{'failed':>8}{'MB':>10}  notes")
    for r in con.execute("SELECT run_id, exit, fetched, failed, bytes, notes "
                         "FROM runs ORDER BY started DESC LIMIT ?", (n,)):
        mb = (r[4] or 0) / 1e6
        print(f"{r[0]:<26}{str(r[1]):>5}{str(r[2]):>9}{str(r[3]):>8}{mb:>10.1f}  {r[5] or ''}")
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cls", nargs="?", choices=S.CLASSES)
    ap.add_argument("--no-process", action="store_true")
    ap.add_argument("--watchdog", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.watchdog:
        sys.exit(watchdog())
    if args.status:
        status()
        return
    if not args.cls:
        ap.error("a class is required unless --watchdog/--status")
    C.require_lake()
    sys.exit(run(args.cls, do_process=not args.no_process))


if __name__ == "__main__":
    main()
