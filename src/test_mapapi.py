#!/usr/bin/env python3
"""
test_mapapi.py — tests for the C4.2 map service.

Same shape as `land/test_rules.py`: no pytest, no fixtures framework, run it and
it tells you. Tests are in two groups.

  **Pure tests** exercise logic that has no business touching the lake — bbox
  parsing, JSON sanitising, the lon/lat transposition that is the classic H3
  bug, projection arithmetic, and the no-external-origin rule for the viewer.
  These always run.

  **Lake tests** need the artifacts on the bulk volume. They are skipped, loudly
  and by name, when the drive is not mounted, because a green run on an
  unmounted drive is worse than a red one.

Three of these are regressions for bugs found while building C4.2, and each is
here because it survived a passing smoke test:

  * `test_parquet_cache_is_keyed_on_columns` — the cache keyed only on
    (path, mtime), so whichever caller read `tenure_events.parquet` first won,
    and in a live server the events layer got a frame with no `cell_r7`.
  * `test_blocks_geometry_is_transformed_from_3978` — `blocks.geometry_wkt` is
    EPSG:3978, and a lon/lat envelope against it matches nothing, which reads as
    "no neighbours" rather than as an error.
  * `test_event_window_is_jurisdiction_scoped` — unfiltered, the scrubber range
    ran 1899→2027 off Yukon's paper record while the layer drew only Ontario.

Run:  python -m test_mapapi
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C
import mapapi as M

REPO = Path(__file__).resolve().parent.parent
VIEWER = REPO / "viewer"

#: A bbox over the Abitibi AOI, where every C1/C2 artifact actually has data.
BOX = (-81.5, 48.0, -80.0, 49.0)
#: The r9 cell the first dossier was written for.
CELL_R9 = "892b968aac7ffff"

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
# Pure tests
# ---------------------------------------------------------------------------

def test_parse_bbox():
    check(M.parse_bbox("-81.5,48,-80,49") == (-81.5, 48.0, -80.0, 49.0),
          "parse_bbox reads four ordered numbers")
    check(M.parse_bbox(None) == M.DEFAULT_BBOX, "parse_bbox falls back to Ontario")
    for bad, why in [("1,2,3", "three values"),
                     ("-80,49,-81,48", "inverted box"),
                     ("-80,48,-80,49", "zero width"),
                     ("-200,48,-80,49", "outside lon range"),
                     ("a,b,c,d", "non-numeric")]:
        try:
            M.parse_bbox(bad)
            check(False, f"parse_bbox rejects {why}")
        except ValueError:
            check(True, f"parse_bbox rejects {why}")


def test_cell_polygon_is_lon_lat():
    """h3 hands back (lat, lng) and GeoJSON wants (lng, lat). Transposed, an
    Ontario cell lands near (48, -80) read as lon=48 — in Kazakhstan — and the
    map renders empty rather than wrong-looking."""
    poly = M.cell_polygon(CELL_R9)
    ring = poly["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    check(poly["type"] == "Polygon", "cell_polygon returns a Polygon")
    check(all(-95 < x < -74 for x in lons), "cell_polygon x values are Ontario longitudes")
    check(all(41 < y < 57 for y in lats), "cell_polygon y values are Ontario latitudes")
    check(ring[0] == ring[-1], "cell_polygon ring is closed")


def test_json_safe():
    import numpy as np
    import pandas as pd
    payload = {"nan": float("nan"), "inf": float("inf"), "nat": pd.NaT,
               "np": np.float64(1.5), "npint": np.int64(3),
               "nested": [{"a": float("nan")}, 2], "s": "ok", "b": True,
               "ts": pd.Timestamp("2026-08-18")}
    out = M.json_safe(payload)
    text = json.dumps(out)  # would emit bare NaN/Infinity if any survived
    check("NaN" not in text and "Infinity" not in text,
          "json_safe leaves no non-JSON literals")
    check(out["nan"] is None and out["inf"] is None and out["nat"] is None,
          "json_safe maps NaN/inf/NaT to null")
    check(out["np"] == 1.5 and out["npint"] == 3, "json_safe unwraps numpy scalars")
    check(out["nested"][0]["a"] is None, "json_safe recurses into nested values")
    check(out["s"] == "ok" and out["b"] is True, "json_safe leaves good values alone")
    check(out["ts"].startswith("2026-08-18"), "json_safe isoformats timestamps")


def test_maybe_json():
    check(M._maybe_json('{"a": 1}') == {"a": 1}, "_maybe_json parses a JSON object string")
    check(M._maybe_json("not json") == "not json", "_maybe_json leaves plain text alone")
    check(M._maybe_json("{oops") == "{oops", "_maybe_json survives malformed JSON")
    check(M._maybe_json(None) is None, "_maybe_json passes None through")


def test_projection():
    box = (-82.0, 48.0, -80.0, 49.0)
    project = M._projector(box, 800, 400)
    x0, y0 = project(-82.0, 48.0)
    x1, y1 = project(-80.0, 49.0)
    check(abs(x0) < 1e-6 and abs(y0 - 400) < 1e-6, "projector puts bbox min at bottom-left")
    check(abs(x1 - 800) < 1e-6 and abs(y1) < 1e-6, "projector puts bbox max at top-right")
    mid_y = project(-81.0, 48.5)[1]
    check(0 < mid_y < 400, "projector keeps interior points inside the frame")
    # Mercator, not plate carrée. Over one degree at 48°N the difference is a
    # single pixel, so test it over a span where it is unmistakable: in Web
    # Mercator the midpoint latitude of 0–70°N sits well below half height.
    wide = M._projector((-10.0, 0.0, 10.0, 70.0), 100, 1000)
    equator_half = wide(0.0, 35.0)[1]
    check(equator_half > 600,
          f"projector is Web Mercator, not linear in latitude (35°N of 0–70°N "
          f"landed at y={equator_half:.0f} of 1000; linear would be 500)")


def test_ramp():
    check(M._ramp_colour(0.0) == M.RAMP[0], "ramp starts at its first colour")
    check(M._ramp_colour(1.0) == M.RAMP[-1], "ramp ends at its last colour")
    check(M._ramp_colour(-5) == M.RAMP[0] and M._ramp_colour(9) == M.RAMP[-1],
          "ramp clamps out-of-range inputs")
    check(M._ramp_colour(float("nan")) == M.RAMP[0], "ramp survives NaN")
    mid = M._ramp_colour(0.5)
    check(all(0 <= c <= 255 for c in mid), "ramp midpoint is a valid colour")


def test_plain_text_is_latin1():
    """Pillow's default bitmap font draws a box for anything outside latin-1."""
    out = M._plain("Abitibi — 45° × “quoted” © 2026")
    check(out.encode("latin-1", "strict"), "figure text encodes as latin-1")
    check("—" not in out and "×" not in out, "figure text substitutes typographic glyphs")


