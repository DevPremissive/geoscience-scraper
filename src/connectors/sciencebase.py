#!/usr/bin/env python3
"""
sciencebase.py — USGS ScienceBase catalog connector (C2.2).

Plain JSON REST, no scraping and no key. Two calls do everything:

    /catalog/items?parentId=<id>&format=json   enumerate a data release's children
    /catalog/item/<id>?format=json             one item, with its file list

This is a *discovery* connector only. Every ScienceBase file is a plain HTTPS
download, so `harvest.py`'s existing `stream_download` fetches them; nothing here
touches the wire at fetch time.

**Why not just list the URLs as a `direct` source.** They look like
`.../file/get/61da068c...?f=__disk__1d/05/98/1d0598977...`, where the tail is a
content-addressed path that changes whenever the publisher re-uploads. Master §9
rule 2 says never hardcode source URLs; here the rule has teeth, because a stale
hardcoded URL would 404 silently into the "failed" counter rather than telling
anyone the release had been revised. Selection is by **file name**, which is the
stable identifier inside a release, and a name that stops resolving is reported
by code rather than skipped.

**The publisher's own checksums are used.** Each file object carries an MD5. The
harvest ledger records our sha256 for change detection, which answers "is this
the same bytes as last time" but not "is this the bytes USGS meant to serve".
`verify_md5()` answers the second, and `discover()` carries the expected digest
through on the resource record so a caller can check it.

Usage:
    python src/connectors/sciencebase.py --tree 6193e9f3d34eb622f68f13a5
    python src/connectors/sciencebase.py --item 61da068cd34ed7929400b5a2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

BASE = "https://www.sciencebase.gov/catalog"

#: ScienceBase is a public catalog with no documented rate limit. The repo-wide
#: politeness gap applies anyway — a data release is a handful of calls and there
#: is no reason to be the traffic anyone notices.
_GAP = C.REQUEST_GAP


def _get(url: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": C.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def item(item_id: str) -> dict:
    """One catalog item, including its file list."""
    time.sleep(_GAP)
    return _get(f"{BASE}/item/{urllib.parse.quote(item_id)}?format=json")


def children(parent_id: str, max_items: int = 200) -> list[dict]:
    """Child items of a data release, as {id, title}."""
    time.sleep(_GAP)
    url = (f"{BASE}/items?parentId={urllib.parse.quote(parent_id)}"
           f"&format=json&max={int(max_items)}&fields=title,id")
    d = _get(url)
    total = d.get("total", 0)
    items = d.get("items", [])
    if total > len(items):
        # Paging exists but a data release with >200 children would be unusual;
        # say so rather than silently returning a prefix.
        print(f"  ! sciencebase: parent {parent_id} has {total} children, "
              f"only {len(items)} fetched — raise max_items", file=sys.stderr)
    return items


def item_files(d: dict) -> list[dict]:
    """Every file on an item.

    Files live in two places: `files` at the top level, and inside each entry of
    `facets` (which is how ScienceBase models shapefile and geodatabase bundles).
    Reading only the first misses whole layers on exactly the items most worth
    having."""
    out = list(d.get("files") or [])
    for facet in (d.get("facets") or []):
        out.extend(facet.get("files") or [])
    return out


def file_md5(f: dict) -> str | None:
    ck = f.get("checksum") or {}
    if (ck.get("type") or "").upper() == "MD5" and ck.get("value"):
        return ck["value"].lower()
    return None


def verify_md5(path, expected: str) -> tuple[bool, str]:
    """Check a downloaded file against the publisher's MD5."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 22):
            h.update(chunk)
    got = h.hexdigest()
    return got == (expected or "").lower(), got


def _fmt_of(name: str) -> str:
    suffix = Path(name).suffix.lower().lstrip(".")
    return suffix or "dat"


def discover(spec: dict, juris: str) -> list[dict]:
    """Registry spec -> resource records for `harvest.py`.

    Spec shape:

        {"type": "sciencebase",
         "parent": "<data release item id>",
         "select": {CODE: {"files": [<file name>, ...], "notes": "..."}}}

    Names are matched exactly against the file names on the release's children.
    A selected name that matches nothing is reported and skipped — a data release
    that has been revised should be visible, not silently thinner."""
    parent = spec["parent"]
    select = spec.get("select") or {}

    # Build one name -> (item, file) index over the whole release, so a spec does
    # not have to know which child item holds which file. Which child a file
    # lives on is the publisher's filing decision and has changed between
    # revisions of this very release.
    index: dict[str, tuple[dict, dict]] = {}
    for kid in children(parent):
        d = item(kid["id"])
        for f in item_files(d):
            name = f.get("name")
            if name and name not in index:
                index[name] = (d, f)

    inv = []
    for code, sel in select.items():
        wanted = sel.get("files") or []
        for name in wanted:
            hit = index.get(name)
            if not hit:
                print(f"  ! sciencebase: {code} file {name!r} not found in release "
                      f"{parent} ({len(index)} files present) — release revised?",
                      file=sys.stderr)
                continue
            d, f = hit
            inv.append({
                "jurisdiction": juris,
                "connector": "sciencebase",
                "code": code,
                "dataset": d.get("title", code),
                "resource_id": f"{code}:{name}",
                "resource_name": name,
                "format": _fmt_of(name),
                "url": f.get("url") or f.get("downloadUri"),
                # dateUploaded is per-file and moves only when the file itself is
                # replaced; the item's lastUpdated moves when anyone edits the
                # metadata, which would re-download a gigabyte over a typo.
                "last_modified": f.get("dateUploaded") or "",
                "size": f.get("size"),
                "license": "public domain (USGS)",
                "portal": spec.get("portal", BASE),
                "note": sel.get("notes", ""),
                "_sb_item": d.get("id"),
                "_sb_md5": file_md5(f),
            })
    return inv


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="USGS ScienceBase catalog probe")
    ap.add_argument("--tree", metavar="PARENT_ID",
                    help="enumerate a data release's children and their files")
    ap.add_argument("--item", metavar="ITEM_ID", help="dump one item's files")
    ap.add_argument("--grep", default="", help="only show file names containing this")
    args = ap.parse_args()

    if args.item:
        d = item(args.item)
        print(d.get("title"))
        for f in item_files(d):
            if args.grep and args.grep.lower() not in f["name"].lower():
                continue
            print(f"  {f['name']:58s} {(f.get('size') or 0)/1e6:8.1f} MB  "
                  f"md5={file_md5(f)}")
        return
    if args.tree:
        total = 0.0
        for kid in children(args.tree):
            d = item(kid["id"])
            files = [f for f in item_files(d)
                     if not args.grep or args.grep.lower() in f["name"].lower()]
            if not files:
                continue
            print(f"\n{d.get('title','')[:88]}\n  {kid['id']}")
            for f in files:
                mb = (f.get("size") or 0) / 1e6
                total += mb
                print(f"    {f['name']:56s} {mb:8.1f} MB")
        print(f"\ntotal: {total:,.0f} MB")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
