#!/usr/bin/env python3
"""
alerting.py — the single choke point for every alert the system raises (C3.6).

Event types: harvest-health (C3.1), lapse-watch (C1.6), heat-threshold (C1.3),
new-filing (C3.5), tier2-negative (C5.2).

Two design rules, both from the plan:

  "Quietly watch, rarely act." Alert fatigue is a system failure, not a nuisance,
  so every type carries a rate limit and anything below `DIGEST_TYPES` accumulates
  into a daily digest instead of interrupting.

  Nothing leaves this machine unless it was explicitly configured to. Delivery is
  local-only by default: alerts are always recorded in the health database, and a
  remote channel is used only when CANADA_GEO_NTFY_TOPIC is set. An unset topic is
  not an error — it is the default posture.

Usage:
    from alerting import alert
    alert("harvest-health", "tenure harvest failed", "BC_MTA_CURRENT: HTTP 503",
          link="ops/health.sqlite", severity="error")

    python src/alerting.py --digest        # emit and clear the pending digest
    python src/alerting.py --test          # record a test alert
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sqlite3, sys
from urllib.request import Request, urlopen

import config as C

ALERT_DB = C.INDEX_ROOT / "health.sqlite"

#: Minimum seconds between two alerts of the same (type, key). Anything more
#: frequent is suppressed and counted, not delivered.
RATE_LIMIT = {
    "harvest-health": 3600,
    "lapse-watch": 21600,
    "heat-threshold": 86400,
    "new-filing": 3600,
    "tier2-negative": 86400,
}
DEFAULT_RATE_LIMIT = 3600

#: Types that never interrupt — they accumulate into the daily digest.
DIGEST_TYPES = {"heat-threshold", "new-filing"}

#: Severities that always deliver immediately, rate limit notwithstanding.
URGENT = {"error", "critical"}


def _con():
    ALERT_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ALERT_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raised_at TEXT, event_type TEXT, key TEXT, severity TEXT,
        title TEXT, message TEXT, link TEXT,
        delivered INTEGER DEFAULT 0, suppressed INTEGER DEFAULT 0,
        digested INTEGER DEFAULT 0)""")
    con.execute("CREATE INDEX IF NOT EXISTS alerts_type_key ON alerts(event_type, key)")
    con.commit()
    return con


def _ntfy_topic() -> str:
    """Remote channel, or "" when none is configured.

    Deliberately read from the environment rather than committed anywhere: a
    public ntfy topic is a broadcast, and guessing one would publish this
    system's activity to a channel nobody chose.
    """
    return os.environ.get("CANADA_GEO_NTFY_TOPIC", "").strip()


def _deliver(title: str, message: str, link: str | None, severity: str) -> bool:
    topic = _ntfy_topic()
    if not topic:
        return False
    body = message + (f"\n{link}" if link else "")
    req = Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={"Title": title,
                 "Priority": "high" if severity in URGENT else "default",
                 "Tags": "pick,warning" if severity in URGENT else "pick"},
    )
    try:
        with urlopen(req, timeout=15):
            return True
    except Exception as e:                                      # noqa: BLE001
        print(f"  ! alert delivery failed: {e}", file=sys.stderr)
        return False


def _recently_alerted(con, event_type: str, key: str, window: int) -> bool:
    row = con.execute(
        "SELECT raised_at FROM alerts WHERE event_type=? AND key=? AND delivered=1 "
        "ORDER BY id DESC LIMIT 1", (event_type, key)).fetchone()
    if not row:
        return False
    try:
        last = dt.datetime.fromisoformat(row[0])
    except ValueError:
        return False
    return (dt.datetime.now() - last).total_seconds() < window


def alert(event_type: str, title: str, message: str, key: str | None = None,
          link: str | None = None, severity: str = "warning") -> str:
    """Raise one alert. Returns "delivered", "digested" or "suppressed".

    Always recorded, whatever the outcome — the record is the audit trail, and
    a suppressed alert that was never written down is indistinguishable from an
    alert that was never raised.
    """
    key = key or title
    con = _con()
    now = dt.datetime.now().isoformat(timespec="seconds")

    if event_type in DIGEST_TYPES and severity not in URGENT:
        outcome, delivered, digested, suppressed = "digested", 0, 0, 0
    elif _recently_alerted(con, event_type, key,
                           RATE_LIMIT.get(event_type, DEFAULT_RATE_LIMIT)) \
            and severity not in URGENT:
        outcome, delivered, digested, suppressed = "suppressed", 0, 0, 1
    else:
        ok = _deliver(title, message, link, severity)
        # Recorded as delivered when it reached its configured destination —
        # and the local database IS the destination when no topic is set.
        outcome = "delivered" if ok else "recorded"
        delivered, digested, suppressed = 1, 0, 0

    con.execute("INSERT INTO alerts (raised_at, event_type, key, severity, title, "
                "message, link, delivered, suppressed, digested) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now, event_type, key, severity, title, message, link or "",
                 delivered, suppressed, digested))
    con.commit()
    con.close()
    print(f"  [{severity}] {event_type}: {title} → {outcome}")
    return outcome


def digest(clear: bool = True) -> str:
    """Render pending digest-class alerts as one message, and mark them sent."""
    con = _con()
    rows = con.execute(
        "SELECT id, raised_at, event_type, title, message FROM alerts "
        "WHERE digested=0 AND delivered=0 AND suppressed=0 ORDER BY id").fetchall()
    if not rows:
        con.close()
        return ""
    lines = [f"{len(rows)} pending item(s)"]
    for _id, raised, etype, title, msg in rows:
        lines.append(f"  · [{etype}] {title} — {msg[:100]}")
    body = "\n".join(lines)
    if clear:
        con.executemany("UPDATE alerts SET digested=1 WHERE id=?",
                        [(r[0],) for r in rows])
        con.commit()
        _deliver("canada-geo-lake daily digest", body, None, "info")
    con.close()
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--recent", type=int, default=0, metavar="N")
    args = ap.parse_args()

    if args.test:
        alert("harvest-health", "test alert",
              "Raised by `alerting.py --test`; no action needed.",
              key="test", severity="info")
    if args.digest:
        out = digest()
        print(out or "nothing pending")
    if args.recent:
        con = _con()
        for r in con.execute(
                "SELECT raised_at, event_type, severity, title FROM alerts "
                "ORDER BY id DESC LIMIT ?", (args.recent,)):
            print(f"  {r[0]}  {r[2]:<8}{r[1]:<16}{r[3]}")
        con.close()
    if not (args.test or args.digest or args.recent):
        topic = _ntfy_topic()
        print(f"alert store : {ALERT_DB}")
        print(f"remote      : {'ntfy.sh/' + topic if topic else 'none configured (local only)'}")


if __name__ == "__main__":
    main()
