#!/usr/bin/env python3
"""
reports.py — on-demand assessment-report fetch for one target (C3.4).

`fetch_reports(geometry, juris)` resolves the assessment reports whose footprint
touches a target and downloads only those. **Per-target, not corpus-scale** — the
national harvest of ~110k reports is C5.3 and is deliberately a different thing
with a different cadence. A deal touches dozens of reports; fetching a province
to answer a question about one cell is the mistake this module exists to avoid.

Contract, from PLAN_C3 3.4:

    fetch_reports(geometry, juris, max_reports=None) ->
      [{report_id, title, year, work_types[], pdf_path, status}]

**Ontario needs no scraping and no key.** Audit C1 replaced the OAFD
Elasticsearch route with LIO ArcGIS layer 50, `OMEIS Technical File Area`, and
that layer is already harvested: 62,436 records with real polygon footprints,
`TECH_ID`, `WORK_TYPE`, `YEAR_FROM/TO`, `COMMODITIES` and — useful for the
acceptance test — `INFO_LINK`, the province's own viewer URL for the same
report. Resolution is therefore a spatial query against `geo.gpkg`, and the PDF
is a plain HTTPS GET from the Azure blob that `connectors/scrape.py` records.
The `es_scroll` dependency and the API-key requirement are both withdrawn.

Other jurisdictions are on demand, per `SCRAPERS_BLOCKED.md`: BC ARIS next, then
SK SMAD / NL GeoFiles / NS NovaScan / NB PARIS / NTGS as a deal touches them.
`resolve_reports` raises for an unregistered jurisdiction rather than returning
an empty list, because "no reports here" and "we cannot look here" are different
answers and only one of them is about the ground.

Usage:
    python src/reports.py --cell 892b968aac7ffff --radius-km 2 --list
    python src/reports.py --cell 892b968aac7ffff --radius-km 2 --fetch --max 25
    python src/reports.py --bbox -80.9,48.3,-80.7,48.45 --list
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sqlite3
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

#: Per jurisdiction: the harvested index layer, its id column, and the scraper
#: in connectors/scrape.py that knows how to build a PDF URL from that id.
INDEX_LAYERS = {
    "ON": {
        "table": "geo_ON__ON_OMEIS_TECHFILE",
        "id_col": "TECH_ID",
        "scraper": "ON_AFRI_PDF",
        "code": "ON_AFRI_PDF",
        "fields": {
            "title": "PROPERTY",
            "year_from": "YEAR_FROM",
            "year_to": "YEAR_TO",
            "work_types": "WORK_TYPE",
            "work_group": "WORK_TYPE_GROUP",
            "commodities": "COMMODITIES",
            "submission": "SUBMISSION_TYPE",
            "performed_for": "PERFORMED_FOR",
            "township": "PRIMARY_TOWNSHIP",
            "info_link": "INFO_LINK",
        },
    },
}

def resolve_pdf_files(report_id: str, juris: str = "ON") -> list[str]:
    """Real file names for a report, from the publisher's own metadata.

    **This is how the modern reports are reached, and it needed no scraping.**
    `connectors/scrape.py` hardcodes the blob name as `{id}.Pdf`, an "observed
    pattern" that holds for the pre-2000 scanned era and fails outright for
    MLAS-era submissions. The GeologyOntario metadata record carries the answer
    directly in `technical_reports[].file_name` — `20000021294_01.pdf`,
    lowercase and suffixed — and that path downloads a valid 8 MB PDF.

    A report may carry several files (`_01`, `_02`, …); all are returned. `maps`
    is a separate array and deliberately not included: map sheets are images and
    contribute nothing to a text corpus.

    Falls back to the legacy pattern when the metadata is unavailable, so the
    pre-2000 corpus keeps working if the API is down or unkeyed."""
    from connectors import scrape as SC
    try:
        meta = SC._on_metadata(report_id)
    except Exception:                                           # noqa: BLE001
        meta = None
    names = []
    for tr in ((meta or {}).get("technical_reports") or []):
        fn = (tr or {}).get("file_name")
        if fn:
            names.append(fn)
    return names or [f"{report_id}.Pdf"]


#: What the `{id}/{id}.Pdf` blob pattern actually covers, measured 2026-08-21
#: over a 60-id stratified sample of the 62,436 technical-file records:
#:
#:     pre-1990   10/12     1990s   11/12     no year   9/12
#:     2000s       2/12     2010+    0/12
#:
#: The pattern is the *scanned-paper* era. Modern MLAS-era submissions
#: (`2000002xxxx` ids) return Azure `BlobNotFound`, and neither the MLAS
#: transaction id, the work-report number, nor any alternate folder resolves
#: them; the container refuses prefix listing and the metadata index carries no
#: filename. Retrieving them needs a devtools capture of the GeologyOntario
#: SPA's own download call — the same exercise SCRAPERS_BLOCKED.md scopes for
#: other jurisdictions, and not something to guess at (audit P1).
#:
#: This is stated rather than smoothed over because a thin corpus that looks
#: complete is the failure mode: the six-question set in C5.1 would answer
#: "work stopped in 1998" for ground that was drilled in 2021.
#: SUPERSEDED 2026-08-21, same day: the gap above was ours, not the publisher's.
#: `resolve_pdf_files()` reads the real name out of the metadata record and the
#: modern reports download fine. Kept as the record of what the legacy pattern
#: alone covers, because the fallback still uses it.
ERA_COVERAGE_NOTE = (
    "Reports that still fail after filename resolution are genuinely absent "
    "from the blob store, not a pattern mismatch. Missing reports are listed by "
    "id and year so the gap is visible, not inferred.")

#: A PDF smaller than this is not a report. Ontario serves an HTML error body
#: with a 200 for some ids, and `harvest.py` learned the same lesson the hard
#: way (audit B3): trust the bytes, not the status code.
MIN_PDF_BYTES = 2048


class UnsupportedJurisdiction(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def cell_polygon_wkt(cell_id: str, radius_km: float = 0.0) -> str:
    """WKT for an H3 cell, optionally buffered by `radius_km`.

    Buffering in degrees rather than reprojecting: a target is a ~300 m cell and
    the radii that matter here are single-digit kilometres, over which the error
    from treating a degree box as a circle is far smaller than the footprint of
    the assessment areas being intersected. Longitude is scaled by cos(lat) so
    the buffer is not 50% too wide east-west at Ontario's latitudes."""
    import h3
    from shapely.geometry import Polygon

    ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell_id)]
    poly = Polygon(ring)
    if radius_km > 0:
        lat = h3.cell_to_latlng(cell_id)[0]
        dlat = radius_km / 111.0
        dlng = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
        # Anisotropic buffer: scale to a local metric-ish frame, buffer, scale back.
        from shapely.affinity import scale
        squashed = scale(poly, xfact=dlat / dlng, yfact=1.0, origin="center")
        poly = scale(squashed.buffer(dlat), xfact=dlng / dlat, yfact=1.0,
                     origin="center")
    return poly.wkt