def test_cell_bbox_contains_its_cell():
    box = M.cell_bbox(CELL_R9, pad=3.0)
    import h3
    lat, lng = h3.cell_to_latlng(CELL_R9)
    check(box[0] < lng < box[2] and box[1] < lat < box[3],
          "cell_bbox contains the cell centroid")
    tight = M.cell_bbox(CELL_R9, pad=0.0)
    check((box[2] - box[0]) > (tight[2] - tight[0]),
          "cell_bbox pad widens the frame")


def test_viewer_makes_no_external_request():
    """The rule from viewer/vendor/NOTICE.md, asserted rather than trusted.

    A basemap or CDN reference sends the viewport — the ground we are looking at
    — to a third party on every pan. Master §8 says heat is public and the
    archive is the moat; the viewport is the part worth not broadcasting."""
    if not VIEWER.exists():
        skip("test_viewer_makes_no_external_request", "viewer/ not present")
        return
    files = [p for p in VIEWER.rglob("*")
             if p.suffix in (".html", ".js", ".css") and "vendor" not in p.parts]
    files.append(REPO / "src" / "serve.py")
    pattern = re.compile(r"""https?://(?!127\.0\.0\.1|localhost)[\w.-]+""")
    offenders = []
    for f in files:
        if not f.exists():
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            # Licence URLs and prose in comments are not requests.
            stripped = line.strip()
            if stripped.startswith(("*", "//", "#", "<!--")) or "License" in line:
                continue
            for m in pattern.finditer(line):
                offenders.append(f"{f.relative_to(REPO)}:{i} {m.group(0)}")
    check(not offenders,
          f"no external origin referenced by the viewer or serve.py "
          f"{'(' + '; '.join(offenders[:4]) + ')' if offenders else ''}")

    idx = VIEWER / "index.html"
    if idx.exists():
        html = idx.read_text()
        check("/viewer/vendor/maplibre-gl.js" in html,
              "viewer loads MapLibre from the vendored copy")
    app = VIEWER / "app.js"
    if app.exists():
        js = app.read_text()
        # The style object itself, not the prose about it: a `glyphs:` or
        # `sprite:` key is what makes MapLibre fetch from another origin.
        declared = re.findall(r"^\s*(glyphs|sprite)\s*:", js, re.M)
        check(not declared,
              f"map style declares no glyph or sprite origin (found {declared})")


