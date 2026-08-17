#!/usr/bin/env python3
"""
process.py — turn latest raw snapshots (all jurisdictions) into one GeoPackage + Parquet.

Layout is raw/<JURIS>/<CODE>/<YYYY-MM-DD>/. For each (JURIS,CODE) we take the most
recent snapshot, unzip archives, merge all vector tiles into a single GPKG layer per
dataset code, and write flat tables to processed/tables/.

Requires: geopandas, pyogrio, pandas, openpyxl, pyarrow.
"""
from __future__ import annotations
import argparse, json, sys, zipfile, tempfile, shutil
from pathlib import Path

import config as C
try:
    import geopandas as gpd
    import pandas as pd
except ImportError:
    sys.exit("pip install -r requirements.txt")

VEC = {".shp", ".geojson", ".json", ".kml", ".kmz", ".gpkg", ".gpx"}
TAB = {".csv", ".tsv", ".xlsx", ".xls"}
JSONL = {".jsonl"}


def latest(code_dir):
    snaps = [p for p in code_dir.iterdir() if p.is_dir()]
    return max(snaps, default=None, key=lambda p: p.name) if snaps else None


#: Written by harvest.py into each snapshot directory: every resource the run
#: discovered for that source, fetched or skipped. Its absence means the
#: snapshot predates the manifest and cannot be pruned against.
RUN_MANIFEST = "_manifest.json"


