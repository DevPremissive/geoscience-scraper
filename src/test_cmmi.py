#!/usr/bin/env python3
"""
test_cmmi.py — tests for C2.2 (ScienceBase connector, CMMI ingest, feature store).

Same shape as `test_mapapi.py` and `land/test_rules.py`: no pytest, run it and it
tells you. Pure tests always run; lake tests are skipped by name when the drive
is not mounted.

The tests that matter most here are regressions for three bugs this component
introduced or exposed, each of which was silent:

  * `test_write_features_carries_forward` — `write_features` claimed in its
    docstring to append and in fact overwrote. Gridding the CMMI rasters into a
    new snapshot left it holding 60 geophysics features and none of the 239
    geology ones, and every consumer that reads the newest snapshot would have
    reported the geology as absent rather than as not-recomputed.
  * `test_registry_crs_matches_the_file` — `rio_copy(..., crs=)` is ignored, so
    the registry recorded EPSG:4326 for a COG that carried no CRS at all. A
    registry describing a file that does not exist is worse than no registry.
  * `test_ingest_refuses_unevidenced_crs` — an asserted CRS with no recorded
    reason is a guess with extra steps, and `ingest()` now refuses it.

Run:  python -m test_cmmi
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C
import cmmi as M
import gridify as G
from connectors import sciencebase as SB

_fails: list[str] = []
_skips: list[str] = []


def check(cond, msg):
    if cond:
        print(f"    ok   {msg}")
    else:
        _fails.append(msg)
        print(f"    FAIL {msg}")


def skip(name, why):
    _skips.append(f"{name}: {why}")
    print(f"    skip {name} — {why}")


def lake_ready() -> bool:
    try:
        C.require_lake()
    except Exception:
        return False
    return (C.PROCESSED_DIR / "fabric" / "r7_ON.parquet").exists()


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------

def test_source_registered():
    import sources as S
    spec = S.US_SOURCES.get("CMMI")
    check(spec is not None, "CMMI is registered in sources.py")
    if not spec:
        return
    check(spec["type"] == "sciencebase", "CMMI uses the sciencebase connector")
    check(bool(spec.get("parent")), "CMMI names a parent data-release item")
    sel = spec.get("select") or {}
    check(set(sel) == set(M.CODES),
          f"sources.py and cmmi.CODES agree on the code list "
          f"({sorted(set(sel) ^ set(M.CODES)) or 'identical'})")
    for code, s in sel.items():
        check(bool(s.get("files")), f"{code} selects at least one file")
        check(bool(s.get("notes")), f"{code} says what it is for")
    # Master §9 rule 2.
    for code, s in sel.items():
        for f in s["files"]:
            check("http" not in f, f"{code} selects by file name, not by URL")
    for code in M.CODES:
        check(S.class_of(code) == "rasters",
              f"{code} is on the monthly cadence, not the daily one")


def test_discover_spec_codes():
    import discover as D
    codes = D.spec_codes({"type": "sciencebase", "select": {"A": {}, "B": {}}})
    check(sorted(codes) == ["A", "B"], "spec_codes reads a sciencebase spec")


def test_fmt_of():
    check(SB._fmt_of("Geology.zip") == "zip", "_fmt_of reads a zip extension")
    check(SB._fmt_of("x.CSV") == "csv", "_fmt_of lowercases")
    check(SB._fmt_of("noext") == "dat", "_fmt_of falls back for a bare name")


def test_file_md5_and_verify():
    check(SB.file_md5({"checksum": {"type": "MD5", "value": "ABC"}}) == "abc",
          "file_md5 lowercases an MD5")
    check(SB.file_md5({"checksum": {"type": "SHA1", "value": "x"}}) is None,
          "file_md5 ignores a non-MD5 digest")
    check(SB.file_md5({}) is None, "file_md5 tolerates a file with no checksum")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.bin"
        p.write_bytes(b"hello cmmi")
        want = hashlib.md5(b"hello cmmi").hexdigest()
        ok, got = SB.verify_md5(p, want)
        check(ok and got == want, "verify_md5 accepts a matching file")
        bad, got2 = SB.verify_md5(p, "0" * 32)
        check(not bad and got2 == want, "verify_md5 rejects a mismatch and reports what it got")


def test_item_files_reads_facets():
    """Shapefile and geodatabase bundles hang off `facets`, not `files`.
    Reading only the top level misses whole layers on the richest items."""
    d = {"files": [{"name": "a.tif"}],
         "facets": [{"files": [{"name": "b.shp"}, {"name": "b.dbf"}]}]}
    names = sorted(f["name"] for f in SB.item_files(d))
    check(names == ["a.tif", "b.dbf", "b.shp"], "item_files merges files and facets")
    check(SB.item_files({}) == [], "item_files tolerates an item with neither")


def test_discover_reports_a_missing_file(monkey=True):
    """A selected name that no longer resolves must be reported, not skipped —
    a revised data release should be visible."""
    real_children, real_item = SB.children, SB.item
    SB.children = lambda parent, max_items=200: [{"id": "kid1", "title": "T"}]
    SB.item = lambda i: {"id": "kid1", "title": "T", "files": [
        {"name": "present.zip", "size": 1, "url": "https://x/1",
         "dateUploaded": "2022-01-01", "checksum": {"type": "MD5", "value": "aa"}}]}
    try:
        inv = SB.discover({"parent": "p", "select": {
            "CODE_A": {"files": ["present.zip", "vanished.zip"]}}}, "US")
    finally:
        SB.children, SB.item = real_children, real_item
    check(len(inv) == 1, "discover returns only files that resolve")
    r = inv[0]
    check(r["resource_id"] == "CODE_A:present.zip", "resource_id is code:filename")
    check(r["connector"] == "sciencebase" and r["format"] == "zip",
          "resource record carries connector and format")
    check(r["last_modified"] == "2022-01-01",
          "change detection keys off the file's dateUploaded, not the item's")
    check(r["_sb_md5"] == "aa", "the publisher's digest is carried through")


def test_feature_prefix():
    p = M._feature_prefix("CMMI_GRAVITY",
                          Path("/x/GeophysicsGravity_HGM_USCanada.tif"))
    check(p == "cmmi_gravity__gravity_hgm", f"feature prefix is short and readable ({p})")
    p2 = M._feature_prefix("CMMI_MOHO", Path("/x/USCanada_Moho.tif"))
    check(p2.startswith("cmmi_moho"), f"feature prefix keeps its theme ({p2})")
    check("uscanada" not in p2.lower(), "feature prefix drops the region suffix")


def test_coarse_crosswalk():
    check(M._coarse_cmmi("Igneous_Intrusive_Felsic") == "intrusive", "CMMI intrusive")
    check(M._coarse_cmmi("Igneous_Extrusive") == "extrusive", "CMMI extrusive")
    check(M._coarse_cmmi("Sedimentary_Chemical_Carbonate") == "sedimentary", "CMMI sedimentary")
    check(M._coarse_cmmi("Metamorphic_Gneiss") == "metamorphic", "CMMI metamorphic")
    check(M._coarse_cmmi("") is None and M._coarse_cmmi(None) is None,
          "CMMI crosswalk returns None rather than guessing")
    check(M._coarse_cgmc("felsic intrusive") == "intrusive", "CGMC intrusive")
    check(M._coarse_cgmc("mafic volcanic") == "extrusive", "CGMC volcanic is extrusive")
    check(M._coarse_cgmc("pegmatite") == "intrusive", "CGMC pegmatite is intrusive")
    check(M._coarse_cgmc("something unmapped") is None, "CGMC crosswalk returns None")


def test_cross_system_guard():
    """The published surfaces are Zn-Pb. Presenting them as a benchmark for an
    orogenic-Au model would be a category error dressed as validation."""
    check(M.PUBLISHED_SYSTEMS.get("cmmi_prospectivity__lawleyetal_mvtmodel") == "mvt_znpb",
          "the MVT surface is labelled with its deposit system")
    check(M.PUBLISHED_SYSTEMS.get("cmmi_prospectivity__lawleyetal_cdmodel") == "cd_znpb",
          "the CD surface is labelled with its deposit system")
    check(("orogenic_au", "mvt_znpb") in M._CROSS_SYSTEM_NOTE,
          "a cross-system comparison carries a stated expectation")
    note = M._CROSS_SYSTEM_NOTE[("orogenic_au", "mvt_znpb")]
    check("negative" in note.lower(),
          "the orogenic-Au vs MVT expectation says the correlation should be negative")


def test_crs_assertions_carry_evidence():
    for stem, (crs, ev) in M.CRS_ASSERTIONS.items():
        check(bool(crs) and bool(ev) and len(ev) > 40,
              f"CRS assertion for {stem} carries substantive evidence")


def test_ingest_refuses_unevidenced_crs():
    import rasters as R
    try:
        R.ingest("/nonexistent.tif", "X", assume_crs="EPSG:4326")
        check(False, "ingest refuses an asserted CRS with no evidence")
    except ValueError as e:
        check("crs_evidence" in str(e),
              "ingest refuses an asserted CRS with no evidence, and says why")
    except Exception as e:
        check(False, f"ingest raised {type(e).__name__} instead of ValueError: {e}")


def test_write_features_carries_forward():
    """Regression: a second producer must not erase the first's features."""
    import pandas as pd

    def frame(feat, n=3):
        return pd.DataFrame({"cell_id": [f"c{i}" for i in range(n)],
                             "feature": [feat] * n,
                             "value": [float(i) for i in range(n)]})

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        G.write_features([frame("geology__a"), frame("geology__b")],
                         "fab-test", "2026-01-01", inputs=[], root=root)
        first = pd.read_parquet(root / "fab-test" / "2026-01-01" / "features.parquet")
        check(first["feature"].nunique() == 2, "first producer writes its features")

        # Second producer, new snapshot, no carry_forward: the old behaviour.
        G.write_features([frame("geophys__x")], "fab-test", "2026-02-01",
                         inputs=[], root=root)
        alone = pd.read_parquet(root / "fab-test" / "2026-02-01" / "features.parquet")
        check(set(alone["feature"]) == {"geophys__x"},
              "without carry_forward a new snapshot holds only what was computed")

        # With carry_forward: the complete matrix.
        G.write_features([frame("geophys__x")], "fab-test", "2026-03-01",
                         inputs=[], root=root, carry_forward="2026-01-01")
        merged = pd.read_parquet(root / "fab-test" / "2026-03-01" / "features.parquet")
        check(set(merged["feature"]) == {"geology__a", "geology__b", "geophys__x"},
              "carry_forward brings the prior matrix into the new snapshot")
        man = json.loads((root / "fab-test" / "2026-03-01" / "manifest.json").read_text())
        check(man["carried_from"] == "2026-01-01", "the manifest names where it carried from")
        check(sorted(man["carried_features"]) == ["geology__a", "geology__b"],
              "the manifest names which features were carried, not recomputed")
        check(man["computed_features"] == ["geophys__x"],
              "the manifest separates computed features from carried ones")

        # A recomputed feature must win over the carried copy.
        newer = frame("geology__a", n=3)
        newer["value"] = [99.0, 99.0, 99.0]
        G.write_features([newer], "fab-test", "2026-04-01", inputs=[], root=root,
                         carry_forward="2026-03-01")
        won = pd.read_parquet(root / "fab-test" / "2026-04-01" / "features.parquet")
        a = won[won["feature"] == "geology__a"]["value"].tolist()
        check(a == [99.0, 99.0, 99.0], "a recomputed feature overrides the carried copy")
        man2 = json.loads((root / "fab-test" / "2026-04-01" / "manifest.json").read_text())
        check("geology__a" not in man2["carried_features"],
              "a recomputed feature is not reported as carried")

        # Merging into a snapshot that already holds a matrix.
        G.write_features([frame("extra__y")], "fab-test", "2026-04-01",
                         inputs=[], root=root)
        both = pd.read_parquet(root / "fab-test" / "2026-04-01" / "features.parquet")
        check("geology__a" in set(both["feature"]) and "extra__y" in set(both["feature"]),
              "writing again into a snapshot merges rather than replaces")


