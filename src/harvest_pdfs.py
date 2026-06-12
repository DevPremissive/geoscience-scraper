#!/usr/bin/env python3
"""
harvest_pdfs.py — download + RAG-ingest assessment-report PDF corpora.

Drives the scrapers registered in connectors/scrape.py. For each finished scraper
it enumerates report ids, downloads new ones into pdfs/<JURIS>/<CODE>/, and
optionally runs RAG ingestion (text extract → chunk → embed → ChromaDB).

Usage:
    python src/harvest_pdfs.py                      # download only
    python src/harvest_pdfs.py --rag                # download + RAG ingest
    python src/harvest_pdfs.py --code BC_ARIS_PDF --rag
    python src/harvest_pdfs.py --only-rag           # re-ingest existing PDFs
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, logging, sqlite3, sys, time
from pathlib import Path
from urllib.request import Request, urlopen

import config as C
from connectors import scrape as SC

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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


def _jurisdiction_from_code(code: str) -> str:
    return code.split("_")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=None)
    ap.add_argument("--rag", action="store_true", help="Run RAG ingestion after download")
    ap.add_argument("--only-rag", action="store_true", help="Re-ingest existing PDFs (skip download)")
    args = ap.parse_args()
    C.ensure_dirs()
    con = _con()
    today = dt.date.today().isoformat()

    targets = ([SC.SCRAPERS[args.code]] if args.code
               else list(SC.SCRAPERS.values()))
    for sc in targets:
        juris = _jurisdiction_from_code(sc.code)
        dest_dir = C.PDF_DIR / sc.code
        dest_dir.mkdir(parents=True, exist_ok=True)

        if not args.only_rag:
            if not sc.ready():
                print(f"  - {sc.code}: scraper stub not finished — skipping. ({sc.notes})")
                continue
            ids = sc.enumerate_ids()
            print(f"  {sc.code}: {len(ids)} reports to consider")
            for rid in ids:
                url = sc.pdf_url(rid)
                dest = dest_dir / f"{rid}.pdf"
                done = con.execute("SELECT 1 FROM harvest WHERE resource_id=?",
                                   (f"{sc.code}:{rid}",)).fetchone()
                if done:
                    continue
                try:
                    sha, size = _dl(url, dest)
                except Exception as e:
                    print(f"    ! {rid}: {e}", file=sys.stderr)
                    continue
                con.execute("INSERT OR REPLACE INTO harvest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (f"{sc.code}:{rid}", juris, sc.code, "scrape",
                             sc.code, url, "pdf", "", sha, size, str(dest), today,
                             dt.datetime.now().isoformat(timespec="seconds")))
                con.commit()
                time.sleep(C.REQUEST_GAP)
                print(f"    + {rid} ({size/1e6:.1f}MB)")

        if args.rag or args.only_rag:
            from pipeline.rag import ingest_pdfs
            from vector_store import VectorStore

            store = VectorStore(collection_name="geo_canada")
            print(f"  → RAG-ingesting {sc.code} from {dest_dir}")
            result = ingest_pdfs(dest_dir, juris, sc.code, store=store)
            print(f"    ingested={result['ingested']} skipped={result['skipped']} "
                  f"failed={result['failed']} chunks={result['chunks']}")

    con.close()
    print("Done.")


if __name__ == "__main__":
    main()