def test_render_layer_names_are_explicit():
    check(set(M.RENDER_LAYERS) >= {"context", "claims", "land", "heat",
                                   "prospectivity", "criticality", "blocks", "events"},
          "/render exposes every layer the viewer can show")
    check(M.STROKE_ONLY <= set(M.RENDER_LAYERS),
          "stroke-only layers are all renderable layers")


def test_render_png_of_synthetic_layers():
    """Rendering does not need the lake — it needs Layer objects."""
    feats = [
        M.Feature({"type": "Polygon", "coordinates": [[(-81.4, 48.1), (-81.0, 48.1),
                                                       (-81.0, 48.4), (-81.4, 48.4),
                                                       (-81.4, 48.1)]]},
                  {"cell_id": "synthetic"}, 1.0),
        M.Feature({"type": "MultiPolygon", "coordinates": [[[(-80.8, 48.5), (-80.4, 48.5),
                                                             (-80.4, 48.8), (-80.8, 48.5)]]]},
                  {}, 0.2),
    ]
    layer = M.Layer("fabric:test", feats, {"label": "Synthetic", "value_min": 0.2,
                                           "value_max": 1.0})
    png = M.render_png([layer], BOX, 320, 240, title="test — figure",
                       marker=(-81.0, 48.5), attribution="test")
    check(png[:8] == b"\x89PNG\r\n\x1a\n", "render_png emits a PNG signature")
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png))
        check(img.size == (320, 240), "render_png honours the requested size")
        check(len(img.getcolors(maxcolors=1 << 20) or []) > 4,
              "render_png actually drew something")
    except ImportError:
        skip("render_png size check", "pillow not installed")


def test_layer_geojson_is_strict_json():
    feats = [M.Feature({"type": "Polygon", "coordinates": [[(0, 0), (1, 0), (1, 1), (0, 0)]]},
                       {"x": float("nan")}, float("nan"))]
    gj = M.Layer("t", feats, {"m": float("inf")}).geojson()
    check("NaN" not in json.dumps(gj) and "Infinity" not in json.dumps(gj),
          "Layer.geojson sanitises NaN out of properties and meta")


# ---------------------------------------------------------------------------
# Lake tests
# ---------------------------------------------------------------------------

def test_parquet_cache_is_keyed_on_columns():
    """Regression: two callers reading different column subsets of the same
    file must not share a cache entry."""
    path = C.PROCESSED_DIR / "tenure_events.parquet"
    if not path.exists():
        skip("test_parquet_cache_is_keyed_on_columns", "tenure_events.parquet missing")
        return
    narrow = M._read_parquet(path, columns=["juris", "event_type", "event_window_start"])
    wide = M._read_parquet(path, columns=["juris", "event_type", "event_window_start",
                                          "cell_r7", "area_ha", "survivorship_biased"])
    check("cell_r7" not in narrow.columns, "narrow read has only its own columns")
    check("cell_r7" in wide.columns,
          "wide read is not served the narrow frame from cache")
    # And in the order the live server hits them: catalog first, layer second.
    M._CACHE.clear()
    M.event_window()
    layer = M.events_layer(bbox=BOX, start="2024-01-01", end="2025-01-01")
    check(isinstance(layer, M.Layer),
          "events layer builds after the catalog has warmed the cache")


def test_fabric_resolution_stepdown():
    fine = M.fabric_layer("prospectivity", bbox=BOX)
    check(fine.meta["resolution"] == 7 and not fine.meta["aggregated"],
          "a local viewport returns native r7 cells")
    coarse = M.fabric_layer("prospectivity", bbox=M.DEFAULT_BBOX)
    check(coarse.meta["aggregated"] and coarse.meta["resolution"] < 7,
          "a province viewport steps the resolution down")
    check(len(coarse.features) <= M.MAX_FEATURES,
          "the step-down brings the payload under the feature budget")
    check(coarse.meta["n_source_cells"] > len(coarse.features),
          "the aggregate reports how many source cells it stands for")
    check(coarse.meta["agg"] == "max",
          "the aggregate names its aggregation function")
    check(all(f.props.get("resolution") == coarse.meta["resolution"]
              for f in coarse.features),
          "every aggregated feature carries the resolution it was drawn at")