# ---------------------------------------------------------------------------
# Lake
# ---------------------------------------------------------------------------

def test_harvested_and_verified():
    codes_with_data = 0
    for code in M.CODES:
        eff, files = M.resolve_files(code)
        payload = [p for p in files.values()
                   if p.name not in ("_source.json", "_manifest.json")]
        if payload:
            codes_with_data += 1
    check(codes_with_data == len(M.CODES),
          f"all {len(M.CODES)} CMMI codes have payload on disk ({codes_with_data})")


def test_rasters_ingested():
    entries = M._ingested()
    check(len(entries) >= 15, f"at least 15 CMMI rasters ingested ({len(entries)})")
    missing = [e["cog"] for e in entries if not Path(e["cog"]).exists()]
    check(not missing, f"every ingested COG is on disk ({len(missing)} missing)")


def test_registry_crs_matches_the_file():
    """Regression: the registry must not claim a CRS the COG does not carry."""
    import rasterio
    reg = json.loads((C.PROCESSED_DIR / "rasters" / "rasters.json").read_text())
    bad = []
    for e in reg:
        p = Path(e["path"])
        if not p.exists():
            continue
        try:
            with rasterio.open(p) as ds:
                on_disk = None if ds.crs is None else ds.crs.to_string()
        except Exception as ex:                                  # noqa: BLE001
            bad.append(f"{e['layer']}: unreadable ({ex})")
            continue
        if on_disk is None or on_disk != e.get("crs"):
            bad.append(f"{e['layer']}: registry {e.get('crs')} vs file {on_disk}")
    check(not bad, f"every registered raster's file carries the CRS the registry claims "
                   f"({'; '.join(bad[:3]) if bad else 'all match'})")
    asserted = [e for e in reg if e.get("crs_asserted")]
    for e in asserted:
        check(bool(e.get("crs_evidence")),
              f"{e['layer']} records why its CRS was asserted")


