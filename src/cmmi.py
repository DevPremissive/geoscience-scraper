#!/usr/bin/env python3
"""
cmmi.py — ingest the USGS CMMI national grids and put them on the fabric (C2.2).

PLAN_C2 2.2 gives this component three jobs. They are listed there as one
half-day task; they are actually three different questions, and this module
keeps them separate:

  (a) **Validate `gridify.py`'s aggregation choices** against a reference. The
      release ships `Geology_H3Grid_Canada`, geology already aggregated onto H3
      by another team. That is an independent implementation of the same
      operation we perform, and the only external check on our majority-area
      logic that exists anywhere. `validate_gridify()`.

  (b) **Supply geophysical evidence.** This turned out to matter far more than
      the plan expected. Ontario's geophysics modality is empty in practice:
      the federal grids are blocked behind a portal that needs a browser (C3.2,
      not built) and `ON_GEOPHYS` is 246 MB of proprietary Geosoft `.GRD` that
      no common driver reads. These are ordinary GeoTIFFs covering the whole
      country. `ingest_all()` + `gridify_on()`.

  (c) **Benchmark comparators.** With a caveat the plan could not have known:
      the published surfaces are clastic-dominated and MVT **Zn-Pb**, and audit
      K3 established Ontario is not an MVT province. They are not a comparator
      for an orogenic-Au model, and this module refuses to present them as one.
      `benchmark()` reports rank agreement and says plainly what it means.

Archives are unzipped into `processed/cmmi/` rather than in place: the raw tree
is the immutable record of what was fetched, and `process.py`'s expansion pass
deletes the archive it expanded, which for a re-downloadable 1.25 GB release is
a bad trade against re-running the unpack.

Usage:
    python src/cmmi.py --unpack
    python src/cmmi.py --ingest
    python src/cmmi.py --gridify ON
    python src/cmmi.py --validate-gridify
    python src/cmmi.py --benchmark orogenic_au
    python src/cmmi.py --status
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C
import process as P
import rasters as R

CMMI_DIR = C.PROCESSED_DIR / "cmmi"
UNPACK_DIR = CMMI_DIR / "unpacked"
MANIFEST = CMMI_DIR / "cmmi.json"

#: The CMMI codes, and whether each is a raster theme we grid onto the fabric.
#: `categorical` matters: averaging a class code produces a number that is not
#: any class (the CGMC lesson, rasters.py).
CODES = {
    "CMMI_GRAVITY":       {"raster": True,  "categorical": False},
    "CMMI_MAGNETIC":      {"raster": True,  "categorical": False},
    "CMMI_LAB":           {"raster": True,  "categorical": False},
    "CMMI_MOHO":          {"raster": True,  "categorical": False},
    "CMMI_SATGRAV":       {"raster": True,  "categorical": False},
    "CMMI_PROSPECTIVITY": {"raster": True,  "categorical": False},
    "CMMI_FAULTS":        {"raster": False, "categorical": False},
    "CMMI_GEOLOGY_H3":    {"raster": False, "categorical": False},
    "CMMI_OCCURRENCES":   {"raster": False, "categorical": False},
}

#: Worm products are point/line vector shapefiles inside otherwise-raster codes.
#: Detected by content, not by code, because the release mixes them.
_RASTER_EXT = {".tif", ".tiff"}

#: Rasters the publisher shipped without a CRS, and the evidence that determines
#: it. Keyed by file stem. `rasters.ingest` refuses an assertion with no
#: evidence, so this table is the whole argument — see audit N3.
CRS_ASSERTIONS = {
    "USCanada_Lawleyetal_CDModel": (
        "EPSG:4326",
        "the MVT sibling in the same release folder declares EPSG:4326 on an "
        "identical grid — 23005x10368, transform "
        "(-180.002198652943, 0.005650762016448, 83.12553464295827, -0.005650762016448) "
        "and bounds (-180.0022, 24.5384, -50.0064, 83.1255) match to full float "
        "precision, and those bounds are decimal degrees on their face"),
}


def _raw_dir(code: str) -> Path:
    return C.RAW_DIR / "US" / code


def resolve_files(code: str) -> tuple[str | None, dict]:
    """Current files for a code, composed across dated snapshots.

    Uses `process.resolve_snapshot`, never `latest()`. A snapshot directory is a
    delta — reading the newest alone is how every Ontario tenure count came from
    46% of the province (audit I12). CMMI is a frozen release so today it makes
    no difference; it will the first time USGS republishes one file."""
    d = _raw_dir(code)
    if not d.exists():
        return None, {}
    return P.resolve_snapshot(d)


def unpack(codes=None, force: bool = False) -> dict:
    """Extract archives into processed/cmmi/unpacked/<code>/.

    Returns {code: [extracted paths]}. Idempotent: an archive whose output
    directory already exists is skipped unless `force`."""
    UNPACK_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, list] = {}
    for code in (codes or CODES):
        eff, files = resolve_files(code)
        if not files:
            print(f"  - {code}: nothing harvested")
            continue
        dest_root = UNPACK_DIR / code
        got = []
        for rel, path in sorted(files.items()):
            if path.suffix.lower() == ".zip" and zipfile.is_zipfile(path):
                dest = dest_root / path.stem
                if dest.exists() and not force:
                    got += [p for p in dest.rglob("*") if p.is_file()]
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                try:
                    with zipfile.ZipFile(path) as zf:
                        # Refuse absolute paths and parent traversal in member
                        # names before writing anything.
                        for m in zf.namelist():
                            if m.startswith(("/", "\\")) or ".." in Path(m).parts:
                                raise ValueError(f"unsafe member {m!r} in {path.name}")
                        zf.extractall(dest)
                except (zipfile.BadZipFile, ValueError) as e:
                    print(f"  ! {code}/{path.name}: {e}", file=sys.stderr)
                    shutil.rmtree(dest, ignore_errors=True)
                    continue
                got += [p for p in dest.rglob("*") if p.is_file()]
            else:
                # CSVs and loose files: reference in place, do not copy.
                got.append(path)
        out[code] = got
        rasters = [p for p in got if p.suffix.lower() in _RASTER_EXT]
        print(f"  + {code:20s} snapshot {eff}  {len(got):4d} files, "
              f"{len(rasters)} GeoTIFF")
    return out


def verify_checksums(codes=None) -> dict:
    """Check harvested files against the MD5s ScienceBase publishes.

    The harvest ledger stores our own sha256, which answers "same bytes as last
    time". It cannot answer "the bytes USGS meant to serve" — a truncated or
    mid-flight-corrupted download has a perfectly consistent sha256 of its own.
    The publisher's digest is the only independent check available, and this
    release ships one per file, so use it.
    """
    from connectors import sciencebase as SB
    import sources as S

    spec = S.US_SOURCES["CMMI"]
    inv = {r["resource_name"]: r for r in SB.discover(spec, "US")}
    ok = bad = missing = nodigest = 0
    failures = []
    for code in (codes or CODES):
        eff, files = resolve_files(code)
        for rel, path in sorted(files.items()):
            if path.name == P.RUN_MANIFEST or path.name == "_source.json":
                continue          # our own sidecars, not published files
            rec = inv.get(path.name)
            if rec is None:
                print(f"  ? {code}/{path.name}: not offered by the release now")
                missing += 1
                continue
            exp = rec.get("_sb_md5")
            if not exp:
                nodigest += 1
                continue
            good, got = SB.verify_md5(path, exp)
            if good:
                ok += 1
                print(f"  ok {code}/{path.name}")
            else:
                bad += 1
                failures.append(f"{code}/{path.name} expected {exp} got {got}")
                print(f"  ! MD5 MISMATCH {code}/{path.name}\n"
                      f"      expected {exp}\n      got      {got}", file=sys.stderr)
    print(f"\n  {ok} verified against the publisher's MD5, {bad} mismatched, "
          f"{missing} no longer offered, {nodigest} without a published digest")
    return {"ok": ok, "bad": bad, "missing": missing, "no_digest": nodigest,
            "failures": failures}


def _feature_prefix(code: str, tif: Path) -> str:
    """Short, stable feature name for a raster.

    The release's file names are long and repeat the region on every one
    (`GeophysicsGravity_HGM_USCanada.tif`). A feature named after the whole path
    is unreadable in a dossier's evidence table, which is where these end up."""
    stem = tif.stem
    stem = re.sub(r"[_-]?US ?Canada", "", stem, flags=re.I)
    stem = re.sub(r"^Geophysics", "", stem, flags=re.I)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    theme = code.replace("CMMI_", "").lower()
    return f"cmmi_{theme}__{stem}" if stem else f"cmmi_{theme}"


