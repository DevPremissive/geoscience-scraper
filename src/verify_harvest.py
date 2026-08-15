#!/usr/bin/env python3
"""
verify_harvest.py — prove the ledger and the disk agree, and that payloads are
the format they claim to be.

Two independent checks, both safe to run at any time:

  reconcile  Every `harvest` row in manifest.sqlite must point at a file that
             exists. Rows whose payload sits at a near-miss path (the ogsearth
             tiles, whose ledger paths lack the `.kmz` the files carry) are
             repaired *from disk* — the candidate's sha256 must match the row
             before anything is rewritten. Rows whose payload is gone are split
             into ORPHAN (no copy of this resource is held — re-fetch it) and
             SUPERSEDED (a newer row for the same resource does have a payload,
             so only that dated snapshot was lost and no re-fetch can undo it).

  verify     Sniff the leading bytes of every payload and compare against the
             registry's declared `format`. HTML served in place of a binary is
             the failure that turned ON bedrock and FED geophysics into 6 KB
             error pages while the ledger recorded them as successful ZIPs.
             Archives are additionally opened to confirm they have a central
             directory: a truncated ZIP still begins with `PK\x03\x04`, so
             magic bytes alone called a 291 MB fragment of the 640 MB MLAS
             administrative bundle a healthy download.

Run after every harvest. `--fix` is required before the reconciler writes.

Usage:
    python src/verify_harvest.py                    # report both checks
    python src/verify_harvest.py reconcile --fix    # repair near-miss paths
    python src/verify_harvest.py verify --jurisdiction ON
"""
from __future__ import annotations
import argparse, hashlib, sqlite3, sys, zipfile
from collections import Counter
from pathlib import Path

import config as C
from harvest import sniff_container

#: Extensions whose container is legitimately a ZIP. Registry `format` values
#: naming one of these are not "wrong" when the bytes sniff as zip.
_ZIP_FORMATS = {"zip", "shp", "gpkg", "fgdb", "gdb", "kmz", "xlsx", "xls"}

#: Formats that are plain text/JSON and so sniff as nothing in particular.
_UNSNIFFABLE = {"csv", "tsv", "json", "jsonl", "geojson", "kml", "gpx", "tif",
                "tiff", "geotif", "dat"}


def _rows(con, only_j=None, only_c=None):
    sql = ("SELECT rowid, jurisdiction, code, connector, format, sha256, size_bytes, "
           "local_path, snapshot_date, resource_id FROM harvest")
    for r in con.execute(sql):
        if only_j and r[1] not in only_j:
            continue
        if only_c and r[2] not in only_c:
            continue
        yield r


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _candidates(local_path: str, fmt: str):
    """Paths a row's payload might actually live at, most likely first.

    Only near-misses of the recorded path are considered — this repairs a
    naming defect, it does not go hunting the lake for a plausible file.
    """
    p = Path(local_path)
    yield p
    if fmt:
        yield Path(local_path + "." + fmt)
    if p.suffix:
        yield p.with_suffix("")


def _live_resources(con) -> set:
    """resource_ids that have at least one row whose payload is on disk."""
    live = set()
    for rid, lp in con.execute("SELECT resource_id, local_path FROM harvest"):
        if rid not in live and lp and Path(lp).exists():
            live.add(rid)
    return live