def test_fabric_metrics_all_resolve():
    for metric in M.FABRIC_METRICS:
        try:
            layer = M.fabric_layer(metric, bbox=BOX)
            check(isinstance(layer, M.Layer), f"fabric metric {metric!r} resolves")
        except FileNotFoundError as e:
            skip(f"fabric metric {metric!r}", str(e))
    try:
        M.fabric_layer("nope", bbox=BOX)
        check(False, "unknown fabric metric raises")
    except ValueError:
        check(True, "unknown fabric metric raises")


def test_heat_quarter_selection():
    latest, meta = M.heat_values()
    peak, pmeta = M.heat_values(quarter="peak")
    check(meta["quarter"] in meta["quarters_available"],
          "heat defaults to a real quarter")
    check(pmeta["quarter"] == "peak", "heat peak mode says so in its meta")
    check(len(peak) >= len(latest),
          "peak covers at least as many cells as one quarter")
    check(peak["cell_id"].is_unique, "peak returns one row per cell")


def test_land_layer_rolls_up_when_over_budget():
    tight = M.land_layer(bbox=(-80.80, 48.32, -80.72, 48.36))
    check(tight.meta["mode"] == "r9_state", "a small viewport returns r9 cell states")
    check(all("state" in f.props for f in tight.features),
          "r9 land features carry their state")
    wide = M.land_layer(bbox=BOX)
    check(wide.meta["mode"] == "r7_open_fraction",
          "a wide viewport rolls land state up to an open fraction")
    check(all(0.0 <= (f.value or 0) <= 1.0 for f in wide.features),
          "open fraction is a fraction")
    check("Zoom in" in wide.meta["note"], "the rollup tells the reader what to do")


def test_blocks_geometry_is_transformed_from_3978():
    """Regression: blocks are stored in EPSG:3978, so both the query envelope
    and the returned geometry have to be transformed."""
    try:
        layer = M.blocks_layer(bbox=BOX)
    except FileNotFoundError as e:
        skip("test_blocks_geometry_is_transformed_from_3978", str(e))
        return
    check(len(layer.features) > 0,
          "blocks_layer finds blocks in the Abitibi bbox (empty means the "
          "lon/lat envelope was tested against projected geometry)")
    xs, ys = [], []
    for f in layer.features[:20]:
        for ring in M._rings(f.geometry):
            for x, y in ring:
                xs.append(x)
                ys.append(y)
    check(xs and all(-95 < x < -74 for x in xs), "block geometry x is a longitude")
    check(ys and all(41 < y < 57 for y in ys), "block geometry y is a latitude")


def test_claims_layer_needs_no_pytz():
    """The MLAS date columns are TIMESTAMP WITH TIME ZONE and DuckDB's Python
    conversion for that type imports pytz, which this repo does not depend on.
    They are cast to VARCHAR in SQL instead."""
    try:
        layer = M.claims_layer(bbox=(-80.80, 48.30, -80.70, 48.40))
    except Exception as e:
        check(False, f"claims_layer raised {type(e).__name__}: {e}")
        return
    check(len(layer.features) > 0, "claims_layer returns claims in the Abitibi bbox")
    p = layer.features[0].props
    check("HOLDER" in p and "TENURE_NUM" in p, "claim features carry holder and tenure id")
    check(all(v is None or isinstance(v, str) for v in p.values()),
          "claim attributes come back as strings, not timezone-aware timestamps")
    check("permission" in layer.meta["licence"].lower(),
          "the claims layer carries its licence restriction (audit H)")


def test_event_window_is_jurisdiction_scoped():
    """Regression: the scrubber and the layer must describe the same series."""
    w = M.event_window("ON")
    check(w.get("jurisdiction") == "ON", "event_window reports its jurisdiction")
    check(w["min"] >= "2018-01-01",
          f"Ontario's event series starts at map staking, not 1899 (got {w['min']})")
    import datetime as dt
    check(w["max"] <= dt.date.today().isoformat(),
          f"event_window does not report a future date (got {w['max']})")


def test_events_layer_windows_on_start():
    early = M.events_layer(bbox=BOX, start="2018-01-01", end="2019-01-01")
    late = M.events_layer(bbox=BOX, start="2024-01-01", end="2025-01-01")
    check(early.meta["events_in_window"] != late.meta["events_in_window"],
          "the event window actually filters")
    check(all(f.props.get("by_type") for f in late.features[:5]),
          "event features break their count down by type")
    check(sum(f.value for f in late.features) > 0, "event pulses carry a total")