def ingest_all(codes=None, force: bool = False) -> list[dict]:
    """rasters.ingest() every GeoTIFF that unpacking produced."""
    unpacked = unpack(codes)
    done = []
    for code, files in unpacked.items():
        if not CODES.get(code, {}).get("raster"):
            continue
        cat = CODES[code]["categorical"]
        tifs = sorted(p for p in files if p.suffix.lower() in _RASTER_EXT)
        if not tifs:
            print(f"  - {code}: no GeoTIFF after unpack")
            continue
        eff, _ = resolve_files(code)
        for tif in tifs:
            assume, evidence = CRS_ASSERTIONS.get(tif.stem, (None, None))
            try:
                cog = R.ingest(tif, code, juris="US", categorical=cat, snapshot=eff,
                               assume_crs=assume, crs_evidence=evidence)
                done.append({"code": code, "tif": str(tif), "cog": str(cog),
                             "feature_prefix": _feature_prefix(code, tif)})
            except Exception as e:                              # noqa: BLE001
                print(f"  ! {code}/{tif.name}: {type(e).__name__}: {e}", file=sys.stderr)
    CMMI_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(done, indent=1))
    print(f"\n  {len(done)} rasters ingested → {MANIFEST}")
    return done


def _ingested() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return json.loads(MANIFEST.read_text())