def resolve_snapshot(code_dir, as_of: str | None = None):
    """Compose the source's current state by overlaying its dated snapshots.

    A snapshot directory is a **delta, not a complete copy**: `harvest.py` only
    writes files whose content changed, so an unchanged tile stays in whichever
    older snapshot last carried it. Taking the newest directory alone therefore
    reads a fraction of the source — Ontario's 2026-06-13 held 164 of 355 claim
    tiles, which is how every Ontario tenure count in this lake came to be
    derived from 46% of the province (audit I12). Under C3.1's daily cadence a
    day with twenty changed tiles would rebuild the province from twenty tiles.

    Overlay oldest-to-newest so a newer version of a file wins, then prune
    against the newest run manifest so resources the publisher has withdrawn do
    not persist forever — an overlay alone cannot express a deletion. Snapshots
    written before run manifests existed simply are not pruned.

    `as_of` reconstructs the state as it stood on a given date, which is what a
    point-in-time diff needs: comparing two raw directories compares a complete
    snapshot against a delta and manufactures thousands of phantom events.

    Returns `(effective_date, {relative_name: path})`, empty if nothing exists.
    """
    snaps = sorted((p for p in code_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
    if as_of:
        snaps = [s for s in snaps if s.name <= as_of]
    if not snaps:
        return None, {}

    files: dict[str, Path] = {}
    for snap in snaps:                       # oldest first — newest wins
        for f in snap.rglob("*"):
            if f.is_file() and f.name != RUN_MANIFEST:
                files[str(f.relative_to(snap))] = f

    # Prune to what the most recent run that recorded a manifest still saw.
    for snap in reversed(snaps):
        mf = snap / RUN_MANIFEST
        if not mf.exists():
            continue
        try:
            current = set(json.loads(mf.read_text(encoding="utf-8"))["files"])
        except Exception:                                       # noqa: BLE001
            break
        dropped = [k for k in files if k not in current and k != "_source.json"]
        for k in dropped:
            del files[k]
        if dropped:
            print(f"   … {len(dropped)} withdrawn resource(s) pruned from the overlay")
        break

    return snaps[-1].name, files


# ZIP-based formats the loaders below read directly. Extracting these breaks them.
ZIP_NATIVE = {".kmz", ".xlsx", ".xls", ".docx", ".qgz"}


#: Archives can nest — NS wraps a self-extracting .exe inside a ZIP named .gdb.
MAX_EXPAND_PASSES = 4


def is_zip(path) -> bool:
    """Detect a ZIP by content rather than by name.

    Checks the local-header magic first, then falls back to a central-directory
    scan, which is what recognises self-extracting archives: Nova Scotia's mineral
    rights download is a ZIP named `.gdb` containing a Windows SFX `.exe` whose
    first bytes are `MZ`, with the real shapefiles inside that.
    """
    try:
        with open(path, "rb") as fh:
            if fh.read(4) in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
                return True
    except OSError:
        return False
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def expand(files, work):
    """Materialise the resolved file set aside and unpack every archive in it.

    Takes the `{relative_name: path}` map from `resolve_snapshot()` rather than a
    single directory, because the current state of a source is composed from
    several dated snapshots.

    Sniffs content, not extension. Québec ships ZIPs named .gpkg/.shp/.fgdb and
    Nova Scotia one named .gdb — 46 archives across four provinces that the old
    `rglob("*.zip")` never saw, which is why QC, BC bedrock and all of NS were
    absent from geo.gpkg. GDAL dispatches on extension, so these must be unpacked
    here or they stay unreadable downstream.
    """
    work.mkdir(parents=True, exist_ok=True)
    for rel, src in files.items():
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for _ in range(MAX_EXPAND_PASSES):
        if not _expand_pass(work):
            break
    return work


def _expand_pass(work) -> bool:
    """Unpack every archive found in one sweep. Returns True if anything changed."""
    changed = False
    for f in sorted(work.rglob("*")):
        if not f.is_file() or f.name == "_source.json":
            continue
        try:
            if f.stat().st_size == 0 or f.suffix.lower() in ZIP_NATIVE or not is_zip(f):
                continue
        except OSError:
            continue
        try:
            with zipfile.ZipFile(f) as zf:
                names = zf.namelist()
                # A KMZ under some other name: keep it whole, just label it.
                # Append rather than replace — these names are often coordinates
                # ("-95_53_-94.5_53.5") where with_suffix() would eat a digit.
                if names and all(n.lower().endswith((".kml", "/")) for n in names):
                    f.rename(f.with_name(f.name + ".kmz"))
                    changed = True
                    continue
                zf.extractall(f.parent / (f.stem or f.name))
            f.unlink()
            changed = True
        except zipfile.BadZipFile:
            print(f"  ! bad zip {f.name}", file=sys.stderr)
        except OSError as e:
            print(f"  ! cannot expand {f.name}: {e}", file=sys.stderr)
    return changed


#: Formats that can hold more than one layer in a single file.
MULTILAYER = {".gpkg", ".gdb", ".fgdb", ".kml", ".kmz"}

#: Sentinel standing for an ESRI ArcInfo binary coverage in PRIORITY. A coverage
#: is a *directory* of .adf files with no distinguishing name, so it cannot be
#: globbed by extension like every other format here.
COVERAGE = "<arcinfo-coverage>"


def is_coverage(path) -> bool:
    """True for an ArcInfo coverage directory (GDAL's AVCBin driver reads it).

    Ontario's MRD128 surficial geology ships this way: 115,526 polygons with
    MATERIAL_DESCRIP and GEOLOGIC_DEPOSIT text, in `Data/coverages/sgu_poly/`.
    Nothing in the extension-driven search below can see it, so a 503 MB
    download produced zero layers and looked like an empty dataset.
    """
    return path.is_dir() and any(path.glob("*.adf"))


def _sublayers(path) -> list:
    """Layer names inside `path`, or `[None]` for single-layer formats.

    `gpd.read_file()` returns only the first layer of a container without a
    `layer=` argument, which silently discarded 27 of the 28 layers in Québec's
    sigeom.gpkg — and, in a coverage, would return the ARC topology primitive
    instead of the PAL polygons anyone actually wants.
    """
    if path.suffix.lower() not in MULTILAYER and not is_coverage(path):
        return [None]
    try:
        import pyogrio
        names = [l[0] for l in pyogrio.list_layers(str(path))]
    except Exception:                                           # noqa: BLE001
        return [None]
    return names if len(names) > 1 else [None]


def lname_clean(name: str) -> str:
    """Normalise a layer name for GPKG: no spaces or hyphens, length-capped."""
    return name.replace(" ", "_").replace("-", "_")[:62]


def _layer_has_geometry(path, layer) -> bool:
    """True when a container layer carries geometry.

    Containers mix spatial and flat tables freely. Québec's geochemistry GPKG
    holds 561,232 sediment sample points beside `R1E03_RESULTAT_ANALYSE_ES`
    and `_ER` — 39.5 M rows of assay results with no geometry at all. Loading
    those as GeoDataFrames to write into the *spatial* store cost ~39 M rows of
    memory and was OOM-killed; Master §4 puts flat tables in Parquet anyway.
    """
    try:
        import pyogrio
        info = pyogrio.read_info(str(path), layer=layer) if layer \
            else pyogrio.read_info(str(path))
        gtype = info.get("geometry_type")
        if gtype not in (None, "Unknown", "None"):
            return True
        # "Unknown" is not the same as "absent". KML/KMZ layers may hold mixed
        # geometry types and report Unknown while being perfectly spatial —
        # trusting the string alone sent all 1,514 OGSEarth tenure tiles to
        # Parquet as one table per tile. Settle it against the data instead.
        head = gpd.read_file(path, layer=layer, rows=1) if layer \
            else gpd.read_file(path, rows=1)
        return "geometry" in head.columns and bool(head.geometry.notna().any())
    except Exception:                                           # noqa: BLE001
        return True          # unreadable metadata: let the normal path try


def _table_to_parquet(path, layer, out: Path) -> int:
    """Stream a geometry-less container layer to Parquet in record batches.

    Québec's `R1E03_RESULTAT_ANALYSE_ES` is 21,237,346 rows and `_ER` another
    18,243,268. Materialising either as one Arrow table alongside the frames
    already held for this source was enough to be OOM-killed, so batches are
    written as they arrive and peak memory stays bounded by one batch rather
    than by the layer.
    """
    import pyogrio, pyarrow.parquet as pq
    kw = {"layer": layer} if layer else {}
    try:
        with pyogrio.raw.open_arrow(str(path), use_pyarrow=True, **kw) as (_meta, reader):
            writer = None
            n = 0
            try:
                for batch in reader:
                    if writer is None:
                        writer = pq.ParquetWriter(out, batch.schema)
                    writer.write_batch(batch)
                    n += batch.num_rows
            finally:
                if writer is not None:
                    writer.close()
        return n
    except Exception:                                           # noqa: BLE001
        df = pyogrio.read_dataframe(str(path), read_geometry=False, **kw)
        df.to_parquet(out, index=False)
        return len(df)


def same_layer(a, b) -> bool:
    """True when two frames hold identical attributes and identical geometry.

    Nova Scotia ships its mineral rights twice in every package — as
    `t493nsal_mineral_rights_dp` and `t493nsal_2026_06JUN_12_0205` inside the
    geodatabase, and as two separate `.shp` files in the shapefile bundle —
    2,227 rows each. Merged on their shared schema they report 4,454.

    Identity here means every attribute and every geometry matches, so
    discarding one copy cannot lose information. That holds across files as
    well as within a container: two OGSEarth tiles cover disjoint ground and
    cannot produce identical geometry sets, and in the one degenerate case
    where they could — a single claim straddling two otherwise-empty tiles —
    keeping one copy is the more correct answer, not the lossy one.
    """
    cols = [c for c in a.columns if c != "geometry"]
    if sorted(cols) != sorted(c for c in b.columns if c != "geometry"):
        return False
    if not a[cols].reset_index(drop=True).equals(b[cols].reset_index(drop=True)):
        return False
    return bool(a.geometry.reset_index(drop=True)
                 .geom_equals(b.geometry.reset_index(drop=True)).all())


def process_one(juris, code, effective_date, files):
    print(f"\n[{juris}/{code}] {effective_date}  ({len(files)} files)")
    with tempfile.TemporaryDirectory() as td:
        work = expand(files, Path(td) / "w")
        # `.gdb`/`.fgdb` sit above `.shp`: a File Geodatabase is a *directory*,
        # it carries richer types than a shapefile, and where a vendor ships both
        # (Nova Scotia) they hold the same data. Without them here, an extracted
        # geodatabase is invisible — Québec's SIGÉOM survives only because the
        # same download also contains a real .gpkg.
        PRIORITY = [".geojson", ".json", ".gpkg", ".gdb", ".fgdb", ".shp",
                    COVERAGE, ".kml", ".kmz", ".gpx"]
        # (frame, source_stem) for everything readable at the winning priority.
        loaded: list = []
        for ext in PRIORITY:
            if ext is COVERAGE:
                candidates = sorted({p.parent for p in work.rglob("*.adf")})
            else:
                candidates = [p for p in work.rglob(f"*{ext}")
                              if p.name != "_source.json"]
            if not candidates:
                continue
            # Exact-duplicate frames seen so far, bucketed by a cheap key so the
            # expensive comparison only runs against genuine candidates. Without
            # the bucket this is quadratic over 1,541 OGSEarth tiles.
            seen: dict = {}
            for f in candidates:
                # Containers hold many layers — gpd.read_file() would silently
                # return only the first. QC's sigeom.gpkg carries 28.
                for sub in _sublayers(f):
                    try:
                        # Flat tables inside a spatial container go to the
                        # tabular store, not into geo.gpkg (Master §4).
                        if not _layer_has_geometry(f, sub):
                            out = (C.TABLES_DIR /
                                   f"{lname_clean(f'{juris}__{code}__{sub or f.stem}')}.parquet")
                            n = _table_to_parquet(f, sub, out)
                            print(f"   table {out.stem:<48}{n:>9}")
                            continue
                        g = gpd.read_file(f, layer=sub) if sub else gpd.read_file(f)
                        if g.empty:
                            continue
                        key = (len(g), tuple(sorted(c for c in g.columns
                                                    if c != "geometry")))
                        if any(same_layer(g, prev) for prev in seen.get(key, ())):
                            print(f"   = dup {f.name}[{sub or '-'}] — identical to "
                                  f"an already-loaded layer, skipped")
                            continue
                        seen.setdefault(key, []).append(g)
                        # Coverage sublayers are topology primitives — every
                        # coverage has an ARC and a PAL — so the sublayer name
                        # alone collides across coverages in one download.
                        stem = (f"{f.name}_{sub}" if sub and is_coverage(f)
                                else (sub or f.stem))
                        if g.crs is None:
                            g.set_crs(epsg=4326, inplace=True, allow_override=True)
                        else:
                            g = g.to_crs(epsg=4326)
                        drop_cols = [c for c in g.columns if c.upper() in ("OBJECTID", "FID")]
                        if drop_cols:
                            g = g.drop(columns=drop_cols)
                        loaded.append((g, stem))
                    except Exception as e:                      # noqa: BLE001
                        print(f"   ! vec {f.name}[{sub or '-'}]: {e}", file=sys.stderr)
            if loaded:
                break

        # Group by schema. Tiled sources (1,541 OGSEarth KMZ tiles) share one
        # schema and must merge into a single layer; multi-dataset bundles (the
        # MLAS ZIP holds cell claims, cancelled claims, alienations, tenure,
        # plans & permits) have different schemas and must not be concatenated
        # into a sparse union of everything.
        groups: dict = {}
        for g, stem in loaded:
            key = tuple(sorted(c for c in g.columns if c != "geometry"))
            groups.setdefault(key, []).append((g, stem))

        base = f"{juris}__{code}".replace(" ", "_").replace("-", "_")
        for frames in groups.values():
            merged = (gpd.pd.concat([f for f, _ in frames], ignore_index=True)
                      if len(frames) > 1 else frames[0][0])
            name = base if len(groups) == 1 else f"{base}__{frames[0][1]}"
            layer_name = lname_clean(name)
            merged.to_file(C.GPKG_PATH, layer=layer_name, driver="GPKG")
            print(f"   layer {layer_name:<48}{len(merged):>9}")

        for tab in sorted(p for e in TAB for p in work.rglob(f"*{e}")):
            try:
                df = (pd.read_csv(tab, low_memory=False, encoding="latin-1",
                                  sep="\t" if tab.suffix == ".tsv" else ",")
                      if tab.suffix in (".csv", ".tsv") else pd.read_excel(tab))
                if df.empty:
                    continue
                df.to_parquet(C.TABLES_DIR / f"{lname(juris,code,tab)}.parquet", index=False)
                print(f"   table {lname(juris,code,tab):<48}{len(df):>9}")
            except Exception as e:                              # noqa: BLE001
                print(f"   ! tab {tab.name}: {e}", file=sys.stderr)

        for jl in sorted(p for e in JSONL for p in work.rglob(f"*{e}")):
            try:
                rows = []
                with open(jl, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            rows.append(json.loads(line))
                if not rows:
                    continue
                df = pd.json_normalize(rows)
                df.to_parquet(C.TABLES_DIR / f"{lname(juris,code,jl)}.parquet", index=False)
                print(f"   table {lname(juris,code,jl):<48}{len(df):>9}")
            except Exception as e:                              # noqa: BLE001
                print(f"   ! jsonl {jl.name}: {e}", file=sys.stderr)


def lname(juris: str, code: str, path: Path) -> str:
    return f"{juris}__{code}__{path.stem}".replace(" ", "_").replace("-", "_")[:62]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", nargs="*", default=None)
    ap.add_argument("--only", nargs="*", default=None,
                    help="dataset codes, e.g. --only ON_MLAS_TENURE")
    args = ap.parse_args()
    only_j = {j.upper() for j in args.jurisdiction} if args.jurisdiction else None
    only_c = {c.upper() for c in args.only} if args.only else None

    C.ensure_dirs()
    # A full run rebuilds the GeoPackage from scratch. A filtered run must not:
    # writing a layer that already exists replaces just that layer and leaves
    # its siblings intact, so a jurisdiction can be reprocessed on its own
    # without re-expanding the ~8 GB of Québec archives every time.
    if C.GPKG_PATH.exists() and not (only_j or only_c):
        C.GPKG_PATH.unlink()
    if not C.RAW_DIR.exists():
        sys.exit("No raw/ yet. Run harvest.py first.")
    for jdir in sorted(p for p in C.RAW_DIR.iterdir() if p.is_dir()):
        if only_j and jdir.name.upper() not in only_j:
            continue
        for cdir in sorted(p for p in jdir.iterdir() if p.is_dir()):
            if only_c and cdir.name.upper() not in only_c:
                continue
            eff, files = resolve_snapshot(cdir)
            if files:
                process_one(jdir.name, cdir.name, eff, files)
    print(f"\nDone. {C.GPKG_PATH}")


if __name__ == "__main__":
    main()