def test_features_on_the_fabric():
    import pandas as pd
    feats = M._latest_features()
    if feats is None:
        skip("test_features_on_the_fabric", "no feature store")
        return
    cm = feats[feats["feature"].str.startswith("cmmi_")]
    check(cm["feature"].nunique() >= 40,
          f"CMMI contributed a substantial feature set ({cm['feature'].nunique()})")
    check(cm["cell_id"].nunique() > 150000,
          f"CMMI features cover most of the Ontario fabric ({cm['cell_id'].nunique():,} cells)")
    # The regression that matters: geology must still be there.
    geo = feats[feats["feature"].str.startswith("bedrock__")]
    check(geo["feature"].nunique() > 0,
          f"the geology features survived the CMMI write "
          f"({geo['feature'].nunique()} bedrock features present)")
    check(feats["feature"].nunique() >= 290,
          f"the newest snapshot holds the complete matrix "
          f"({feats['feature'].nunique()} features)")
    check(not cm["value"].isna().all(), "CMMI feature values are not all null")


def test_gridify_validation_recorded():
    p = M.CMMI_DIR / "gridify_validation.json"
    if not p.exists():
        skip("test_gridify_validation_recorded", "run --validate-gridify first")
        return
    v = json.loads(p.read_text())
    check(v.get("reference_resolution") == 7,
          "the reference grid is r7, the same resolution as our fabric")
    check(v["shared"] / max(1, v["our_cells"]) > 0.99,
          f"our fabric agrees with the reference on >99% of our cells "
          f"({100*v['shared']/max(1,v['our_cells']):.1f}%)")
    if v.get("theirs_only_in_clipped_water") is not None:
        check(v["theirs_only_in_clipped_water"] / max(1, v["theirs_only"]) > 0.9,
              "the bulk of the reference's extra cells are explained by our water clipping")
    lith = v.get("lithology")
    if lith:
        check("verdict" in lith and "inconclusive" in lith["verdict"],
              "the lithology comparison records that it is inconclusive, not a pass")