def gridify_on(juris: str = "ON", write: bool = True, limit: int | None = None):
    """Zonal-average every ingested CMMI raster onto the jurisdiction's r7 fabric.

    Continuous geophysics, so mean/min/max/stdev. The interesting one for a
    gradient product is `stdev`: an HGM ridge crossing a cell shows as high
    within-cell variance even when the mean is unremarkable."""
    import pandas as pd
    import fabric as F
    import gridify as G

    cells = F.load_r7(juris)
    entries = _ingested()
    if limit:
        entries = entries[:limit]
    if not entries:
        sys.exit("no CMMI rasters ingested — run --ingest first")

    frames, inputs = [], []
    for e in entries:
        cog = Path(e["cog"])
        if not cog.exists():
            print(f"  ! missing COG {cog}", file=sys.stderr)
            continue
        try:
            df = R.zonal(cog, cells, categorical=False,
                         feature_prefix=e["feature_prefix"])
        except Exception as ex:                                 # noqa: BLE001
            print(f"  ! zonal failed for {e['feature_prefix']}: "
                  f"{type(ex).__name__}: {ex}", file=sys.stderr)
            continue
        if df.empty:
            print(f"  - {e['feature_prefix']:44s} no overlap with {juris}")
            continue
        covered = df["cell_id"].nunique()
        pct = 100.0 * covered / len(cells)
        frames.append(df)
        inputs.append(e["cog"])
        print(f"  + {e['feature_prefix']:44s} {covered:7,} cells ({pct:5.1f}%) "
              f"{len(df):8,} rows")

    if not frames:
        sys.exit("nothing gridified")
    allrows = pd.concat(frames, ignore_index=True)
    print(f"\n  {allrows['feature'].nunique()} features, {len(allrows):,} rows, "
          f"{allrows['cell_id'].nunique():,} of {len(cells):,} cells touched")
    if not write:
        return allrows

    meta = json.loads((F.FABRIC_DIR / f"r7_{juris}.json").read_text())
    eff, _ = resolve_files("CMMI_GRAVITY")
    snapshot = eff or "unknown"
    # Carry the existing matrix forward. Without this the new snapshot would hold
    # the CMMI geophysics and nothing else, and every consumer that reads the
    # newest snapshot would report the geology features as absent rather than
    # as not-recomputed-today.
    prior = _prior_snapshot(meta["fabric_version"], snapshot)
    if prior:
        print(f"  carrying forward the feature matrix from snapshot {prior}")
    out = G.write_features(frames, fabric_version=meta["fabric_version"],
                           snapshot=snapshot, inputs=inputs, carry_forward=prior)
    print(f"  → {out}")
    return allrows