def test_cell_evidence_shape():
    ev = M.cell_evidence(CELL_R9)
    for key in ("cell_id", "resolution", "centroid", "cell_r7", "land_state",
                "neighbours", "dossier"):
        check(key in ev, f"cell evidence carries {key}")
    check(ev["resolution"] == 9, "an r9 id is reported at r9")
    check(-95 < ev["centroid"][0] < -74 and 41 < ev["centroid"][1] < 57,
          "cell evidence centroid is (lon, lat) in Ontario")
    text = json.dumps(ev)
    check("NaN" not in text and "Infinity" not in text,
          "cell evidence is strict JSON")
    # Missing data must say why, never be silent.
    for key in ("land_state", "criticality", "features"):
        v = ev.get(key)
        if isinstance(v, dict) and v.get("available") is False:
            check(bool(v.get("reason")), f"{key} unavailability states a reason")
    try:
        M.cell_evidence("not-a-cell")
        check(False, "cell evidence rejects an invalid H3 id")
    except ValueError:
        check(True, "cell evidence rejects an invalid H3 id")


def test_criticality_is_scored_on_open_cells():
    layer = M.criticality_layer(bbox=BOX)
    check(len(layer.features) > 0, "criticality layer returns scored cells")
    check(layer.meta["resolution"] == 9, "criticality is served at its native r9")
    check("open cells" in layer.meta["note"].lower(),
          "the criticality layer restates the open-cells caveat (audit J)")
    scored = {f.props["cell_id"] for f in layer.features}
    try:
        df, _ = M._land_frame("abitibi", "ON")
    except FileNotFoundError:
        return
    states = df[df["cell_id"].isin(scored)]["state"].unique().tolist()
    check(states == [] or states == ["open"],
          f"every scored cell that has a land state is open (saw {states})")


def test_render_cell_inset():
    png = M.render_cell_inset(CELL_R9)
    check(png[:8] == b"\x89PNG\r\n\x1a\n", "cell inset is a PNG")
    check(len(png) > 3000, "cell inset is not a blank frame")


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

def test_http_surface():
    try:
        from fastapi.testclient import TestClient
    except ImportError as e:
        skip("test_http_surface", f"fastapi TestClient unavailable ({e})")
        return
    import serve
    client = TestClient(serve.app)

    r = client.get("/api/catalog")
    check(r.status_code == 200, "GET /api/catalog is 200")
    cat = r.json()
    check("fabric_metrics" in cat and "aois" in cat, "catalog lists metrics and AOIs")

    r = client.get("/api/fabric", params={"metric": "heat", "bbox": "-81.5,48,-80,49"})
    check(r.status_code == 200, "GET /api/fabric is 200")
    check(r.json()["type"] == "FeatureCollection", "fabric returns a FeatureCollection")

    r = client.get("/api/fabric", params={"metric": "nope"})
    check(r.status_code == 400 and "unknown metric" in r.json()["detail"],
          "an unknown metric is a 400 naming the valid ones")

    r = client.get("/api/fabric", params={"metric": "heat", "bbox": "1,2,3"})
    check(r.status_code == 400, "a malformed bbox is a 400")

    r = client.get(f"/api/cell/{CELL_R9}")
    check(r.status_code == 200, "GET /api/cell is 200")

    r = client.get("/api/cell/not-a-cell")
    check(r.status_code == 400, "an invalid cell id is a 400")

    r = client.get("/render", params={"cell": CELL_R9, "layers": "context,land",
                                      "width": 200, "height": 150})
    check(r.status_code == 200 and r.headers["content-type"] == "image/png",
          "GET /render returns a PNG")

    r = client.get("/render", params={"layers": "context,wat"})
    check(r.status_code == 400 and "unknown layers" in r.json()["detail"],
          "an unknown render layer is a 400")

    # The dossier file endpoint takes path components off the URL.
    for evil in ("..", "../../etc", "."):
        r = client.get(f"/api/dossier/{evil}/passwd")
        check(r.status_code in (400, 404),
              f"dossier file endpoint refuses {evil!r}")

    r = client.get("/")
    check(r.status_code == 200 and "Targeting viewer" in r.text,
          "GET / serves the C4.2 viewer")
    check("unpkg.com" not in r.text and "cartocdn" not in r.text,
          "the served page references no CDN")

    r = client.get("/browse")
    check(r.status_code == 200 and "cartocdn" not in r.text,
          "the legacy browser is still served and no longer calls a CDN")


