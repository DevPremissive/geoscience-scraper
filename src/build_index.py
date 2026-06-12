#!/usr/bin/env python3
"""
build_index.py — build the national DuckDB metadata search index.

Loads every Parquet table + every GeoPackage layer + the provenance manifest into
catalog.duckdb, then builds a full-text index over text columns spanning all
jurisdictions. Query with any DuckDB client. See QUERIES.md.
"""
from __future__ import annotations
import sys
import config as C
try:
    import duckdb
except ImportError:
    sys.exit("pip install duckdb")

TEXT_COLS = ("COMPANY", "COMPANY_NAME", "TITLE", "COMMODITY", "COMMODITIES", "TOWNSHIP",
             "NAME", "STATUS", "DEPOSIT", "DEPOSIT_NAME", "AUTHOR", "MINERAL",
             "OCCURRENCE", "REPORT", "WORK_TYPE")


def main():
    C.ensure_dirs()
    if C.CATALOG_DB.exists():
        C.CATALOG_DB.unlink()
    con = duckdb.connect(str(C.CATALOG_DB))
    for ext in ("spatial", "fts", "sqlite"):
        try:
            con.execute(f"INSTALL {ext}; LOAD {ext};")
        except Exception as e:                                  # noqa: BLE001
            print(f"  ! {ext}: {e}", file=sys.stderr)

    tables = []
    for pq in sorted(C.TABLES_DIR.glob("*.parquet")):
        con.execute(f'CREATE TABLE "{pq.stem}" AS SELECT * FROM read_parquet(?)', [str(pq)])
        tables.append(pq.stem)
        print(f"  table   {pq.stem}")

    if C.GPKG_PATH.exists():
        try:
            layers = con.execute("SELECT DISTINCT layer_name FROM st_read_meta(?)",
                                 [str(C.GPKG_PATH)]).fetchall()
        except Exception:
            layers = []
        for (layer,) in layers:
            t = f"geo_{layer}"
            con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM st_read(?, layer=?)',
                        [str(C.GPKG_PATH), layer])
            tables.append(t)
            print(f"  spatial {t}")

    if C.MANIFEST_DB.exists():
        con.execute("ATTACH ? AS m (TYPE sqlite)", [str(C.MANIFEST_DB)])
        con.execute("CREATE TABLE resources AS SELECT * FROM m.harvest")
        print("  table   resources (provenance)")

    # unified full-text view
    parts = []
    for t in tables:
        cols = {c[1].upper(): c[1] for c in con.execute(f'PRAGMA table_info("{t}")').fetchall()}
        present = [cols[k] for k in TEXT_COLS if k in cols]
        if not present:
            continue
        cat = " || ' ' || ".join(f"COALESCE(CAST(\"{c}\" AS VARCHAR),'')" for c in present)
        parts.append(f"SELECT '{t}' AS source_table, CAST(rowid AS BIGINT) AS rid, "
                     f"{cat} AS text FROM \"{t}\"")
    if parts:
        con.execute("CREATE TABLE search_text AS " + "\nUNION ALL\n".join(parts))
        try:
            con.execute("PRAGMA create_fts_index('search_text','rid','text',overwrite=1)")
            print("  fts     search_text built")
        except Exception as e:                                  # noqa: BLE001
            print(f"  ! fts: {e}", file=sys.stderr)

    con.close()
    print(f"\nDone. {C.CATALOG_DB}")


if __name__ == "__main__":
    main()