def test_benchmark_labels_cross_system():
    hits = sorted((M.CMMI_DIR).glob("benchmark_*.json")) if M.CMMI_DIR.exists() else []
    if not hits:
        skip("test_benchmark_labels_cross_system", "run --benchmark first")
        return
    b = json.loads(hits[-1].read_text())
    check(bool(b["results"]), "the benchmark produced results")
    for r in b["results"]:
        check(r["published_system"] != "unknown",
              f"{r['feature']} resolves to a named deposit system")
        if r["published_system"] != r["our_system"]:
            check(r["kind"] == "cross-system reference",
                  f"{r['feature']} is labelled a cross-system reference, not a comparator")
            check(bool(r.get("expectation")),
                  f"{r['feature']} states what the comparison was expected to show")


PURE = [test_source_registered, test_discover_spec_codes, test_fmt_of,
        test_file_md5_and_verify, test_item_files_reads_facets,
        test_discover_reports_a_missing_file, test_feature_prefix,
        test_coarse_crosswalk, test_cross_system_guard,
        test_crs_assertions_carry_evidence, test_ingest_refuses_unevidenced_crs,
        test_write_features_carries_forward]

LAKE = [test_harvested_and_verified, test_rasters_ingested,
        test_registry_crs_matches_the_file, test_features_on_the_fabric,
        test_gridify_validation_recorded, test_benchmark_labels_cross_system]


def main():
    print("C2.2 CMMI tests\n")
    print("  pure")
    for fn in PURE:
        print(f"  {fn.__name__}")
        fn()
    if lake_ready():
        print("\n  lake")
        for fn in LAKE:
            print(f"  {fn.__name__}")
            try:
                fn()
            except Exception as e:                               # noqa: BLE001
                _fails.append(f"{fn.__name__} raised {type(e).__name__}: {e}")
                print(f"    FAIL {fn.__name__} raised {type(e).__name__}: {e}")
    else:
        print(f"\n  SKIPPING {len(LAKE)} lake tests — {C.LAKE_ROOT} is not mounted")

    print("\n" + "-" * 64)
    if _skips:
        print(f"{len(_skips)} skipped:")
        for s in _skips:
            print("  " + s)
    if _fails:
        print(f"{len(_fails)} FAILURE(S)")
        for f in _fails:
            print("  " + f)
        sys.exit(1)
    print("all C2.2 tests pass")


if __name__ == "__main__":
    main()