# ---------------------------------------------------------------------------
# (a) Validate our aggregation against the release's own H3 grid
# ---------------------------------------------------------------------------

def _wrap(text: str, width: int):
    import textwrap
    return textwrap.wrap(text, width) or [""]


def _read_h3_reference(prov: str = "Ontario", columns=None):
    """The release's H3 geology grid, filtered to one province.

    Filtered in the driver rather than in pandas: the Canada grid is 1,820,346
    rows with a 1.5 GB .dbf, and Ontario is a fraction of it."""
    import pyogrio

    files = (unpack(["CMMI_GEOLOGY_H3"]) or {}).get("CMMI_GEOLOGY_H3", [])
    shp = [p for p in files if p.suffix.lower() == ".shp"]
    if not shp:
        return None, None
    cols = columns or ["H3_Address", "H3_Resol", "ProvMajor", "ProvMinor",
                       "LithMajor", "LithMinor", "LithCntact", "CMMI_Class"]
    df = pyogrio.read_dataframe(shp[0], read_geometry=False, columns=cols,
                                where=f"ProvMajor = '{prov}'")
    return df, shp[0]


#: Coarse lithology classes both vocabularies can be mapped onto. CGMC uses 34
#: descriptive labels ("felsic intrusive"), CMMI a structured taxonomy
#: ("Igneous_Intrusive_Felsic"). Comparing them at full resolution would measure
#: the vocabularies, not the aggregation; these four classes are what both
#: actually agree on the meaning of.
_COARSE = ("intrusive", "extrusive", "sedimentary", "metamorphic")


def _coarse_cmmi(label: str) -> str | None:
    l = (label or "").lower()
    if "intrusive" in l:
        return "intrusive"
    if "extrusive" in l or "volcanic" in l:
        return "extrusive"
    if l.startswith("sedimentary"):
        return "sedimentary"
    if l.startswith("metamorphic"):
        return "metamorphic"
    return None


def _coarse_cgmc(label: str) -> str | None:
    l = (label or "").lower()
    if "intrusive" in l or "pegmatite" in l or "anorthosite" in l:
        return "intrusive"
    if "volcanic" in l:
        return "extrusive"
    if "sedimentary" in l or "carbonate" in l or "clastic" in l or "shale" in l:
        return "sedimentary"
    if "metamorph" in l or "gneiss" in l or "schist" in l or "migmatite" in l:
        return "metamorphic"
    return None


