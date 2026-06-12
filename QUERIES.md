# Metadata search cookbook (national)

All queries run against `catalog.duckdb`:

```bash
duckdb ~/canada-geo-lake/catalog.duckdb
```

Layer/table naming:
- spatial: `geo_<JURIS>__<CODE>__<sourcefile>`  (e.g. `geo_ON__ON_ODHD__drillholes`)
- tabular: `<JURIS>__<CODE>__<name>`
- provenance: `resources`
- cross-jurisdiction FTS: `search_text`

Always start by listing what built:
```sql
SHOW TABLES;
DESCRIBE "geo_ON__ON_ODHD__<layer>";   -- exact column names come from source files
```

### Cross-Canada full-text search (any commodity/company/deposit term)
```sql
LOAD fts;
SELECT source_table, rid, text,
       fts_main_search_text.match_bm25(rid, 'gold porphyry') AS score
FROM search_text
WHERE score IS NOT NULL
ORDER BY score DESC
LIMIT 50;
```

### Which jurisdictions actually have data loaded, and how much
```sql
SELECT regexp_extract(table_name, '^geo_([A-Z_]+?)__', 1) AS juris,
       count(*) AS layers
FROM information_schema.tables
WHERE table_name LIKE 'geo_%'
GROUP BY 1 ORDER BY 2 DESC;
```

### Mineral occurrences by commodity, nationwide (union the per-province layers)
```sql
SELECT 'ON' j, "COMMODITY", count(*) n FROM "geo_ON__ON_OMI__<layer>" GROUP BY 2
UNION ALL
SELECT 'BC', "COMMODITY", count(*) FROM "geo_BC__BC_MINFILE__<layer>" GROUP BY 2
UNION ALL
SELECT 'SK', "COMMODITY", count(*) FROM "geo_SK__SK_SMDI__<layer>" GROUP BY 2
ORDER BY n DESC;
```

### Drill holes over a depth threshold (Ontario; adapt columns per province)
```sql
SELECT * FROM "geo_ON__ON_ODHD__<layer>"
WHERE TRY_CAST("HOLE_DEPTH" AS DOUBLE) > 500;
```

### Spatial: everything within ~5 km of a point, any layer (DuckDB spatial)
```sql
LOAD spatial;
SELECT * FROM "geo_BC__BC_MINFILE__<layer>"
WHERE st_dwithin(geom, st_point(-123.1, 49.3), 0.045);
```

### National tenure overview from the federal composite layer
```sql
SELECT "OWNER", count(*) FROM "geo_FED__NATIONAL_TENURE__<layer>"
GROUP BY 1 ORDER BY 2 DESC LIMIT 25;
```

### Provenance: when each source was last refreshed and from where
```sql
SELECT jurisdiction, code, max(snapshot_date) last_snapshot,
       count(*) files, max(url) sample_url
FROM resources
GROUP BY 1,2 ORDER BY 1,2;
```

### Point-in-time history (needs ≥2 snapshots of a tenure layer)
```sql
-- compare claim counts across two harvest dates from the manifest
SELECT snapshot_date, count(*) FROM resources
WHERE code = 'BC_MTO_CURRENT' GROUP BY 1 ORDER BY 1;
```

> Replace `<layer>` and column names with the real ones from `SHOW TABLES` /
> `DESCRIBE`. Field names come straight from the source shapefiles/CSVs and differ by
> province; the FTS index normalizes the common text columns so cross-Canada search
> works even when schemas don't match exactly.
