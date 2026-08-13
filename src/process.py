#!/usr/bin/env python3
"""
process.py — turn latest raw snapshots (all jurisdictions) into one GeoPackage + Parquet.

Layout is raw/<JURIS>/<CODE>/<YYYY-MM-DD>/. For each (JURIS,CODE) we take the most
recent snapshot, unzip archives, merge all vector tiles into a single GPKG layer per
dataset code, and write flat tables to processed/tables/.

Requires: geopandas, pyogrio, pandas, openpyxl, pyarrow.
"""
from __future__ import annotations
import json, sys, zipfile, tempfile, shutil
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


def expand(snapshot, work):
    """Copy a snapshot aside and unpack every archive in it, whatever it is named.

    Sniffs content, not extension. Québec ships ZIPs named .gpkg/.shp/.fgdb and
    Nova Scotia one named .gdb — 46 archives across four provinces that the old
    `rglob("*.zip")` never saw, which is why QC, BC bedrock and all of NS were
    absent from geo.gpkg. GDAL dispatches on extension, so these must be unpacked
    here or they stay unreadable downstream.
    """
    shutil.copytree(snapshot, work, dirs_exist_ok=True)
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


def _sublayers(path) -> list:
    """Layer names inside `path`, or `[None]` for single-layer formats.

    `gpd.read_file()` returns only the first layer of a container without a
    `layer=` argument, which silently discarded 27 of the 28 layers in Québec's
    sigeom.gpkg.
    """
    if path.suffix.lower() not in MULTILAYER:
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


def process_one(juris, code, snapshot):
    print(f"\n[{juris}/{code}] {snapshot.name}")
    with tempfile.TemporaryDirectory() as td:
        work = expand(snapshot, Path(td) / "w")
        PRIORITY = [".geojson", ".json", ".gpkg", ".shp", ".kml", ".kmz", ".gpx"]
        # (frame, source_stem) for everything readable at the winning priority.
        loaded: list = []
        for ext in PRIORITY:
            candidates = [p for p in work.rglob(f"*{ext}") if p.name != "_source.json"]
            if not candidates:
                continue
            for f in candidates:
                # Containers hold many layers — gpd.read_file() would silently
                # return only the first. QC's sigeom.gpkg carries 28.
                for sub in _sublayers(f):
                    try:
                        g = gpd.read_file(f, layer=sub) if sub else gpd.read_file(f)
                        if g.empty:
                            continue
                        if g.crs is None:
                            g.set_crs(epsg=4326, inplace=True, allow_override=True)
                        else:
                            g = g.to_crs(epsg=4326)
                        drop_cols = [c for c in g.columns if c.upper() in ("OBJECTID", "FID")]
                        if drop_cols:
                            g = g.drop(columns=drop_cols)
                        loaded.append((g, sub or f.stem))
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
    C.ensure_dirs()
    if C.GPKG_PATH.exists():
        C.GPKG_PATH.unlink()
    if not C.RAW_DIR.exists():
        sys.exit("No raw/ yet. Run harvest.py first.")
    for jdir in sorted(p for p in C.RAW_DIR.iterdir() if p.is_dir()):
        for cdir in sorted(p for p in jdir.iterdir() if p.is_dir()):
            snap = latest(cdir)
            if snap:
                process_one(jdir.name, cdir.name, snap)
    print(f"\nDone. {C.GPKG_PATH}")


if __name__ == "__main__":
    main()
