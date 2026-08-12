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


def expand(snapshot, work):
    shutil.copytree(snapshot, work, dirs_exist_ok=True)
    for z in list(work.rglob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                zf.extractall(z.parent / z.stem)
                z.unlink()
        except zipfile.BadZipFile:
            print(f"  ! bad zip {z.name}", file=sys.stderr)
    for f in list(work.rglob("*")):
        if f.is_file() and f.suffix == "" and f.stat().st_size > 0:
            try:
                with zipfile.ZipFile(f) as zf:
                    if any(n.endswith(".kml") for n in zf.namelist()):
                        f.rename(f.with_suffix(".kmz"))
            except (zipfile.BadZipFile, IsADirectoryError):
                pass
    return work


def process_one(juris, code, snapshot):
    print(f"\n[{juris}/{code}] {snapshot.name}")
    with tempfile.TemporaryDirectory() as td:
        work = expand(snapshot, Path(td) / "w")
        PRIORITY = [".geojson", ".json", ".gpkg", ".shp", ".kml", ".kmz", ".gpx"]
        best_frames: list = []
        for ext in PRIORITY:
            candidates = [p for p in work.rglob(f"*{ext}") if p.name != "_source.json"]
            if not candidates:
                continue
            frames: list = []
            for f in candidates:
                try:
                    g = gpd.read_file(f)
                    if g.empty:
                        continue
                    if g.crs is None:
                        g.set_crs(epsg=4326, inplace=True, allow_override=True)
                    else:
                        g = g.to_crs(epsg=4326)
                    drop_cols = [c for c in g.columns if c.upper() in ("OBJECTID", "FID")]
                    if drop_cols:
                        g = g.drop(columns=drop_cols)
                    frames.append(g)
                except Exception as e:                          # noqa: BLE001
                    print(f"   ! vec {f.name}: {e}", file=sys.stderr)
            if frames:
                best_frames = frames
                break
        layer_name = f"{juris}__{code}".replace(" ", "_").replace("-", "_")[:62]
        if best_frames:
            merged = gpd.pd.concat(best_frames, ignore_index=True) if len(best_frames) > 1 else best_frames[0]
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