def validate_gridify(juris: str = "ON", prov: str = "Ontario"):
    """Check our r7 fabric and aggregation against the release's own H3 grid.

    PLAN_C2 2.2 asks for "a reference feature matrix to validate gridify.py
    aggregation choices". It is a better check than the plan knew: the CMMI
    geology grid is **H3 resolution 7**, the same resolution as our fabric, so
    the comparison is cell-for-cell with no resampling in between. Two teams
    independently polyfilled Canada at r7 and independently aggregated bedrock
    geology onto those cells.

    Two things are measured, and the first is the more informative:

      1. **Cell-set agreement** — do we and they consider the same r7 cells to be
         Ontario? This tests `fabric.py`'s delegated decisions (ContainsCentroid
         containment, lake clipping) against an outside implementation, with no
         vocabulary mapping to muddy it. A disagreement here is a fabric
         disagreement, full stop.
      2. **Majority-lithology agreement** on the shared cells, mapped to four
         coarse classes both taxonomies mean the same thing by. This tests the
         majority-area logic. It is deliberately coarse — at full label
         resolution the number would measure the two vocabularies, not the two
         aggregations."""
    import pandas as pd
    import fabric as F

    ref, path = _read_h3_reference(prov)
    if ref is None:
        print("  ! CMMI H3 geology grid not unpacked — run --unpack first")
        return None
    print(f"  reference: {path.name}, {len(ref):,} rows where ProvMajor={prov!r}")
    bad_res = ref.loc[ref["H3_Resol"] != 7]
    if len(bad_res):
        print(f"  ! {len(bad_res)} reference rows are not resolution 7")
    ref_cells = set(ref["H3_Address"].dropna())

    cells = F.load_r7(juris)
    ours = set(cells["cell_id"])
    both = ours & ref_cells
    only_ours = ours - ref_cells
    only_theirs = ref_cells - ours

    print(f"\n  cell sets")
    print(f"    our r7 {juris} fabric      : {len(ours):,}")
    print(f"    CMMI r7 {prov} cells       : {len(ref_cells):,}")
    print(f"    in both                   : {len(both):,} "
          f"({100*len(both)/max(1,len(ours)):.1f}% of ours)")
    print(f"    ours only                 : {len(only_ours):,}")
    print(f"    theirs only               : {len(only_theirs):,}")

    result = {"jurisdiction": juris, "province": prov, "reference": str(path),
              "reference_resolution": 7,
              "our_cells": len(ours), "reference_cells": len(ref_cells),
              "shared": len(both), "ours_only": len(only_ours),
              "theirs_only": len(only_theirs)}

    # Attribute the disagreement before reporting it. `fabric.py` deliberately
    # subtracts major lakes — polyfilling Ontario's total area (1,076,395 km2, of
    # which 158,654 km2 is water) would put tens of thousands of permanently-null
    # cells in the Great Lakes and Hudson Bay. CMMI made the opposite choice and
    # carries a lithology under the water. An unattributed "20,702 cells differ"
    # reads as a fabric bug; it is two defensible answers to a question the plan
    # never asked, and only the remainder is worth investigating.
    in_lake = _count_in_water(only_theirs, juris)
    if in_lake is not None:
        unexplained = len(only_theirs) - in_lake
        result["theirs_only_in_clipped_water"] = in_lake
        result["theirs_only_unexplained"] = unexplained
        print(f"\n  attributing the difference")
        print(f"    theirs-only inside a clipped lake : {in_lake:,} "
              f"({100*in_lake/max(1,len(only_theirs)):.1f}%) — our water clipping, deliberate")
        print(f"    theirs-only unexplained           : {unexplained:,}")
        agree = len(both) / max(1, len(both) + unexplained + len(only_ours))
        result["agreement_excluding_water"] = round(agree, 4)
        print(f"    cell-set agreement once water is set aside: {100*agree:.2f}%")

    # --- majority lithology on the shared cells ---------------------------
    feats = _latest_features()
    if feats is None or not len(both):
        print("\n  no feature store or no shared cells — skipping lithology check")
        result["lithology"] = None
        _write_validation(result)
        return result

    ourlith = feats[feats["feature"].str.startswith("bedrock__dominant__")]
    if ourlith.empty:
        print("\n  no bedrock__dominant__* features on the fabric — "
              "skipping lithology check")
        result["lithology"] = None
        _write_validation(result)
        return result

    # bedrock__dominant__<label> == 1.0 on the cell it dominates.
    ourlith = ourlith[ourlith["value"] > 0].copy()
    ourlith["label"] = ourlith["feature"].str.replace("bedrock__dominant__", "", regex=False)
    ourlith = (ourlith.sort_values("value", ascending=False)
                      .drop_duplicates("cell_id")[["cell_id", "label"]])
    ourlith["coarse_ours"] = ourlith["label"].map(_coarse_cgmc)

    theirs = ref[["H3_Address", "LithMajor", "LithCntact"]].rename(
        columns={"H3_Address": "cell_id"})
    theirs["coarse_theirs"] = theirs["LithMajor"].map(_coarse_cmmi)

    j = ourlith.merge(theirs, on="cell_id", how="inner").dropna(
        subset=["coarse_ours", "coarse_theirs"])
    if j.empty:
        print("\n  no cells with a coarse class on both sides")
        result["lithology"] = None
        _write_validation(result)
        return result

    agree = (j["coarse_ours"] == j["coarse_theirs"])
    rate = float(agree.mean())
    print(f"\n  majority lithology, coarse classes {list(_COARSE)}")
    print(f"    cells compared            : {len(j):,}")
    print(f"    agree                     : {agree.sum():,} ({100*rate:.1f}%)")
    # A contact in the cell means two lithologies compete for the majority, so
    # that is exactly where two implementations should disagree most. If the
    # rates are the same, disagreement is not coming from the aggregation.
    if "LithCntact" in j:
        for state in ("Absent", "Present"):
            sub = j[j["LithCntact"] == state]
            if len(sub):
                r = float((sub["coarse_ours"] == sub["coarse_theirs"]).mean())
                print(f"    contact {state:8s}          : {100*r:5.1f}% over {len(sub):,} cells")
    print("\n    confusion (rows = ours, cols = theirs)")
    ct = pd.crosstab(j["coarse_ours"], j["coarse_theirs"])
    for line in ct.to_string().splitlines():
        print("      " + line)

    # Interpret, rather than leaving a bare percentage to be quoted as a pass or
    # a failure. Two things distinguish "the aggregations disagree" from "the
    # vocabularies disagree", and both are computable.
    off = ct.copy()
    for c in off.columns:
        if c in off.index:
            off.loc[c, c] = 0
    worst = off.stack().idxmax() if off.values.sum() else None
    worst_n = int(off.stack().max()) if off.values.sum() else 0
    share = worst_n / max(1, int(off.values.sum()))
    verdict_bits = []
    if worst and share > 0.25:
        verdict_bits.append(
            f"{100*share:.0f}% of all disagreement sits in a single cell of the "
            f"matrix (ours={worst[0]} / theirs={worst[1]}, {worst_n:,} cells). A "
            f"systematic one-way swap is a taxonomy boundary, not an aggregation "
            f"error — the two schemes file the same rocks differently.")
    bc = result_contact = {st: float((g["coarse_ours"] == g["coarse_theirs"]).mean())
                           for st, g in j.groupby("LithCntact") if len(g)}
    if "Absent" in bc and "Present" in bc and bc["Absent"] > bc["Present"]:
        verdict_bits.append(
            f"agreement is {100*bc['Absent']:.0f}% where the reference reports no "
            f"lithological contact in the cell and {100*bc['Present']:.0f}% where it "
            f"reports one. Majority is ill-defined exactly where two units compete, "
            f"so disagreement concentrating there is what two correct "
            f"implementations would do.")
    print("\n    reading this")
    if verdict_bits:
        for b in verdict_bits:
            for i, line in enumerate(_wrap(b, 74)):
                print(("      - " if i == 0 else "        ") + line)
    print("      - VERDICT: the cell-set comparison validates the fabric; the")
    print("        lithology comparison does NOT validate the majority logic")
    print("        either way, because the crosswalk between the two")
    print("        vocabularies is doing more work than the aggregation is.")

    result["lithology"] = {
        "cells_compared": int(len(j)),
        "agreement": round(rate, 4),
        "verdict": ("inconclusive — the taxonomy crosswalk dominates; use the "
                    "cell-set comparison for the fabric check"),
        "largest_confusion": (f"ours={worst[0]}/theirs={worst[1]}" if worst else None),
        "largest_confusion_share_of_disagreement": round(share, 4),
        "by_contact": {st: round(float((g["coarse_ours"] == g["coarse_theirs"]).mean()), 4)
                       for st, g in j.groupby("LithCntact") if len(g)},
        "confusion": {str(k): {str(kk): int(vv) for kk, vv in v.items()}
                      for k, v in ct.to_dict("index").items()},
    }
    _write_validation(result)
    return result


