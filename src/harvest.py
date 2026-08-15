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
from connectors import arcgis, wfs, es_scroll


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
    row = con.execute("SELECT sha256, ckan_modified, local_path FROM harvest "
                      "WHERE resource_id=? ORDER BY fetched_at DESC LIMIT 1",
                      (resource_id,)).fetchone()
    return row if row else None


def payload_present(prev) -> bool:
    """True when the file a ledger row references is actually on disk.

    Every "unchanged, skip it" decision is conditional on this. A row whose
    payload has gone missing is not evidence that we hold the data — it is
    precisely the state the eight lost Ontario downloads left behind, and
    skipping on it would refuse to re-fetch the very files we came for.
    """
    return bool(prev) and bool(prev[2]) and Path(prev[2]).exists()


#: Longest real extension we expect is "geojson" (7).
_MAX_EXT_LEN = 8


def needs_extension(name: str, fmt: str) -> bool:
    """True when `name` has no usable file extension for `fmt`.

    Resource names are frequently coordinates — `-95_53_-94.5_53.5` — for which
    `Path.suffix` returns junk like `.5` or `.5_-93_54`. Only a short, purely
    alphabetic suffix counts as a real extension, so those names get one appended
    instead of being written without any.
    """
    if not fmt:
        return False
    suffix = Path(name).suffix.lstrip(".")
    return not (suffix.isalpha() and len(suffix) <= _MAX_EXT_LEN)


def sniff_container(path) -> str | None:
    """Identify a file by its leading bytes, ignoring whatever it is named.

    Returns "zip", "html", "sqlite" or None. Registry/CKAN `format` fields lie —
    Québec serves ZIPs labelled `.gpkg`, and failed downloads arrive as HTML
    error pages with a `.zip` name.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:16] == b"SQLite format 3\x00":
        return "sqlite"
    if head.lstrip()[:14].lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    return None


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
        if (prev and not args.force and prev[1]
                and prev[1] == (r.get("last_modified") or "")
                and payload_present(prev)):
            skipped += 1
            continue

        fname = (r["resource_name"] or r["resource_id"] or "data").replace("/", "_")
        fname = C.safe_filename(fname)
        if needs_extension(fname, fmt):
            fname += f".{fmt}"
        dest = C.RAW_DIR / r["jurisdiction"] / r["code"] / today / fname
        # Stage every download beside its destination. Nothing is written to
        # `dest` until the content is known-good and known-wanted, so a skip or a
        # failure can never delete a file the manifest already points at.
        tmp = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            if r["connector"] == "arcgis_layer":
                cnt = arcgis.fetch_layer_paged(r["url"], tmp)
                sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
                size = tmp.stat().st_size
                extra = f" ({cnt} features)"
            elif r["connector"] == "wfs_layer":
                cnt = wfs.fetch_paged(
                    r["url"], r.get("type_name", ""),
                    r.get("sort_by", "OBJECTID"), tmp,
                    page_size=r.get("page_size", 10000),
                )
                sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
                size = tmp.stat().st_size
                extra = f" ({cnt} features)"
            elif r["connector"] == "es_scroll":
                result = es_scroll.fetch_scroll(
                    endpoint=r["url"],
                    index=r["_es_index"],
                    api_key=r.get("_es_api_key", ""),
                    dest=tmp,
                    page_size=r.get("_es_page_size", 1000),
                    query=r.get("_es_query", {"match_all": {}}),
                )
                sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
                size = tmp.stat().st_size
                extra = f" ({result['fetched']} records)"
            else:
                sha, size = stream_download(r["url"], tmp)
                extra = ""
        except Exception as e:                                  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            print(f"  ! FAIL {r['jurisdiction']}/{r['code']} {fname}: {e}", file=sys.stderr)
            failed += 1
            continue

        # Reject HTML served in place of the binary the registry promised. This is
        # how the FED geophysics and ON bedrock "downloads" became error pages.
        sniffed = sniff_container(tmp)
        if sniffed == "html" and fmt not in ("html", "htm"):
            tmp.unlink(missing_ok=True)
            print(f"  ! REJECT {r['jurisdiction']}/{r['code']} {fname}: "
                  f"server returned HTML, registry says {fmt}", file=sys.stderr)
            failed += 1
            continue

        if prev and prev[0] == sha and not args.force and payload_present(prev):
            # Content unchanged since the last harvest AND we still hold the file:
            # drop the staged copy, leave what the manifest references untouched.
            tmp.unlink(missing_ok=True)
            skipped += 1
            continue
        if prev and prev[0] == sha and not payload_present(prev):
            # Same bytes, but the payload the ledger points at is gone. Promote
            # the staged copy into today's snapshot and heal the row onto it.
            print(f"  ~ RECOVER {r['jurisdiction']}/{r['code']}: re-fetched "
                  f"{size/1e6:.1f}MB for an orphaned ledger row", file=sys.stderr)

        if sniffed and fmt and sniffed != fmt:
            # The registry's declared format disagrees with the bytes on the wire
            # (QC ships ZIPs named .gpkg/.shp/.fgdb). Record the truth; process.py
            # sniffs content too, so the file stays readable either way.
            r = {**r, "sniffed_format": sniffed}

        tmp.replace(dest)
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
