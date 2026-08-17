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
import sources as S
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
    """Stream `url` to `dest`, refusing a transfer that ended early.

    `r.read()` returning b"" means "connection finished", not "file complete" —
    a dropped connection is indistinguishable from a clean end, and urllib does
    not enforce Content-Length. That silently produced a 291 MB fragment of the
    640 MB MLAS administrative bundle which was stored, hashed, and recorded in
    the ledger as a successful fetch; only opening it as a ZIP revealed it had
    no central directory. A short read is now a hard failure, so nothing reaches
    the snapshot or the ledger unless the whole file arrived.

    The staged `.part` is kept on failure and resumed via a Range request, since
    that server drops this transfer every time and retrying from zero never
    finishes. A `.part` surviving from an earlier run is resumed too; if the
    remote file changed in between the result is a spliced file, which the
    central-directory check in `verify_harvest.py` rejects rather than storing.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Resume a partial `.part` with a Range request. MNDM drops the 640 MB
    # administrative bundle somewhere between 90 and 290 MB every time, so
    # retrying from zero never finishes; continuing from where it stopped does.
    start = dest.stat().st_size if dest.exists() else 0
    headers = {"User-Agent": C.USER_AGENT}
    if start:
        headers["Range"] = f"bytes={start}-"

    req = Request(url, headers=headers)
    with urlopen(req, timeout=C.TIMEOUT) as r:
        if start and r.getcode() != 206:
            start = 0            # server ignored Range — start over cleanly
        declared = r.headers.get("Content-Length")
        declared = int(declared) if declared and declared.isdigit() else None
        expected = start + declared if declared is not None else None
        with open(dest, "ab" if start else "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)

    n = dest.stat().st_size
    if expected is not None and n != expected:
        raise IOError(f"truncated download: got {n:,} bytes, expected {expected:,}")

    # Hash the finished file rather than the stream, so a resumed download and
    # a single-shot one produce the same digest.
    h = hashlib.sha256()
    with open(dest, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest(), n


#: Attempts per resource before it is recorded as failed.
FETCH_ATTEMPTS = 3


def with_retry(fn, what: str, tmp, cleanup=True):
    """Run `fn`, retrying transient network failures with a growing backoff.

    A single DNS hiccup mid-run cost 7 of 10 Ontario ArcGIS layers — the errors
    were `Temporary failure in name resolution` and `Remote end closed
    connection without response`, and the network was healthy again seconds
    later. Nothing retried, so a momentary blip left the jurisdiction
    half-harvested and silent about it. C3.1 runs this unattended every day,
    which makes one-shot fetching untenable.

    `cleanup` controls whether the staged `.part` is discarded between attempts.
    It must be True for connectors that build a file whole (ArcGIS/WFS paging,
    Elasticsearch scroll), where a partial body would corrupt the next try. It is
    False for plain HTTP downloads, which resume from the bytes already on disk.
    """
    delay = 3
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as e:                                  # noqa: BLE001
            if cleanup:
                tmp.unlink(missing_ok=True)
            if attempt == FETCH_ATTEMPTS:
                raise
            print(f"  … retry {attempt}/{FETCH_ATTEMPTS - 1} {what}: {e}",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jurisdiction", nargs="*", default=None)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--manifest-only", action="store_true",
                    help="record what each source currently publishes, fetch nothing")
    ap.add_argument("--class", dest="cls", choices=S.CLASSES, default=None,
                    help="harvest one cadence class (see PLAN_C3 3.1)")
    args = ap.parse_args()

    C.ensure_dirs()
    con = init_manifest()
    today = dt.date.today().isoformat()
    now = dt.datetime.now().isoformat(timespec="seconds")

    only_j = {j.upper() for j in args.jurisdiction} if args.jurisdiction else None
    only_c = {c.upper() for c in args.only} if args.only else None

    inv = discover_all(only_j)
    fetched = skipped = failed = pending = 0
    #: (jurisdiction, code) -> filenames this run discovered, fetched or not.
    discovered: dict = {}

    for r in inv:
        if not r.get("url"):
            pending += 1
            continue
        if only_c and r["code"].upper() not in only_c:
            continue
        if args.cls and S.class_of(r["code"]) != args.cls:
            continue
        fmt = r["format"]
        if fmt not in C.CORE_FORMATS and r["connector"] not in ("arcgis_layer", "arcgis_hub"):
            continue

        fname = (r["resource_name"] or r["resource_id"] or "data").replace("/", "_")
        fname = C.safe_filename(fname)
        if needs_extension(fname, fmt):
            fname += f".{fmt}"
        dest = C.RAW_DIR / r["jurisdiction"] / r["code"] / today / fname

        # Record every resource this run saw, before any skip decision. The
        # run manifest is what lets process.py tell "unchanged, still published"
        # from "withdrawn by the publisher" when it overlays dated snapshots —
        # a skipped resource is still a current one and must not be pruned.
        discovered.setdefault((r["jurisdiction"], r["code"]), set()).add(fname)
        if args.manifest_only:
            continue

        prev = last_known(con, r["resource_id"] or r["url"])
        if (prev and not args.force and prev[1]
                and prev[1] == (r.get("last_modified") or "")
                and payload_present(prev)):
            skipped += 1
            continue
        # Stage every download beside its destination. Nothing is written to
        # `dest` until the content is known-good and known-wanted, so a skip or a
        # failure can never delete a file the manifest already points at.
        tmp = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)

        def _fetch():
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
            return sha, size, extra

        try:
            # Paged/scrolled connectors rebuild their output from scratch each
            # attempt; plain downloads resume, so their partial must survive.
            paged = r["connector"] in ("arcgis_layer", "wfs_layer", "es_scroll")
            sha, size, extra = with_retry(
                _fetch, f"{r['jurisdiction']}/{r['code']}", tmp, cleanup=paged)
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

    # A run manifest per source, so process.py can distinguish a resource that
    # simply did not change from one the publisher has withdrawn. Only written
    # for a full run of that source — with --only the inventory is filtered and
    # the manifest would claim the rest of the source no longer exists.
    if not only_c:
        for (juris, code), names in discovered.items():
            snap = C.RAW_DIR / juris / code / today
            snap.mkdir(parents=True, exist_ok=True)
            (snap / "_manifest.json").write_text(
                json.dumps({"date": today, "run_finished": now,
                            "files": sorted(names)}, indent=2), encoding="utf-8")

    con.close()
    print(f"\nDone. fetched={fetched} skipped={skipped} failed={failed} "
          f"pending_scrapers={pending}")
    print(f"Run harvest_pdfs.py for the PENDING scrape (PDF) sources once stubs are finished.")


if __name__ == "__main__":
    main()