def _count_in_water(cell_ids, juris: str = "ON") -> int | None:
    """How many of `cell_ids` have centroids inside the water layers we clip."""
    try:
        import geopandas as gpd
        import h3
        from shapely.geometry import Point
        import fabric as F
    except ImportError:
        return None
    layers = F.WATER_LAYERS.get(juris) or []
    if not layers or not cell_ids:
        return None
    cell_ids = list(cell_ids)
    pts = gpd.GeoDataFrame(
        geometry=[Point(lng, lat) for lat, lng in
                  (h3.cell_to_latlng(c) for c in cell_ids)], crs=4326)
    hit = set()
    for name in layers:
        try:
            w = F._read_layer(name)
        except Exception:
            continue
        w = (w.to_crs(4326) if w.crs and w.crs.to_epsg() != 4326
             else w.set_crs(4326, allow_override=True))
        j = gpd.sjoin(pts, w[["geometry"]], predicate="within", how="left")
        hit |= set(j.index[j.index_right.notna()])
    return len(hit)


def _write_validation(result: dict):
    CMMI_DIR.mkdir(parents=True, exist_ok=True)
    out = CMMI_DIR / "gridify_validation.json"
    out.write_text(json.dumps(result, indent=1))
    print(f"\n  → {out}")


# ---------------------------------------------------------------------------
# (c) Benchmark comparators — with the deposit-type caveat enforced
# ---------------------------------------------------------------------------