# ---------------------------------------------------------------------------
# Dossier integration (C4.1 × C4.2)
# ---------------------------------------------------------------------------

def test_dossier_figures_and_sales_gate():
    from dossier.schema import Dossier, Section, Figure, Provenance
    import base64

    prov = Provenance(source_id="mapapi:context", snapshot_date="2026-08-18")
    fig = Figure(key="f", title="t", caption="c", generated_by="test",
                 provenance=prov, data_base64=base64.b64encode(b"x").decode())
    check(fig.redistribution == "permission_required",
          "a figure defaults to permission_required, never open (audit H)")

    d = Dossier(target_id="ON-test", dossier_version="v1", fabric_version="fab-x",
                feature_snapshot="2026-08-18", jurisdiction="ON",
                sections=[Section(key="land", title="Identity & land",
                                  figures=[fig], facts=[])])
    import tempfile
    from dossier import render as R
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "v1.json"
        p.write_text(d.model_dump_json())
        out = R.render(p, Path(td) / "v1.html", profile="internal")
        html = out.read_text()
        check("data:image/png;base64," in html,
              "the internal render inlines figures as data URIs")
        check("http://" not in html and "https://" not in html,
              "the rendered dossier makes no outbound request")
        try:
            R.render(p, Path(td) / "sales.html", profile="sales")
            check(False, "the sales render refuses an uncleared figure")
        except PermissionError as e:
            check("not cleared for redistribution" in str(e),
                  "the sales render refuses an uncleared figure")
        check(not (Path(td) / "sales.html").exists(),
              "the refused sales render writes no file")


def test_real_dossier_has_its_map_inset():
    """PLAN_C4 4.1 section 1 specifies a map inset. It was missing until C4.2."""
    root = C.PROCESSED_DIR / "dossiers"
    if not root.exists():
        skip("test_real_dossier_has_its_map_inset", "no dossiers on disk")
        return
    jsons = sorted(root.glob("*/*.json"))
    if not jsons:
        skip("test_real_dossier_has_its_map_inset", "no dossier JSON on disk")
        return
    d = json.loads(jsons[-1].read_text())
    land = next((s for s in d["sections"] if s["key"] == "land"), None)
    if land is None or not land.get("available"):
        skip("test_real_dossier_has_its_map_inset", "land section unavailable")
        return
    figs = land.get("figures") or []
    check(len(figs) == 1, f"{jsons[-1].parent.name} section 1 carries its map inset")
    if figs:
        check(len(figs[0]["data_base64"]) > 4000, "the inset is a real image")
        check(figs[0]["provenance"]["source_id"].startswith("mapapi:"),
              "the inset is stamped with the map service as its source")


# ---------------------------------------------------------------------------

PURE = [test_parse_bbox, test_cell_polygon_is_lon_lat, test_json_safe,
        test_maybe_json, test_projection, test_ramp, test_plain_text_is_latin1,
        test_cell_bbox_contains_its_cell, test_viewer_makes_no_external_request,
        test_render_layer_names_are_explicit, test_render_png_of_synthetic_layers,
        test_layer_geojson_is_strict_json, test_dossier_figures_and_sales_gate]

LAKE = [test_parquet_cache_is_keyed_on_columns, test_fabric_resolution_stepdown,
        test_fabric_metrics_all_resolve, test_heat_quarter_selection,
        test_land_layer_rolls_up_when_over_budget,
        test_blocks_geometry_is_transformed_from_3978,
        test_claims_layer_needs_no_pytz, test_event_window_is_jurisdiction_scoped,
        test_events_layer_windows_on_start, test_cell_evidence_shape,
        test_criticality_is_scored_on_open_cells, test_render_cell_inset,
        test_http_surface, test_real_dossier_has_its_map_inset]


def main():
    print("C4.2 map service tests\n")
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
            except Exception as e:
                _fails.append(f"{fn.__name__} raised {type(e).__name__}: {e}")
                print(f"    FAIL {fn.__name__} raised {type(e).__name__}: {e}")
    else:
        print(f"\n  SKIPPING {len(LAKE)} lake tests — {C.LAKE_ROOT} is not mounted "
              f"(or the r7 fabric is not built). These are the tests that would "
              f"catch a broken resolver, so a green run here proves less than "
              f"a green run with the drive attached.")

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
    print("all C4.2 tests pass")


if __name__ == "__main__":
    main()
