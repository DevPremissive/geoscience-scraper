#!/usr/bin/env python3
"""
process.py — turn latest raw snapshots (all jurisdictions) into one GeoPackage + Parquet.

Layout is raw/<JURIS>/<CODE>/<YYYY-MM-DD>/. For each (JURIS,CODE) we take the most
recent snapshot, unzip archives, load vectors into processed/geo.gpkg as layers named
<JURIS>__<CODE>__<file>, and flat tables to processed/tables/.

Requires: geopandas, pyogrio, pandas, openpyxl, pyarrow.
"""
from __future__ import annotations
import sys, zipfile, tempfile, shutil
from pathlib import Path

import config as C
try:
    import geopandas as gpd
    import pandas as pd
except ImportError:
    sys.exit("pip install -r requirements.txt")

VEC = {".shp", ".geojson", ".json", ".kml", ".kmz", ".gpkg", ".gpx"}
TAB = {".csv", ".tsv", ".xlsx", ".xls"}


def latest(code_dir):
    snaps = [p for p in code_dir.iterdir() if p.is_dir()]
    return max(snaps, default=None, key=lambda p: p.name) if snaps else None


def expand(snapshot, work):
    shutil.copytree(snapshot, work, dirs_exist_ok=True)
    for z in list(work.rglob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                zf.extractall(z.parent / z.stem)
        except zipfile.BadZipFile:
            print(f"  ! bad zip {z.name}", file=sys.stderr)
    return work


def lname(juris, code, path):
    return f"{juris}__{code}__{path.stem}".replace(" ", "_").replace("-", "_")[:62]


def process_one(juris, code, snapshot):
    print(f"\n[{juris}/{code}] {snapshot.name}")
    with tempfile.TemporaryDirectory() as td:
        work = expand(snapshot, Path(td) / "w")
        for vec in sorted(p for e in VEC for p in work.rglob(f"*{e}")):
            try:
                g = gpd.read_file(vec)
                if g.empty:
                    continue
                if g.crs is None:
                    g.set_crs(epsg=4326, inplace=True, allow_override=True)
                g = g.to_crs(epsg=4326)
                g.to_file(C.GPKG_PATH, layer=lname(juris, code, vec), driver="GPKG")
                print(f"   layer {lname(juris,code,vec):<48}{len(g):>9}")
            except Exception as e:                              # noqa: BLE001
                print(f"   ! vec {vec.name}: {e}", file=sys.stderr)
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
