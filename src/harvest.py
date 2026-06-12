#!/usr/bin/env python3
"""
harvest.py — incremental national download with manifest ledger.

- Discovers current resources across all jurisdictions (discover.py).
- Downloads changed CKAN/ArcGIS resources into raw/<JURIS>/<CODE>/<date>/.
- ArcGIS layers are paged to GeoJSON; CKAN/Hub resources streamed directly.
- Records url/hash/size/timestamp in manifest.sqlite for idempotency + history.
- Scrape (PDF) sources are handled by harvest_pdfs.py once their stubs are finished.

Usage:
    python src/harvest.py                       # all jurisdictions, core formats
    python src/harvest.py --jurisdiction ON BC  # subset
    python src/harvest.py --only ON_ODHD CGMC   # specific dataset codes
    python src/harvest.py --force               # ignore change-detection
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, sqlite3, sys, time
from pathlib import Path
from urllib.request import Request, urlopen

import config as C
from discover import discover_all
from connectors import arcgis


def init_manifest():
    con = sqlite3.connect(C.MANIFEST_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS harvest (
        resource_id TEXT, jurisdiction TEXT, code TEXT, connector TEXT,
        dataset TEXT, url TEXT, format TEXT, ckan_modified TEXT, sha256 TEXT,
        size_bytes INTEGER, local_path TEXT, snapshot_date TEXT, fetched_at TEXT,
        PRIMARY KEY (resource_id, sha256))""")
    con.commit()
    return con


def last_known(con, resource_id):
    row = con.execute("SELECT sha256, ckan_modified FROM harvest WHERE resource_id=? "
                      "ORDER BY fetched_at DESC LIMIT 1", (resource_id,)).fetchone()
    return row if row else None


def stream_download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(); n = 0
    req = Request(url, headers={"User-Agent": C.USER_AGENT})
    with urlopen(req, timeout=C.TIMEOUT) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk); h.update(chunk); n += len(chunk)
    return h.hexdigest(), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jurisdiction", nargs="*", default=None)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    C.ensure_dirs()
    con = init_manifest()
    today = dt.date.today().isoformat()
    now = dt.datetime.now().isoformat(timespec="seconds")

    only_j = {j.upper() for j in args.jurisdiction} if args.jurisdiction else None
    only_c = {c.upper() for c in args.only} if args.only else None

    inv = discover_all(only_j)
    fetched = skipped = failed = pending = 0

    for r in inv:
        if not r.get("url"):
            pending += 1
            continue
        if only_c and r["code"].upper() not in only_c:
            continue
        fmt = r["format"]
        if fmt not in C.CORE_FORMATS and r["connector"] not in ("arcgis_layer", "arcgis_hub"):
            continue

        prev = last_known(con, r["resource_id"] or r["url"])
        if prev and not args.force and prev[1] and prev[1] == (r.get("last_modified") or ""):
            skipped += 1
            continue

        fname = (r["resource_name"] or r["resource_id"] or "data").replace("/", "_")
        if not Path(fname).suffix:
            fname += f".{fmt or 'dat'}"
        dest = C.RAW_DIR / r["jurisdiction"] / r["code"] / today / fname

        try:
            if r["connector"] == "arcgis_layer":
                cnt = arcgis.fetch_layer_paged(r["url"], dest)
                sha = hashlib.sha256(dest.read_bytes()).hexdigest()
                size = dest.stat().st_size
                extra = f" ({cnt} features)"
            else:
                sha, size = stream_download(r["url"], dest)
                extra = ""
        except Exception as e:                                  # noqa: BLE001
            print(f"  ! FAIL {r['jurisdiction']}/{r['code']} {fname}: {e}", file=sys.stderr)
            failed += 1
            continue

        if prev and prev[0] == sha and not args.force:
            dest.unlink(missing_ok=True)
            skipped += 1
            continue

        (dest.parent / "_source.json").write_text(json.dumps(r, indent=2), encoding="utf-8")
        con.execute("INSERT OR REPLACE INTO harvest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["resource_id"] or r["url"], r["jurisdiction"], r["code"],
                     r["connector"], r["dataset"], r["url"], fmt,
                     r.get("last_modified") or "", sha, size, str(dest), today, now))
        con.commit()
        fetched += 1
        print(f"  + {r['jurisdiction']:<6}{r['code']:<22}{fmt:<7}{size/1e6:7.1f}MB{extra}")
        time.sleep(C.REQUEST_GAP)

    con.close()
    print(f"\nDone. fetched={fetched} skipped={skipped} failed={failed} "
          f"pending_scrapers={pending}")
    print(f"Run harvest_pdfs.py for the PENDING scrape (PDF) sources once stubs are finished.")


if __name__ == "__main__":
    main()
