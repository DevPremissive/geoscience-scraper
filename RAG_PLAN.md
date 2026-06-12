# RAG Ingestion Plan — Canada Geoscience & Mining Data Lake

## Storage Location

Everything lives under `~/canada-geo-lake/` (configurable via `CANADA_GEO_LAKE` env var).

```
~/canada-geo-lake/
├── raw/                    # immutable dated snapshots (from harvest.py)
├── pdfs/                   # assessment-report PDFs (from harvest_pdfs.py)
├── processed/
│   ├── geo.gpkg            # ALL vector layers, one GeoPackage
│   └── tables/*.parquet    # flat attribute tables
├── catalog.duckdb          # DuckDB metadata search index
├── manifest.sqlite         # harvest ledger
├── chroma_db/              # 🌟 NEW: vector database for semantic search
└── logs/
```

The vector store (`chroma_db/`) is a persistent ChromaDB collection alongside the existing DuckDB catalog.

---

## Data Flow

```
                    ┌─────────────────────┐
                    │  CKAN / ArcGIS       │
                    │  (structured tabular │
                    │   + vector data)      │
                    └────────┬────────────┘
                             │ harvest.py
                             ▼
                    ┌─────────────────────┐
                    │  raw/ snapshots      │
                    │  (SHP, CSV, GeoJSON, │
                    │   XLSX, GPKG ...)    │
                    └────────┬────────────┘
                             │ process.py
                             ▼
                    ┌─────────────────────┐
     ┌──────────────┤  processed/          ├──────────────┐
     │              │  geo.gpkg + Parquet  │              │
     │              └────────┬────────────┘              │
     │                       │                            │
     ▼                       ▼                            ▼
┌──────────┐       ┌──────────────────┐          ┌──────────────┐
│  Phase 2 │       │   Phase 2 (alt)  │          │   Phase 1    │
│ tabular   │       │   vector layer   │          │  PDF corpus  │
│ per-row   │       │   per-row        │          │  (assessment │
│ textify   │       │   textify        │          │   reports)   │
└─────┬────┘       └─────┬───────────┘          └──────┬───────┘
      │                  │                             │
      └──────────────────┴─────────────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────┐
                    │  textify.py          │
                    │  row → NL text       │
                    └────────┬────────────┘
                             │
                             ▼
                    ┌─────────────────────┐
                    │  chunk.py            │
                    │  semantic / para     │
                    └────────┬────────────┘
                             │
                             ▼
                    ┌─────────────────────┐
                    │  embed.py            │
                    │  Ollama nomic-embed  │
                    │  (768d)              │
                    └────────┬────────────┘
                             │
                             ▼
                    ┌─────────────────────┐
                    │  upsert to ChromaDB  │
                    │  ~/canada-geo-lake/  │
                    │  chroma_db/          │
                    └─────────────────────┘
```

---

## Phase 1: PDF Assessment Reports (scrape stubs → RAG)

### What needs building

| Step | File | What it does |
|------|------|-------------|
| 1 | Finish `connectors/scrape.py` stubs | Fill `enumerate_ids()` / `pdf_url()` for each legacy system (~15 min/site with devtools) |
| 2 | `pdf_extract.py` | PyMuPDF text extraction from harvested PDFs → plain text |
| 3 | `pipeline/rag.py` | Chunk (paragraph strategy), embed (Ollama nomic-embed-text), upsert to ChromaDB |
| 4 | `coverage.py` | Coverage profile: which jurisdictions/document types are represented |

### Document types (classification)

Assessment reports carry structured metadata from the scrape source:
- **Jurisdiction** (ON, BC, SK, NL...)
- **Report type** (geochem, assay, drill log, geophysics)
- **Commodity** (gold, copper, zinc...)
- **NTS map sheet**
- **Company**

We classify each PDF at ingestion time using a rule-based classifier on its metadata + first-page text, mapping to a canonical `doc_type`:
- `assessment_report_geochem`
- `assessment_report_drill`
- `assessment_report_geophys`
- `assessment_report_general`

### Chunking strategy (PDFs)

Same as mining-scraper's paragraph strategy:
- Split on double newlines
- Merge adjacent paragraphs up to `max_chunk_chars=1400`
- Drop chunks < 200 chars
- Hard-truncate at 1400 chars for embedding model context window

### Chunk metadata

```python
{
    "jurisdiction": "ON",
    "source_code": "ON_AFRI_PDF",
    "report_id": "AFRI_12345",
    "doc_type": "assessment_report_drill",
    "commodity": "gold",
    "company": "Company Name",
    "nts_sheet": "42A",
    "chunk_index": 7,
    "total_chunks": 34,
}
```

---

## Phase 2: Tabular / Vector Data (per-row embeddings)

### What it does

Every table in `processed/tables/*.parquet` and every layer in `processed/geo.gpkg` gets converted per-row into a natural-language text record and embedded.

### Date activation

The CKAN + ArcGIS sources (federal, ON, BC, SK) are ready to harvest immediately. Run `harvest.py` + `process.py` to populate `processed/`, then:

| Step | File | What it does |
|------|------|-------------|
| 1 | `textify.py` | Reads each Parquet/GPKG layer, builds a text template per row using column names + values, stores as a new column or sidecar |
| 2 | `pipeline/rag.py` | (shared with Phase 1) Embeds textified rows, upserts to ChromaDB |
| 3 | `coverage.py` | Coverage profile by jurisdiction and data type |