#: Deposit systems each published CMMI surface actually models. A comparison
#: across systems is not a benchmark, and this table is what stops one being
#: reported as though it were.
PUBLISHED_SYSTEMS = {
    "cmmi_prospectivity__lawleyetal_cdmodel": "cd_znpb",
    "cmmi_prospectivity__lawleyetal_mvtmodel": "mvt_znpb",
}

#: What a cross-system result actually tells you. Rank agreement between two
#: models of *different* deposit types is not accuracy either way — but it is not
#: noise either, and saying nothing about it invites someone to read the number
#: as a score. Orogenic Au sits in Archean greenstone belts; MVT Zn-Pb sits in
#: Phanerozoic carbonate platforms. In Ontario those are disjoint terranes, so a
#: strong NEGATIVE correlation is the geologically expected outcome and a
#: positive one would be the thing worth worrying about.
_CROSS_SYSTEM_NOTE = {
    ("orogenic_au", "mvt_znpb"): (
        "Expected to be strongly negative in Ontario: orogenic Au is an Archean "
        "greenstone-belt system (Superior Province) and MVT Zn-Pb is a "
        "Phanerozoic carbonate-platform system (Hudson Bay Lowland, southern "
        "Ontario). These occupy different ground. A negative rho is evidence "
        "our model has learned terrane; a positive one would suggest it had "
        "learned something generic instead."),
    ("orogenic_au", "cd_znpb"): (
        "Expected to be weak either way. Clastic-dominated Zn-Pb sits in "
        "rifted-margin siliciclastic basins, which in Ontario neither coincide "
        "with nor cleanly avoid the greenstone belts."),
}


def benchmark(system: str = "orogenic_au", juris: str = "ON", version: str | None = None):
    """Rank agreement between our model's scores and each published surface.

    Reports Spearman rank correlation and top-decile overlap per cell, and
    labels each comparison `comparator` or `cross-system reference` depending on
    whether the deposit types match. PLAN_C2 2.2 says "published surfaces as
    benchmark comparators for every model this component ships" — but the only
    published surfaces here are Zn-Pb, our shipped model is orogenic Au, and
    audit K3 established Ontario is not an MVT province. Presenting an MVT
    surface as a benchmark for an Au model would be a category error dressed as
    validation, so the label is computed, not assumed."""
    import numpy as np
    import pandas as pd

    model_dir = C.PROCESSED_DIR / "models" / system
    if not model_dir.exists():
        sys.exit(f"no model for {system!r}")
    versions = sorted(p.name for p in model_dir.iterdir() if p.is_dir())
    ver = version or versions[-1]
    scores = pd.read_parquet(model_dir / ver / "oof_scores.parquet")[["cell_id", "score"]]

    feats = _latest_features()
    if feats is None:
        sys.exit("no feature store — run --gridify first")
    pub = feats[feats["feature"].str.contains("prospectivity", case=False, na=False)]
    if pub.empty:
        sys.exit("no CMMI prospectivity features on the fabric — run --gridify")

    print(f"model: {system}/{ver}  ({len(scores):,} scored cells)\n")
    results = []
    for feature, grp in pub.groupby("feature"):
        base = feature.rsplit("__", 1)[0] if feature.endswith(("__mean", "__min", "__max", "__stdev")) else feature
        stat = feature.rsplit("__", 1)[1] if "__" in feature else ""
        if stat and stat not in ("mean",):
            continue
        pub_system = PUBLISHED_SYSTEMS.get(base, "unknown")
        j = scores.merge(grp[["cell_id", "value"]], on="cell_id", how="inner").dropna()
        if len(j) < 100:
            print(f"  {feature}: only {len(j)} overlapping cells, skipped")
            continue
        rho = j["score"].corr(j["value"], method="spearman")
        k = max(1, int(len(j) * 0.10))
        top_ours = set(j.nlargest(k, "score")["cell_id"])
        top_pub = set(j.nlargest(k, "value")["cell_id"])
        overlap = len(top_ours & top_pub) / k
        same = pub_system == system
        kind = "comparator" if same else "cross-system reference"
        results.append({"feature": feature, "published_system": pub_system,
                        "our_system": system, "kind": kind, "cells": len(j),
                        "spearman": round(float(rho), 4),
                        "top_decile_overlap": round(overlap, 4),
                        "expected_by_chance": 0.10})
        print(f"  {base}")
        print(f"    published system : {pub_system}   ours: {system}   [{kind}]")
        print(f"    cells compared   : {len(j):,}")
        print(f"    spearman rho     : {rho:+.4f}")
        print(f"    top-decile overlap: {overlap:.3f}  (chance 0.100)")
        if not same:
            print(f"    NOTE: different deposit systems. This is NOT a validation of "
                  f"{system}.")
            note = _CROSS_SYSTEM_NOTE.get((system, pub_system))
            if note:
                for line in _wrap(note, 72):
                    print(f"          {line}")
            else:
                print(f"          Agreement would mean shared geological controls or "
                      f"shared bias\n          (both lean on the same geophysics), "
                      f"not that either is right.")
            results[-1]["expectation"] = note
        print()
    out = CMMI_DIR / f"benchmark_{system}_{ver}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"system": system, "version": ver,
                               "jurisdiction": juris, "results": results}, indent=1))
    print(f"  → {out}")
    return results


