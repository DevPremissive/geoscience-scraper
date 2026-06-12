#!/usr/bin/env python3
"""
harvest_pdfs.py — download the assessment-report PDF corpora (NLP targets).

Drives the scrapers registered in connectors/scrape.py. For each finished scraper
it enumerates report ids, maps each to a PDF/ZIP url, and downloads into
pdfs/<JURIS>/<CODE>/, ledgering in manifest.sqlite. Scrapers whose stubs are not yet
finished are skipped with a clear message.

Usage:
    python src/harvest_pdfs.py                 # all finished scrapers
    python src/harvest_pdfs.py --code BC_ARIS_PDF
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, sqlite3, sys, time
from urllib.request import Request, urlopen

import config as C
from connectors import scrape as SC


def _con():
    con = sqlite3.connect(C.MANIFEST_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS harvest (
        resource_id TEXT, jurisdiction TEXT, code TEXT, connector TEXT, dataset TEXT,
        url TEXT, format TEXT, ckan_modified TEXT, sha256 TEXT, size_bytes INTEGER,
        local_path TEXT, snapshot_date TEXT, fetched_at TEXT,
        PRIMARY KEY (resource_id, sha256))""")
    return con


def _dl(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(); n = 0
    req = Request(url, headers={"User-Agent": C.USER_AGENT})
    with urlopen(req, timeout=C.TIMEOUT) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk); h.update(chunk); n += len(chunk)
    return h.hexdigest(), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=None)
    args = ap.parse_args()
    C.ensure_dirs()
    con = _con()
    today = dt.date.today().isoformat()

    targets = ([SC.SCRAPERS[args.code]] if args.code
               else list(SC.SCRAPERS.values()))
    for sc in targets:
        if not sc.ready():
            print(f"  - {sc.code}: scraper stub not finished — skipping. ({sc.notes})")
            continue
        ids = sc.enumerate_ids()
        print(f"  {sc.code}: {len(ids)} reports to consider")
        for rid in ids:
            url = sc.pdf_url(rid)
            dest = C.PDF_DIR / sc.code / f"{rid}.pdf"
            done = con.execute("SELECT 1 FROM harvest WHERE resource_id=?",
                               (f"{sc.code}:{rid}",)).fetchone()
            if done:
                continue
            try:
                sha, size = _dl(url, dest)
            except Exception as e:                              # noqa: BLE001
                print(f"    ! {rid}: {e}", file=sys.stderr)
                continue
            con.execute("INSERT OR REPLACE INTO harvest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"{sc.code}:{rid}", sc.code.split('_')[0], sc.code, "scrape",
                         sc.code, url, "pdf", "", sha, size, str(dest), today,
                         dt.datetime.now().isoformat(timespec="seconds")))
            con.commit()
            time.sleep(C.REQUEST_GAP)
    con.close()
    print("Done.")


if __name__ == "__main__":
    main()
