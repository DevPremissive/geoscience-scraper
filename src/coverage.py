#!/usr/bin/env python3
"""
coverage.py — national coverage report from the harvest manifest.

Shows what data has been harvested per jurisdiction, with dates, sizes,
formats, and gaps.

Usage:
    python src/coverage.py                       # full national report
    python src/coverage.py --jurisdiction ON BC  # subset
    python src/coverage.py --csv                 # machine-readable
    python src/coverage.py --gaps                # only show missing datasets
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

import config as C
import sources as S


def query_manifest(only_j=None, only_code=None):
    """Query manifest.sqlite for current coverage state."""
    if not C.MANIFEST_DB.exists():
        print(f"No manifest at {C.MANIFEST_DB}. Run harvest.py first.")
        return []

    con = sqlite3.connect(C.MANIFEST_DB)
    rows = con.execute("""
        SELECT
            jurisdiction,
            code,
            connector,
            format,
            MAX(snapshot_date) AS latest,
            MAX(fetched_at) AS last_fetched,
            SUM(size_bytes) AS total_bytes,
            COUNT(DISTINCT resource_id) AS resources,
            COUNT(DISTINCT snapshot_date || resource_id || sha256) AS versions
        FROM harvest
        GROUP BY jurisdiction, code, connector, format
        ORDER BY jurisdiction, code
    """).fetchall()
    con.close()

    if only_j:
        rows = [r for r in rows if r[0].upper() in {j.upper() for j in only_j}]
    if only_code:
        rows = [r for r in rows if r[1].upper() in {c.upper() for c in only_code}]
    return rows


def expected_datasets() -> dict[str, set[str]]:
    """Return {jurisdiction: {code, ...}} for all declared datasets."""
    expected: dict[str, set[str]] = {}
    for juris, key, spec in S.iter_connectors():
        ctype = spec.get("type")
        if ctype == "ckan":
            expected.setdefault(juris, set()).update(spec.get("match", {}).keys())
        elif ctype in ("direct", "es_scroll"):
            expected.setdefault(juris, set()).update(spec.get("resources", {}).keys())
            expected.setdefault(juris, set()).update(spec.get("indexes", {}).keys())
        elif ctype == "arcgis":
            expected.setdefault(juris, set()).update(spec.get("layers", {}).keys())
            expected.setdefault(juris, set()).update(spec.get("items", {}).keys())
        elif ctype == "wfs":
            expected.setdefault(juris, set()).update(spec.get("layers", {}).keys())
        elif ctype == "ogsearth":
            expected.setdefault(juris, set()).update(spec.get("layers", {}).keys())
        elif ctype == "scrape":
            expected.setdefault(juris, set()).add(spec.get("code", ""))
    return expected


def fmt_bytes(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    elif b < 1024 * 1024:
        return f"{b/1024:.0f}KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b/1024/1024:.1f}MB"
    return f"{b/1024/1024/1024:.1f}GB"


def main():
    ap = argparse.ArgumentParser(description="National coverage report")
    ap.add_argument("--jurisdiction", nargs="*", default=None)
    ap.add_argument("--code", nargs="*", default=None)
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--gaps", action="store_true")
    args = ap.parse_args()

    harvested = query_manifest(args.jurisdiction, args.code)
    expected = expected_datasets()

    if args.jurisdiction:
        expected = {k: v for k, v in expected.items()
                    if k.upper() in {j.upper() for j in args.jurisdiction}}

    # Build harvested sets per jurisdiction
    harvested_by_j: dict[str, dict[str, list]] = {}
    for r in harvested:
        juris, code = r[0], r[1]
        harvested_by_j.setdefault(juris, {}).setdefault(code, []).append(r)

    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["jurisdiction", "code", "connector", "format", "latest",
                     "last_fetched", "total_bytes", "resources", "versions"])
        for r in harvested:
            w.writerow(r)
        return

    for juris in sorted(expected):
        exp = expected[juris]
        have = harvested_by_j.get(juris, {})
        have_codes = set(have.keys())
        missing = exp - have_codes
        extra = have_codes - exp

        print(f"\n{'='*60}")
        print(f"  {juris}")
        print(f"{'='*60}")

        if missing and args.gaps:
            print(f"  MISSING ({len(missing)}):")
            for c in sorted(missing):
                print(f"    - {c}")
        elif not args.gaps:
            print(f"  Declared: {len(exp):>3}  |  Harvested: {len(have_codes):>3}"
                  f"  |  Missing: {len(missing):>3}")
            if have_codes:
                # Connector priority for dedup: direct > es_scroll > arcgis > wfs > ckan > ogsearth > scrape
                _PRIORITY = {"direct": 0, "es_scroll": 1, "arcgis": 2, "arcgis_layer": 2,
                             "arcgis_hub": 2, "wfs_layer": 3, "wfs": 3, "ckan": 4,
                             "ogsearth": 5, "scrape": 6}
                print(f"\n  {'CODE':<24} {'FMT':<7} {'LATEST':<12} {'SIZE':>10}")
                print(f"  {'-'*24} {'-'*7} {'-'*12} {'-'*10}")
                for code in sorted(have_codes):
                    entries = have[code]
                    # Prefer by connector priority, then by size
                    best = min(entries, key=lambda x: (_PRIORITY.get(x[2], 99), -(x[6] or 0)))
                    _, _, conn, fmt, latest, _, total, res, ver = best
                    desc = fmt_bytes(total) if total else "?"
                    print(f"  {code:<24} {fmt:<7} {latest or '?':<12} {desc:>10}")
                if missing:
                    print(f"\n  Missing: {', '.join(sorted(missing))}")
            if extra:
                print(f"  Extra (not in registry): {', '.join(sorted(extra))}")

    # Summary
    all_exp = sum(len(v) for v in expected.values())
    all_have = sum(len(harvested_by_j.get(j, {})) for j in expected)
    print(f"\n{'='*60}")
    print(f"  TOTAL: {all_have}/{all_exp} datasets harvested "
          f"({all_have/all_exp*100:.0f}%)" if all_exp else "  No datasets declared.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