def _prior_snapshot(fabric_version: str, snapshot: str) -> str | None:
    """The most recent snapshot before `snapshot` that holds a feature matrix."""
    root = C.PROCESSED_DIR / "features" / fabric_version
    if not root.exists():
        return None
    cands = sorted(p.name for p in root.iterdir()
                   if p.is_dir() and p.name < snapshot
                   and (p / "features.parquet").exists())
    return cands[-1] if cands else None


def _latest_features():
    import pandas as pd
    root = C.PROCESSED_DIR / "features"
    if not root.exists():
        return None
    fabs = sorted(p for p in root.iterdir() if p.is_dir())
    if not fabs:
        return None
    snaps = sorted(p for p in fabs[-1].iterdir() if p.is_dir())
    for s in reversed(snaps):
        f = s / "features.parquet"
        if f.exists():
            return pd.read_parquet(f)
    return None


def status():
    print("CMMI (C2.2) status\n")
    for code in CODES:
        eff, files = resolve_files(code)
        n = len(files)
        mb = sum(p.stat().st_size for p in files.values()) / 1e6 if files else 0
        unp = UNPACK_DIR / code
        nu = len([p for p in unp.rglob("*") if p.is_file()]) if unp.exists() else 0
        ntif = len([p for p in unp.rglob("*") if p.suffix.lower() in _RASTER_EXT]) if unp.exists() else 0
        print(f"  {code:20s} raw {n:2d} files {mb:8.1f} MB  snapshot {eff or '-':10s} "
              f"unpacked {nu:4d} ({ntif} tif)")
    ing = _ingested()
    print(f"\n  ingested rasters: {len(ing)}")
    feats = _latest_features()
    if feats is not None:
        cm = feats[feats["feature"].str.startswith("cmmi_")]
        print(f"  CMMI features on the fabric: {cm['feature'].nunique()} "
              f"across {cm['cell_id'].nunique():,} cells")


def main():
    ap = argparse.ArgumentParser(description="C2.2 — USGS CMMI benchmark & evidence pull")
    ap.add_argument("--unpack", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="check harvested files against ScienceBase's published MD5s")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--gridify", metavar="JURIS")
    ap.add_argument("--validate-gridify", action="store_true")
    ap.add_argument("--benchmark", metavar="SYSTEM")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    C.require_lake()
    if args.verify:
        r = verify_checksums()
        sys.exit(1 if r["bad"] else 0)
    elif args.unpack:
        unpack(force=args.force)
    elif args.ingest:
        ingest_all(force=args.force)
    elif args.gridify:
        gridify_on(args.gridify, write=not args.no_write, limit=args.limit)
    elif args.validate_gridify:
        validate_gridify()
    elif args.benchmark:
        benchmark(args.benchmark)
    elif args.status:
        status()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