### Textification examples

**Drill hole (ON_ODHD):**
```
"Drill hole ID: 12345. Township: McGarry. Company: XYZ Mining.
 Hole depth: 350m. Azimuth: 270°. Dip: -45°.
 Assays: Au 2.3 g/t over 3.0m, Ag 12.1 g/t over 3.0m.
 Status: drilled 2022. Commodities: gold, silver."
```

**Mineral occurrence (BC MINFILE):**
```
"MINFILE occurrence: 093A 001. Name: Red Chris. 
 Commodities: copper, gold, silver.
 Deposit type: porphyry Cu-Au.
 Status: past producer (2005-2019).
 NTS: 093A. Latitude: 58.12, Longitude: -130.12."
```

**Mineral tenure claim (BC MTO):**
```
"Mineral claim: 1234567. Owner: XYZ Corp.
 Status: good standing. Issue date: 2023-01-15.
 Area: 345.2 ha. Commodity: gold.
 NTS mapsheets: 093A, 093B."
```

### Chunk metadata (tabular rows)

```python
{
    "jurisdiction": "BC",
    "source_code": "BC_MINFILE",
    "row_id": "093A001",
    "feature_type": "mineral_occurrence",
    "commodity": "copper",
    "company": "Company Name",
    "nts_sheet": "093A",
    "geometry_type": "Point",
    "latitude": 58.12,
    "longitude": -130.12,
}
```

### Scalability

- ON_ODHD alone: 126k rows → 126k chunks (fine for ChromaDB)
- BC MINFILE: ~14k occurrences
- National mineral deposits: ~30k
- Tenure layers: can be large (BC MTO ~100k claims) — may batch-upsert

**Estimated total:** 200k–500k chunks from all ready-structured sources. ChromaDB handles millions.

---

## Shared RAG Infrastructure

### Embedding model

Same stack as mining-scraper: **nomic-embed-text** via Ollama (`localhost:11434`).
- 768-dimensional vectors
- 8192 token context window (generous for longer textified rows)
- Fallback: sentence-transformers `all-MiniLM-L6-v2` (384d) when `EMBEDDING_BACKEND=local`

### Vector database

**ChromaDB** at `~/canada-geo-lake/chroma_db/`.
- Persistent on-disk (no server process needed)
- Collection namespacing: `geo_canada` (or per-jurisdiction if needed)
- Metadata filtering: filter by jurisdiction, commodity, doc_type, NTS sheet

### Index structure in ChromaDB

Single collection `geo_canada` with per-chunk metadata for faceted filtering:

```
Collection: "geo_canada"
  ├── Phase 1 chunks (PDF assessment reports)
  │   └── metadata: jurisdiction, source_code, report_id, doc_type, commodity, company, nts_sheet
  └── Phase 2 chunks (tabular/vector per-row)
      └── metadata: jurisdiction, source_code, row_id, feature_type, commodity, company, nts_sheet, geometry
```

### Semantic search queries (examples)

After both phases are live:

```python
# Find all gold occurrences within 5km of a past-producing mine
vector_store.query("gold occurrence near past-producing mine", n_results=50)

# Drill holes with high-grade gold intersections
vector_store.query("drill hole intercepts over 5 g/t gold", n_results=20)

# Assessment reports for a specific NTS sheet
vector_store.query("assessment reports NTS 093A copper", n_results=30)

# Cross-jurisdiction: all references to a company
vector_store.query("company XYZ mining exploration results", n_results=50)
```

---

## Implementation Order

### Sprint 1 — PDF pipeline (bridge from mining-scraper)
1. Copy `rag_pipeline.py` + `vector_store.py` + `models.py` from mining-scraper into this project
2. Adapt for geoscience doc types (assessment reports instead of SEC filings)
3. Write `pdf_extract.py` (PyMuPDF → plain text)
4. Verify with a small manual PDF download

### Sprint 2 — Finish 2–3 PDF scrape stubs
1. Start with ON AFRI (best-documented stub hints)
2. Then BC ARIS (richest corpus)
3. Then SK SMAD or NL GeoFiles

### Sprint 3 — Tabular textify
1. Write `textify.py` — generic row-to-text converter
2. Configure per-layer templates using column introspection
3. Test-embed ON_ODHD drill holes (126k rows)

### Sprint 4 — Unified query layer
1. Faceted filter UI over ChromaDB metadata
2. Hybrid query: "gold drill holes, filtered by jurisdiction=ON, by commodity=gold"
3. Cross-reference with DuckDB catalog for spatial queries
4. Coverage dashboard showing jurisdiction-by-jurisdiction RAG status

---

## Design Decisions (confirmed)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Textify templates | **Hand-craft per layer** | Higher quality text → better embeddings; effort is one-time per layer |
| PDF metadata enrichment | **Parse first page** | Extract NTS/company/commodity from PDF text before embedding; DuckDB cross-ref as secondary |
| Scanned PDFs (OCR) | **Deprioritize** | Older AFRI reports skipped initially; revisit if coverage demands it |
| Geometry in RAG | **Both** | Store lat/lng as metadata for vector-db radius filtering; use DuckDB spatial for exact GIS joins |