def reconcile(con, fix=False, only_j=None, only_c=None):
    """Point every ledger row at the file it describes, or report it orphaned.

    Orphans are split in two, because the difference decides whether anyone
    needs to act:

      ORPHAN      no row for this resource has a payload on disk — the data is
                  genuinely absent and needs re-fetching.
      SUPERSEDED  this row's payload is gone, but a newer row for the same
                  resource does have one. That is the ordinary shape of a
                  destroyed historical snapshot whose source has since been
                  re-harvested with different content: the *data* is held, that
                  particular dated snapshot is not, and no re-fetch can bring
                  it back. Reporting these as orphans forever would train the
                  reader to ignore the check.
    """
    ok = repaired = 0
    fixable, orphans = [], []
    live = _live_resources(con)

    for rowid, juris, code, conn, fmt, sha, size, lp, snap, rid in _rows(con, only_j, only_c):
        found = None
        for cand in _candidates(lp, fmt):
            if cand.exists() and cand.is_file():
                found = cand
                break
        if found is None:
            orphans.append((juris, code, conn, snap, size or 0, lp, rid in live))
            continue
        if str(found) == lp:
            ok += 1
            continue
        # A different path than the ledger records. Confirm it is the same
        # payload before rewriting — size first, it is free.
        if found.stat().st_size != size or _sha256(found) != sha:
            orphans.append((juris, code, conn, snap, size or 0, lp, rid in live))
            continue
        fixable.append((rowid, lp, str(found)))

    if fix and fixable:
        con.executemany("UPDATE harvest SET local_path=? WHERE rowid=?",
                        [(new, rid) for rid, _old, new in fixable])
        con.commit()
        repaired = len(fixable)

    print(f"== reconcile ==")
    print(f"  rows on disk at recorded path : {ok}")
    print(f"  rows repairable from disk     : {len(fixable)}"
          + (f"  → {repaired} rewritten" if fix else "  (re-run with --fix)"))
    true_orphans = [o for o in orphans if not o[6]]
    superseded   = [o for o in orphans if o[6]]
    print(f"  orphaned rows (need re-fetch) : {len(true_orphans)}")
    print(f"  superseded snapshots          : {len(superseded)}   (payload gone, newer copy held)")
    if fixable and not fix:
        by_code = Counter(Path(old).parent.parent.name for _rid, old, _new in fixable)
        for code, n in by_code.most_common():
            print(f"    {n:>6} in {code}")
    for juris, code, conn, snap, size, lp, sup in sorted(orphans, key=lambda r: -r[4]):
        print(f"    {'SUPERSEDED' if sup else 'ORPHAN':<11}{juris:<4}{code:<20}{conn:<10}{snap}  {size/1e6:8.1f}MB")
    return len(true_orphans)


def _zip_complete(path) -> bool:
    """True when a ZIP has a readable central directory, i.e. is not truncated.

    Magic-byte sniffing cannot see this: a fragment of a ZIP still starts with
    `PK\\x03\\x04`. The central directory lives at the *end*, so this is the
    cheapest honest test that the whole file arrived.
    """
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def verify(con, only_j=None, only_c=None):
    """Sniff every payload; report content that contradicts its declared format."""
    checked = html = mismatch = absent = truncated = 0
    problems = []

    for _rowid, juris, code, conn, fmt, _sha, _size, lp, snap, _rid in _rows(con, only_j, only_c):
        p = Path(lp)
        if not p.exists():
            absent += 1
            continue
        checked += 1
        sniffed = sniff_container(p)
        fmt = (fmt or "").lower()
        if sniffed == "html" and fmt not in ("html", "htm"):
            html += 1
            problems.append(("HTML", juris, code, snap, fmt, p))
        elif sniffed == "zip" and fmt not in _ZIP_FORMATS:
            mismatch += 1
            problems.append(("ZIP-as-" + fmt, juris, code, snap, fmt, p))
        elif sniffed == "sqlite" and fmt not in ("gpkg", "sqlite", "db"):
            mismatch += 1
            problems.append(("SQLITE-as-" + fmt, juris, code, snap, fmt, p))
        elif sniffed == "zip" and not _zip_complete(p):
            # Starts like a ZIP but has no central directory — a fragment of a
            # download that ended early and was stored as if it succeeded.
            truncated += 1
            problems.append(("TRUNCATED-ZIP", juris, code, snap, fmt, p))

    print(f"== verify ==")
    print(f"  payloads sniffed              : {checked}")
    print(f"  HTML served as binary         : {html}")
    print(f"  container/format disagreement : {mismatch}")
    print(f"  truncated archives            : {truncated}")
    print(f"  rows skipped (no file)        : {absent}   (see reconcile)")
    for kind, juris, code, snap, fmt, p in problems[:40]:
        print(f"    {kind:<16}{juris:<4}{code:<20}{snap}  {p.name[:60]}")
    if len(problems) > 40:
        print(f"    … {len(problems)-40} more")
    return html + truncated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("check", nargs="?", choices=["reconcile", "verify", "both"],
                    default="both")
    ap.add_argument("--fix", action="store_true",
                    help="reconcile: rewrite near-miss local_path values")
    ap.add_argument("--jurisdiction", nargs="*", default=None)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    C.require_lake()
    only_j = {j.upper() for j in args.jurisdiction} if args.jurisdiction else None
    only_c = {c.upper() for c in args.only} if args.only else None

    con = sqlite3.connect(C.MANIFEST_DB)
    rc = 0
    if args.check in ("reconcile", "both"):
        rc += reconcile(con, fix=args.fix, only_j=only_j, only_c=only_c)
    if args.check in ("verify", "both"):
        rc += verify(con, only_j=only_j, only_c=only_c)
    con.close()
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    main()