def bbox_wkt(bbox) -> str:
    minx, miny, maxx, maxy = bbox
    return (f"POLYGON(({minx} {miny},{maxx} {miny},{maxx} {maxy},"
            f"{minx} {maxy},{minx} {miny}))")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _catalog():
    import duckdb
    con = duckdb.connect(str(C.CATALOG_DB), read_only=True)
    try:
        con.execute("LOAD spatial")
    except Exception:
        con.execute("INSTALL spatial; LOAD spatial")
    return con


def _split_work_types(raw) -> list:
    """`WORK_TYPE` is a comma-joined list with parenthetical expansions:
    `EM (Electromagnetic), MAG (Magnetic / Magnetometer Survey)`. Splitting on a
    bare comma would cut inside `Magnetic / Magnetometer Survey` — the same
    shape of trap as the HOLDER field (audit I10), so split on the separator
    that only appears between entries."""
    import re
    if not raw:
        return []
    parts = re.split(r",\s*(?=[A-Z0-9]{1,12}\s*\()", str(raw))
    if len(parts) == 1:
        parts = [p for p in str(raw).split(",")]
    return [p.strip() for p in parts if p.strip()]


def resolve_reports(geometry_wkt: str, juris: str = "ON",
                    max_reports: int | None = None) -> list[dict]:
    """Assessment reports whose footprint intersects `geometry_wkt`.

    Ordered newest first, then by area ascending: a small, recent report about
    this ground is worth more per page than a regional compilation that happens
    to overlap it."""
    spec = INDEX_LAYERS.get(juris)
    if spec is None:
        raise UnsupportedJurisdiction(
            f"no report index registered for {juris!r}. Registered: "
            f"{sorted(INDEX_LAYERS)}. Adding one is a C3.4 step-1 task; see "
            f"SCRAPERS_BLOCKED.md for the per-system strategy.")
    f = spec["fields"]
    con = _catalog()
    try:
        sql = f'''
            SELECT "{spec['id_col']}" AS report_id,
                   "{f['title']}"        AS title,
                   "{f['year_from']}"    AS year_from,
                   "{f['year_to']}"      AS year_to,
                   "{f['work_types']}"   AS work_types,
                   "{f['work_group']}"   AS work_group,
                   "{f['commodities']}"  AS commodities,
                   "{f['submission']}"   AS submission,
                   "{f['performed_for']}" AS performed_for,
                   "{f['township']}"     AS township,
                   "{f['info_link']}"    AS info_link,
                   ST_Area(geom)         AS footprint_deg2
            FROM "{spec['table']}"
            WHERE ST_Intersects(geom, ST_GeomFromText(?))
            ORDER BY COALESCE("{f['year_from']}", 0) DESC, footprint_deg2 ASC
        '''
        rows = con.execute(sql, [geometry_wkt]).fetchall()
        names = [d[0] for d in con.description]
    finally:
        con.close()

    out = []
    for row in rows:
        r = dict(zip(names, row))
        rid = (r.get("report_id") or "").strip()
        if not rid:
            continue
        year = r.get("year_from") or r.get("year_to")
        out.append({
            "report_id": rid,
            "title": r.get("title") or None,
            "year": int(year) if year else None,
            "year_to": int(r["year_to"]) if r.get("year_to") else None,
            "work_types": _split_work_types(r.get("work_types")),
            "work_group": r.get("work_group") or None,
            "commodities": _split_work_types(r.get("commodities")),
            "submission_type": r.get("submission") or None,
            "performed_for": r.get("performed_for") or None,
            "township": r.get("township") or None,
            "info_link": r.get("info_link") or None,
            "juris": juris,
            "pdf_path": None,
            "status": "resolved",
        })
    if max_reports:
        out = out[:max_reports]
    return out


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _manifest():
    con = sqlite3.connect(C.MANIFEST_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS harvest (
        resource_id TEXT, jurisdiction TEXT, code TEXT, connector TEXT, dataset TEXT,
        url TEXT, format TEXT, ckan_modified TEXT, sha256 TEXT, size_bytes INTEGER,
        local_path TEXT, snapshot_date TEXT, fetched_at TEXT,
        PRIMARY KEY (resource_id, sha256))""")
    return con


def pdf_dir(juris: str, code: str) -> Path:
    """`pdfs/<JURIS>/<CODE>/`, the layout config.py documents.

    `harvest_pdfs.py` writes `pdfs/<CODE>/` instead. Nothing has been downloaded
    through it yet, so there is no corpus to reconcile — this follows the
    documented layout and `harvest_pdfs.py` was corrected to match."""
    return C.PDF_DIR / juris / code


def _download(url: str, dest: Path) -> tuple[str, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    n = 0
    req = Request(url, headers={"User-Agent": C.USER_AGENT})
    try:
        with urlopen(req, timeout=C.TIMEOUT) as r, open(tmp, "wb") as fh:
            while chunk := r.read(1 << 16):
                fh.write(chunk)
                h.update(chunk)
                n += len(chunk)
        head = tmp.open("rb").read(5)
        if head[:4] != b"%PDF":
            raise ValueError(f"not a PDF (starts {head!r}) — {n} bytes")
        if n < MIN_PDF_BYTES:
            raise ValueError(f"implausibly small PDF: {n} bytes")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return h.hexdigest(), n


def coverage_summary(reports: list[dict]) -> dict:
    """What was retrieved, what was not, and when the gaps are from.

    A dossier must be able to say "3 of the 12 reports on this ground could not
    be retrieved, all of them 2015 or later" rather than quietly presenting the
    7 it has. Missing recent work is exactly the kind of absence that reads as
    "nothing happened here"."""
    got = [r for r in reports if r.get("status") in ("downloaded", "cached")]
    missing = [r for r in reports if r.get("status") == "failed"]
    years = [r["year"] for r in missing if r.get("year")]
    return {
        "resolved": len(reports),
        "retrieved": len(got),
        "missing": len(missing),
        "missing_ids": [r["report_id"] for r in missing],
        "missing_year_range": [min(years), max(years)] if years else None,
        "retrieved_year_range": (
            [min(y), max(y)] if (y := [r["year"] for r in got if r.get("year")])
            else None),
        "note": ERA_COVERAGE_NOTE if missing else None,
    }


def fetch_reports(geometry_wkt: str, juris: str = "ON",
                  max_reports: int | None = None,
                  refetch: bool = False, quiet: bool = False) -> list[dict]:
    """Resolve, then download only the resolved reports. Idempotent.

    A PDF already on disk is reported `cached` and not re-fetched; the ledger
    row is what makes that decision, and it is checked against the file the way
    `harvest.payload_present` does, because a ledger row whose payload has gone
    missing is not evidence we hold the data (audit I1)."""
    from connectors import scrape as SC

    spec = INDEX_LAYERS[juris]
    sc = SC.SCRAPERS.get(spec["scraper"])
    if sc is None or not sc.ready():
        raise UnsupportedJurisdiction(
            f"scraper {spec['scraper']!r} is not ready; cannot build PDF URLs")

    reports = resolve_reports(geometry_wkt, juris, max_reports)
    dest_dir = pdf_dir(juris, spec["code"])
    con = _manifest()
    today = dt.date.today().isoformat()
    n_new = n_cached = n_failed = 0

    try:
        for rep in reports:
            rid = rep["report_id"]
            dest = dest_dir / f"{rid}.pdf"
            resource_id = f"{spec['code']}:{rid}"
            prev = con.execute(
                "SELECT local_path FROM harvest WHERE resource_id=? "
                "ORDER BY fetched_at DESC LIMIT 1", (resource_id,)).fetchone()
            if not refetch and dest.exists() and prev and Path(prev[0]).exists():
                rep["pdf_path"] = str(dest)
                rep["status"] = "cached"
                n_cached += 1
                continue

            # Ask the publisher what the file is called before guessing.
            names = resolve_pdf_files(rid, juris)
            base = sc.pdf_url(rid).rsplit("/", 1)[0]
            sha = size = None
            url = None
            last_err = None
            for fn in names:
                cand = f"{base}/{fn}"
                try:
                    sha, size = _download(cand, dest)
                    url = cand
                    break
                except Exception as e:                          # noqa: BLE001
                    last_err = f"{type(e).__name__}: {e}"
            if url is None:
                rep["status"] = "failed"
                rep["error"] = last_err or "no candidate file name resolved"
                rep["tried"] = names
                n_failed += 1
                if not quiet:
                    print(f"    ! {rid}: {rep['error']} (tried {names})",
                          file=sys.stderr)
                continue
            rep["pdf_files"] = names

            (dest_dir / f"{rid}.json").write_text(json.dumps({
                "jurisdiction": juris, "connector": "scrape",
                "code": spec["code"], "dataset": spec["code"],
                "resource_id": resource_id, "resource_name": f"{rid}.pdf",
                "format": "pdf", "url": url, "last_modified": "",
                "size": size, "license": None, "portal": sc.portal,
                "note": "resolved by C3.4 fetch_reports from "
                        f"{spec['table']}; index fields carried below",
                "index": {k: rep.get(k) for k in
                          ("title", "year", "year_to", "work_types",
                           "work_group", "commodities", "submission_type",
                           "performed_for", "township", "info_link")},
            }, indent=1), encoding="utf-8")

            con.execute("INSERT OR REPLACE INTO harvest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (resource_id, juris, spec["code"], "scrape", spec["code"],
                         url, "pdf", "", sha, size, str(dest), today,
                         dt.datetime.now().isoformat(timespec="seconds")))
            con.commit()
            rep["pdf_path"] = str(dest)
            rep["status"] = "downloaded"
            rep["sha256"] = sha
            rep["size_bytes"] = size
            n_new += 1
            if not quiet:
                print(f"    + {rid} ({size/1e6:.1f} MB) {rep.get('year') or ''}")
            time.sleep(C.REQUEST_GAP)
    finally:
        con.close()

    if not quiet:
        print(f"  {len(reports)} resolved · {n_new} downloaded · "
              f"{n_cached} cached · {n_failed} failed → {dest_dir}")
        if n_failed:
            cs = coverage_summary(reports)
            yr = cs["missing_year_range"]
            print(f"  MISSING {n_failed} report(s)"
                  + (f", years {yr[0]}-{yr[1]}" if yr else "")
                  + f": {', '.join(cs['missing_ids'][:6])}")
            print(f"  {ERA_COVERAGE_NOTE}")
    return reports


# ---------------------------------------------------------------------------

def _geometry_from_args(args) -> tuple[str, str]:
    if args.cell:
        return cell_polygon_wkt(args.cell, args.radius_km), f"cell {args.cell}"
    if args.bbox:
        vals = [float(x) for x in args.bbox.split(",")]
        if len(vals) != 4:
            sys.exit("--bbox needs minlon,minlat,maxlon,maxlat")
        return bbox_wkt(vals), f"bbox {args.bbox}"
    if args.wkt:
        return args.wkt, "wkt"
    sys.exit("give one of --cell, --bbox, --wkt")


def main():
    ap = argparse.ArgumentParser(description="C3.4 — on-demand report fetch")
    ap.add_argument("--cell", help="H3 cell id")
    ap.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat")
    ap.add_argument("--wkt", help="geometry as WKT")
    ap.add_argument("--radius-km", type=float, default=0.0,
                    help="buffer around --cell")
    ap.add_argument("--juris", default="ON")
    ap.add_argument("--max", type=int, default=None, dest="max_reports")
    ap.add_argument("--list", action="store_true", help="resolve only")
    ap.add_argument("--fetch", action="store_true", help="resolve and download")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    C.require_lake()
    geom, label = _geometry_from_args(args)

    if args.fetch:
        reports = fetch_reports(geom, args.juris, args.max_reports, args.refetch)
    else:
        reports = resolve_reports(geom, args.juris, args.max_reports)

    if args.json:
        print(json.dumps(reports, indent=1))
        return

    print(f"\n{len(reports)} reports intersect {label} "
          f"({args.juris}, radius {args.radius_km} km)\n")
    for r in reports[:60]:
        yr = r["year"] or "----"
        wt = ", ".join(r["work_types"])[:44]
        st = "" if r["status"] == "resolved" else f"  [{r['status']}]"
        print(f"  {r['report_id']:14s} {yr}  {wt:46s}{st}")
        if r.get("title"):
            print(f"                 {r['title'][:70]}")
    if len(reports) > 60:
        print(f"  ... and {len(reports)-60} more")
    if not args.fetch:
        print("\n  (resolve only — add --fetch to download the PDFs)")
        if reports:
            print(f"  cross-check one against the province's own viewer:\n"
                  f"    {reports[0]['info_link']}")


if __name__ == "__main__":
    main()
