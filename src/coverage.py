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
    python src/coverage.py --markdown            # regenerate COVERAGE.md

COVERAGE.md is GENERATED, never hand-edited. It was hand-maintained until
2026-08-17 and had drifted into fiction: it marked ON_ODHD, ON_OAFD, both
Ontario geology layers, ON_GEOPHYS, ON_GEOCHEM and CDoGS as READY while every
one of them was empty or an HTML error page on disk (audit C0.8). A status
column that describes what the registry *could* fetch, rather than what was
fetched, is worse than no status column — so this one is derived from the
harvest ledger, geo.gpkg and the Parquet store every time it is written.
"""
from __future__ import annotations

import argparse
import csv
import json
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
    ap.add_argument("--markdown", action="store_true",
                    help="regenerate COVERAGE.md from on-disk truth")
    ap.add_argument("--gaps", action="store_true")
    args = ap.parse_args()

    if args.markdown:
        write_markdown()
        return

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




# ---------------------------------------------------------------------------
# Generated COVERAGE.md
# ---------------------------------------------------------------------------

def _spatial_layers() -> dict:
    """{code: total features} across all geo.gpkg layers belonging to a code."""
    out: dict = {}
    try:
        import pyogrio
    except ImportError:
        return out
    try:
        names = [n for n, _ in pyogrio.list_layers(str(C.GPKG_PATH))]
    except Exception:                                           # noqa: BLE001
        return out
    for n in names:
        parts = n.split("__")
        if len(parts) < 2:
            continue
        code = parts[1]
        try:
            cnt = pyogrio.read_info(str(C.GPKG_PATH), layer=n)["features"]
        except Exception:                                       # noqa: BLE001
            continue
        out[code] = out.get(code, 0) + int(cnt)
    return out


def _tables() -> dict:
    """{code: number of parquet tables}."""
    out: dict = {}
    if not C.TABLES_DIR.exists():
        return out
    for p in C.TABLES_DIR.glob("*.parquet"):
        parts = p.stem.split("__")
        if len(parts) >= 2:
            out[parts[1]] = out.get(parts[1], 0) + 1
    return out


def _rasters() -> dict:
    """{code: n_cogs} from the raster registry.

    Without this, every raster source reads as RAW ONLY: `status_for` only knew
    about `geo.gpkg` and `processed/tables/`, and a COG lives in neither. CGMC —
    the national lithology raster, ingested since C0.4 and gridded onto the
    fabric — has been reported as "payload on disk but nothing lifted into a
    store" for as long as this file has been generated. COVERAGE.md exists to say
    what is actually on disk, so a whole modality it cannot see is a defect in
    the report, not a detail."""
    reg = C.PROCESSED_DIR / "rasters" / "rasters.json"
    if not reg.exists():
        return {}
    entries = json.loads(reg.read_text())
    out = {}
    for e in entries:
        if Path(e.get("path", "")).exists():
            out[e.get("code")] = out.get(e.get("code"), 0) + 1
    return out


def _blocked() -> dict:
    """{code: reason} for sources the registry marks unfetchable."""
    out = {}
    for _juris, _key, spec in S.iter_connectors():
        for code, reason in (spec.get("blocked") or {}).items():
            out[code] = reason
    return out


def status_for(code, harvested, spatial, tables, blocked, rasters=None) -> str:
    """One honest word about what is actually on disk for this code.

    A code can be lifted into more than one store — CMMI ships GeoTIFFs and
    shapefiles under the same code — so the most-processed state wins and the
    counts columns carry the detail."""
    rasters = rasters or {}
    if code in blocked:
        return f"BLOCKED ({blocked[code]})"
    if code not in harvested:
        return "NOT HARVESTED"
    if code in spatial:
        return "SPATIAL"
    if code in rasters:
        return "RASTER"
    if code in tables:
        return "TABULAR"
    return "RAW ONLY"


def write_markdown(path=None) -> Path:
    import datetime as dt
    path = Path(path) if path else C.REPO_ROOT / "COVERAGE.md"
    rows = query_manifest()
    harvested = {}
    for juris, code, conn, fmt, latest, fetched, nbytes, res, vers in rows:
        h = harvested.setdefault(code, {"juris": juris, "connector": conn,
                                        "latest": latest, "bytes": 0, "resources": 0})
        h["bytes"] += nbytes or 0
        h["resources"] += res or 0
        h["latest"] = max(h["latest"] or "", latest or "")
    spatial, tables, blocked = _spatial_layers(), _tables(), _blocked()
    expected = expected_datasets()

    lines = [
        "# Coverage — what is actually on disk",
        "",
        "**Generated by `python src/coverage.py --markdown`. Do not hand-edit.**",
        "",
        f"Built {dt.datetime.now().isoformat(timespec='seconds')} from the harvest",
        "ledger, `processed/geo.gpkg` and `processed/tables/`. Status means:",
        "",
        "| Status | Meaning |",
        "|---|---|",
        "| `SPATIAL` | harvested and present in `geo.gpkg` as queryable geometry |",
        "| `TABULAR` | harvested and present in `processed/tables/` as Parquet |",
        "| `RASTER` | harvested and registered as a COG in `processed/rasters/` |",
        "| `RAW ONLY` | payload on disk but nothing lifted into a store yet |",
        "| `NOT HARVESTED` | declared in `sources.py`, never fetched |",
        "| `BLOCKED` | known unfetchable through this connector; see registry notes |",
        "",
    ]

    rasters = _rasters()
    tot_spatial = sum(spatial.values())
    lines += [
        "## Totals",
        "",
        f"- **{len(harvested)}** dataset codes harvested of **"
        f"{sum(len(v) for v in expected.values())}** declared",
        f"- **{tot_spatial:,}** features across **{len(spatial)}** codes in `geo.gpkg`",
        f"- **{sum(tables.values())}** Parquet tables across **{len(tables)}** codes",
        f"- **{sum(rasters.values())}** registered COGs across **{len(rasters)}** codes",
        f"- **{sum(h['bytes'] for h in harvested.values())/1e9:.1f} GB** recorded in the ledger",
        "",
    ]

    for juris in sorted(expected):
        codes = sorted(expected[juris] | {c for c, h in harvested.items()
                                          if h["juris"] == juris})
        codes = [c for c in codes if c]
        if not codes:
            continue
        lines += [f"## {juris}", "",
                  "| Code | Status | Features | Tables | COGs | Latest | Size |",
                  "|---|---|---:|---:|---:|---|---:|"]
        for code in codes:
            h = harvested.get(code)
            st = status_for(code, harvested, spatial, tables, blocked, rasters)
            feats = f"{spatial[code]:,}" if code in spatial else ""
            tbls = str(tables[code]) if code in tables else ""
            cogs = str(rasters[code]) if code in rasters else ""
            latest = h["latest"] if h else ""
            size = fmt_bytes(h["bytes"]) if h and h["bytes"] else ""
            lines.append(f"| {code} | {st} | {feats} | {tbls} | {cogs} | "
                         f"{latest} | {size} |")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  → {path}  ({len(lines)} lines)")
    return path

if __name__ == "__main__":
    main()
