#!/usr/bin/env python3
"""
ingest.py — assessment-report PDFs → a per-target, citable chunk store (C5.1).

PLAN_C5 5.1: extract → chunk → embed → one ChromaDB collection per target,
`dd_<target_id>`. The unit that matters throughout is **(report_id, page)**,
because that is the citation a human checks. A chunk that cannot say which page
it came from is not evidence, so page boundaries are never crossed.

Three things the plan called for that the data changed:

  **OCR is not the blocker it was expected to be — for Ontario.** PLAN_C5 says
  "a large fraction of pre-1990 assessment reports are scans; without OCR the
  corpus's best negatives and intercepts are invisible". Measured over the first
  303 pages retrieved: **2% fall below the text threshold**. Ontario has already
  OCR'd its scanned AFRI holdings and ships a text layer. Low-text detection is
  still here and still reports what it finds — BC ARIS may differ, and a
  jurisdiction whose scans are raw must not silently produce an empty corpus —
  but no OCR engine is required to run this today, and none is installed.

  **The chunk ceiling is tokens, not characters.** PLAN_C5 says "under ~2,700
  characters"; audit K2 established the real limit is ~512 *tokens*, which is a
  different quantity for lithology prose than for a page of assay numbers. So
  chunks are cut conservatively and `_embed_one` halves on refusal rather than
  guessing a safe width up front.

  **Table-dense pages are tagged, not skipped.** 5.2 needs the assay tables, and
  they are exactly the pages that chunk worst as prose.

Usage:
    python -m dd.ingest --cell 892b968aac7ffff --radius-km 2
    python -m dd.ingest --cell 892b968aac7ffff --stats
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

EMBED_URL = "http://localhost:8083/embedding"

#: Below this many extractable characters a page is treated as an image. Set
#: from the measured distribution: real text pages in this corpus run 750-2,500
#: chars, so 100 separates "scan" from "sparse page" without catching figures.
LOW_TEXT_CHARS = 100

#: Target chunk width. Deliberately under PLAN_C5's ~2,700-char figure because
#: that figure is in the wrong unit (audit K2) — 1,200 chars of dense assay text
#: can exceed 512 tokens where 2,500 chars of prose does not.
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
MIN_CHUNK_CHARS = 120

#: A page with this share of numeric/whitespace content is a table. Tagged for
#: C5.2 rather than dropped.
TABLE_DIGIT_SHARE = 0.22

DD_DIR = C.PROCESSED_DIR / "dd"


def target_id(cell_id: str, juris: str = "ON") -> str:
    return f"{juris}-{cell_id}"


def collection_name(tid: str) -> str:
    """Chroma collection per target. Chroma restricts names to
    [a-zA-Z0-9._-] and 3-63 chars, which an H3 id satisfies once the
    jurisdiction separator is normalised."""
    return f"dd_{tid}".replace(":", "_")[:63]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _ocr_available() -> bool:
    import shutil
    return bool(shutil.which("tesseract"))


def extract_pages(pdf_path: Path) -> tuple[list[dict], dict]:
    """Per-page text, with a low-text flag and a table flag.

    Returns `(pages, stats)`. Pages keep their 1-based number: the citation a
    reader checks is "page 14 of 42A07SE0009", and a 0-based index would send
    them to the wrong page every time."""
    import fitz

    pages, low, tables = [], 0, 0
    doc = fitz.open(str(pdf_path))
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text().strip()
            is_low = len(text) < LOW_TEXT_CHARS
            digits = sum(c.isdigit() for c in text)
            share = digits / max(1, len(text))
            is_table = share >= TABLE_DIGIT_SHARE and len(text) >= LOW_TEXT_CHARS
            if is_low:
                low += 1
            if is_table:
                tables += 1
            pages.append({"page": i, "text": text, "chars": len(text),
                          "low_text": is_low, "table_dense": is_table})
    finally:
        doc.close()
    return pages, {"pages": len(pages), "low_text_pages": low,
                   "table_pages": tables}


def chunk_page(text: str, width: int = CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split one page, preferring paragraph then sentence boundaries.

    Never spans pages — the page is the citation unit."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= width:
        return [text] if len(text) >= MIN_CHUNK_CHARS else ([text] if text else [])
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + width)
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > width // 2:
                end = start + cut + 1
        piece = text[start:end].strip()
        if len(piece) >= MIN_CHUNK_CHARS:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed_one(text: str, retries: int = 4):
    """Embed one chunk, halving on refusal.

    Same contract as `corpus._embed_one` and for the same reason: the server's
    limit is 512 tokens, which no fixed character width can guarantee."""
    import json as _json
    import urllib.request

    cur = text
    for _ in range(retries):
        try:
            req = urllib.request.Request(
                EMBED_URL, data=_json.dumps({"content": cur}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = _json.loads(r.read())
            v = d[0]["embedding"][0] if isinstance(d, list) else d["embedding"]
            return list(map(float, v))
        except Exception:                                       # noqa: BLE001
            if len(cur) <= MIN_CHUNK_CHARS:
                return None
            cur = cur[: max(MIN_CHUNK_CHARS, len(cur) // 2)]
    return None


def _chroma(tid: str, reset: bool = False):
    import chromadb
    C.CHROMA_DB.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(C.CHROMA_DB))
    name = collection_name(tid)
    if reset:
        try:
            client.delete_collection(name)
        except Exception:                                       # noqa: BLE001
            pass
    try:
        return client.get_collection(name)
    except Exception:                                           # noqa: BLE001
        # Dimension and model recorded on the collection: the pre-existing
        # `geo_canada` collection is 768-d Ollama nomic and holds 4 rows, and
        # mixing that with 1,024-d mxbai vectors would fail at query time with a
        # dimension error nobody could trace back to here (PLAN_C5 5.1).
        return client.create_collection(
            name, metadata={"embedding_model": "mxbai-embed-large-v1.Q4_K_M",
                            "dimension": 1024, "endpoint": EMBED_URL,
                            "component": "C5.1", "target_id": tid})


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def ingest_target(cell_id: str, juris: str = "ON", radius_km: float = 2.0,
                  max_reports: int | None = None, fetch: bool = True,
                  reset: bool = False, quiet: bool = False) -> dict:
    """Fetch (C3.4) then ingest every retrievable report for one target."""
    import reports as R

    tid = target_id(cell_id, juris)
    geom = R.cell_polygon_wkt(cell_id, radius_km)
    if fetch:
        reps = R.fetch_reports(geom, juris, max_reports, quiet=quiet)
    else:
        reps = R.resolve_reports(geom, juris, max_reports)
        for r in reps:
            p = R.pdf_dir(juris, R.INDEX_LAYERS[juris]["code"]) / f"{r['report_id']}.pdf"
            if p.exists():
                r["pdf_path"], r["status"] = str(p), "cached"
            else:
                r["status"] = "failed"

    coverage = R.coverage_summary(reps)
    usable = [r for r in reps if r.get("pdf_path")]
    if not usable:
        return {"target_id": tid, "ingested": 0, "chunks": 0,
                "coverage": coverage,
                "error": "no PDFs on disk for this target"}

    col = _chroma(tid, reset=reset)
    ocr = _ocr_available()
    n_chunks = n_embedded = n_low = n_tables = n_pages = 0
    per_report = []

    for rep in usable:
        pdf = Path(rep["pdf_path"])
        try:
            pages, st = extract_pages(pdf)
        except Exception as e:                                  # noqa: BLE001
            per_report.append({"report_id": rep["report_id"],
                               "error": f"{type(e).__name__}: {e}"})
            continue
        n_pages += st["pages"]
        n_low += st["low_text_pages"]
        n_tables += st["table_pages"]

        ids, docs, metas, vecs = [], [], [], []
        for pg in pages:
            if pg["low_text"]:
                continue
            for j, chunk in enumerate(chunk_page(pg["text"])):
                n_chunks += 1
                v = _embed_one(chunk)
                if v is None:
                    continue
                ids.append(f"{rep['report_id']}:p{pg['page']}:c{j}")
                docs.append(chunk)
                metas.append({
                    "report_id": rep["report_id"],
                    "page": pg["page"],
                    "year": rep.get("year") or 0,
                    "juris": juris,
                    "title": rep.get("title") or "",
                    "work_types": ", ".join(rep.get("work_types") or []),
                    "table_dense": bool(pg["table_dense"]),
                    "info_link": rep.get("info_link") or "",
                })
                vecs.append(v)
        if ids:
            for k in range(0, len(ids), 256):
                col.upsert(ids=ids[k:k+256], documents=docs[k:k+256],
                           metadatas=metas[k:k+256], embeddings=vecs[k:k+256])
            n_embedded += len(ids)
        per_report.append({
            "report_id": rep["report_id"], "year": rep.get("year"),
            "pages": st["pages"], "low_text_pages": st["low_text_pages"],
            "table_pages": st["table_pages"], "chunks": len(ids),
        })
        if not quiet:
            print(f"    {rep['report_id']:14s} {st['pages']:4d}p  "
                  f"{len(ids):4d} chunks  "
                  f"{st['table_pages']:3d} table-dense  "
                  f"{st['low_text_pages']:2d} low-text")

    manifest = {
        "target_id": tid, "cell_id": cell_id, "juris": juris,
        "radius_km": radius_km, "collection": collection_name(tid),
        "embedding": {"model": "mxbai-embed-large-v1.Q4_K_M", "dimension": 1024,
                      "endpoint": EMBED_URL},
        "reports": per_report,
        "pages": n_pages, "chunks_made": n_chunks, "chunks_embedded": n_embedded,
        "low_text_pages": n_low, "table_pages": n_tables,
        "ocr_engine": "tesseract" if ocr else None,
        "ocr_note": (None if ocr else
                     "No OCR engine installed. Ontario AFRI ships a text layer "
                     f"({n_low} of {n_pages} pages fell below the threshold), so "
                     "this did not matter here; a jurisdiction with raw scans "
                     "would need `apt install tesseract-ocr ocrmypdf`."),
        "coverage": coverage,
    }
    DD_DIR.mkdir(parents=True, exist_ok=True)
    (DD_DIR / f"{tid}.json").write_text(json.dumps(manifest, indent=1))
    if not quiet:
        print(f"\n  {n_embedded} chunks embedded from {len(per_report)} reports, "
              f"{n_pages} pages → collection {collection_name(tid)}")
        if n_low:
            print(f"  {n_low} low-text pages skipped"
                  + ("" if ocr else " (no OCR engine installed)"))
        print(f"  → {DD_DIR / (tid + '.json')}")
    return manifest


def stats(cell_id: str, juris: str = "ON") -> dict | None:
    tid = target_id(cell_id, juris)
    p = DD_DIR / f"{tid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def main():
    ap = argparse.ArgumentParser(description="C5.1 — per-target report ingestion")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--juris", default="ON")
    ap.add_argument("--radius-km", type=float, default=2.0)
    ap.add_argument("--max", type=int, default=None, dest="max_reports")
    ap.add_argument("--no-fetch", action="store_true",
                    help="use PDFs already on disk")
    ap.add_argument("--reset", action="store_true", help="rebuild the collection")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    C.require_lake()

    if args.stats:
        s = stats(args.cell, args.juris)
        print(json.dumps(s, indent=1) if s else "no ingestion manifest for this target")
        return
    ingest_target(args.cell, args.juris, args.radius_km, args.max_reports,
                  fetch=not args.no_fetch, reset=args.reset)


if __name__ == "__main__":
    main()
