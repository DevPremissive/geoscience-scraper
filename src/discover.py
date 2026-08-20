#!/usr/bin/env python3
"""
discover.py — resolve current downloadable resources across ALL Canadian sources.

Walks the registry in sources.py, dispatching each connector spec to the right
connector module (ckan / arcgis). Scrape sources are reported as 'needs scraper'
so you know what's pending.

Usage:
    python src/discover.py                 # full national inventory (table)
    python src/discover.py --json          # machine-readable (piped by harvest.py)
    python src/discover.py --jurisdiction ON BC   # restrict
"""
from __future__ import annotations
import argparse, json, sys

import config as C
import sources as S
from connectors import ckan, arcgis, wfs, ogsearth, es_scroll, sciencebase


def spec_codes(spec) -> list:
    """Dataset codes a connector spec declares, without touching the network."""
    t = spec.get("type")
    if t == "ckan":
        return list(spec.get("match") or {})
    if t == "arcgis":
        return list(spec.get("layers") or {}) + list(spec.get("items") or {})
    if t in ("wfs", "ogsearth"):
        return list(spec.get("layers") or {})
    if t == "direct":
        return list(spec.get("resources") or {})
    if t == "es_scroll":
        return list(spec.get("indexes") or {})
    if t == "sciencebase":
        return list(spec.get("select") or {})
    if t == "scrape":
        return [spec.get("code")] if spec.get("code") else []
    return []


def discover_all(only=None, cls=None):
    inv = []
    for juris, key, spec in S.iter_connectors():
        if only and juris not in only:
            continue
        # Skip a connector whose codes are all outside the requested class.
        # Discovery is not free: enumerating the OGSEarth superoverlay walks
        # 1,514 tile URLs, and a daily tenure run has no use for any of them.
        if cls:
            codes = spec_codes(spec)
            if codes and not any(S.class_of(c) == cls for c in codes):
                continue
        ctype = spec.get("type")
        try:
            if ctype == "ckan":
                inv.extend(ckan.discover(spec, juris))
            elif ctype == "arcgis":
                inv.extend(arcgis.discover(spec, juris))
            elif ctype == "wfs":
                inv.extend(wfs.discover(spec, juris))
            elif ctype == "ogsearth":
                inv.extend(ogsearth.discover(spec, juris))
            elif ctype == "direct":
                for code, res in spec.get("resources", {}).items():
                    inv.append({
                        "jurisdiction": juris, "connector": "direct",
                        "code": code, "dataset": code, "resource_id": code,
                        "resource_name": res.get("resource_name", code),
                        "format": res.get("format", "dat"),
                        "url": res["url"],
                        "last_modified": "",
                        "size": res.get("size"),
                        "license": None,
                        "portal": spec.get("portal"),
                        "note": res.get("notes", ""),
                    })
            elif ctype == "es_scroll":
                for code, res in spec.get("indexes", {}).items():
                    info = es_scroll.discover(
                        endpoint=spec["endpoint"],
                        index=res["index"],
                        api_key=spec.get("api_key", ""),
                    )
                    inv.append({
                        "jurisdiction": juris, "connector": "es_scroll",
                        "code": code, "dataset": code, "resource_id": code,
                        "resource_name": res.get("resource_name", code),
                        "format": "jsonl",
                        "url": spec["endpoint"],
                        "last_modified": "",
                        "size": None,
                        "license": None,
                        "portal": spec.get("portal"),
                        "note": res.get("notes", ""),
                        "_es_index": res["index"],
                        "_es_api_key": spec.get("api_key", ""),
                        "_es_query": res.get("query", {"match_all": {}}),
                        "_es_page_size": spec.get("page_size", 1000),
                    })
            elif ctype == "sciencebase":
                inv.extend(sciencebase.discover(spec, juris))
            elif ctype == "scrape":
                inv.append({"jurisdiction": juris, "connector": "scrape",
                            "code": spec.get("code", f"{juris}_SCRAPE"),
                            "dataset": spec.get("code"), "resource_id": None,
                            "format": "pdf", "url": None,
                            "portal": spec.get("portal"),
                            "note": "needs scraper stub finished (connectors/scrape.py)"})
        except Exception as e:                                   # noqa: BLE001
            print(f"  ! {juris}/{key} discover failed: {e}", file=sys.stderr)
    return inv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--jurisdiction", nargs="*", default=None)
    ap.add_argument("--class", dest="cls", choices=S.CLASSES, default=None)
    args = ap.parse_args()
    only = {j.upper() for j in args.jurisdiction} if args.jurisdiction else None

    inv = discover_all(only, cls=args.cls)

    if args.json:
        json.dump(inv, sys.stdout, indent=2)
        return

    by_j = {}
    for r in inv:
        by_j.setdefault(r["jurisdiction"], []).append(r)
    for juris in sorted(by_j):
        rows = by_j[juris]
        downloadable = [r for r in rows if r.get("url")]
        print(f"\n########## {juris}  ({len(downloadable)} downloadable, "
              f"{len(rows)} total) ##########")
        for r in rows:
            if not r.get("url"):
                print(f"   [{r['connector']:<11}] PENDING  {r['code']}  "
                      f"({r.get('note','')})")
                continue
            size = f"{int(r['size'])/1e6:.1f}MB" if r.get("size") else "?"
            fc = f" ~{r['feature_count']} feats" if r.get("feature_count") else ""
            print(f"   [{r['connector']:<11}] {r['format']:>7} {size:>8}{fc}  "
                  f"{r['code']} :: {r['resource_name']}")
    print(f"\nTOTAL: {sum(1 for r in inv if r.get('url'))} downloadable resources "
          f"across {len(by_j)} jurisdictions.")


if __name__ == "__main__":
    main()
