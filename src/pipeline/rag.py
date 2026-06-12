from __future__ import annotations

import hashlib
import logging
import re as _re
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class Chunker:
    _PARA_SEP = _re.compile(r'\n\s*\n+')

    def chunk(
        self,
        text: str,
        document_metadata: dict = None,
        max_chunk_chars: int = 1400,
        min_chunk_chars: int = 200,
    ) -> list[dict]:
        if document_metadata is None:
            document_metadata = {}
        clean_text = text.strip()
        if len(clean_text) < min_chunk_chars:
            return [self._make_chunk(clean_text, 0, document_metadata)]
        paragraphs = _re.split(self._PARA_SEP, clean_text)
        raw_chunks = self._merge_paragraphs(paragraphs, min_chunk_chars, max_chunk_chars)
        return [self._make_chunk(chunk, i, document_metadata) for i, chunk in enumerate(raw_chunks)]

    def _merge_paragraphs(self, paragraphs: list[str], min_chars: int, max_chars: int) -> list[str]:
        chunks = []
        current = []
        current_len = 0
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if current_len + len(para) > max_chars and current:
                text_block = '\n\n'.join(current).strip()
                if len(text_block) >= min_chars:
                    chunks.append(text_block)
                current = []
                current_len = 0
            current.append(para)
            current_len += len(para)
        if current:
            text_block = '\n\n'.join(current).strip()
            if len(text_block) >= min_chars:
                chunks.append(text_block)
        return chunks if chunks else [paragraphs[0]]

    def _make_chunk(self, text: str, index: int, metadata: dict) -> dict:
        chunk_text = text[:1400]
        anchor = metadata.get("source_url", "") or metadata.get("report_id", "")
        chunk_id = hashlib.sha256(
            f"{metadata.get('jurisdiction','')}:{metadata.get('source_code','')}:{anchor}:{index}".encode("utf-8")
        ).hexdigest()[:32]
        meta = {k: v for k, v in metadata.items()}
        meta["chunk_index"] = index
        return {
            "id": chunk_id,
            "text": chunk_text,
            "metadata": meta,
        }


def ingest_pdf(
    pdf_path: str | Path,
    jurisdiction: str,
    source_code: str,
    report_id: str = "",
    metadata: dict = None,
    store=None,
    chunker=None,
) -> int:
    if chunker is None:
        chunker = Chunker()
    if store is None:
        from vector_store import VectorStore
        store = VectorStore()
    if metadata is None:
        metadata = {}

    from pdf_extract import extract_text, extract_metadata
    text = extract_text(pdf_path)
    if not text:
        logger.warning(f"No text extracted from {pdf_path}")
        return 0
    pdf_meta = extract_metadata(pdf_path, text)
    meta = {
        "jurisdiction": jurisdiction,
        "source_code": source_code,
        "report_id": report_id,
        "doc_type": pdf_meta["report_type"],
        "commodity": json.dumps(pdf_meta["commodities"]),
        "company": pdf_meta["company"],
        "nts_sheet": pdf_meta["nts_sheet"],
        "source_url": str(pdf_path),
        **metadata,
    }
    chunks = chunker.chunk(text, document_metadata=meta)
    records = []
    seen_text: set = set()
    for c in chunks:
        if c["text"] not in seen_text:
            seen_text.add(c["text"])
            records.append(c)
    count = store.upsert(records)
    logger.info(f"Ingested {Path(pdf_path).name}: {len(records)} chunks ({count} stored)")
    return count


def ingest_pdfs(
    pdf_dir: str | Path,
    jurisdiction: str,
    source_code: str,
    file_pattern: str = "*.pdf",
    store=None,
    chunker=None,
    dry_run: bool = False,
) -> dict:
    if store is None:
        from vector_store import VectorStore
        store = VectorStore()
    if chunker is None:
        chunker = Chunker()
    pdf_dir = Path(pdf_dir)
    results = {"total": 0, "ingested": 0, "failed": 0, "skipped": 0, "chunks": 0}
    for pdf in sorted(pdf_dir.glob(file_pattern)):
        results["total"] += 1
        try:
            if dry_run:
                results["ingested"] += 1
                continue
            n = ingest_pdf(pdf, jurisdiction, source_code, report_id=pdf.stem, store=store, chunker=chunker)
            if n > 0:
                results["ingested"] += 1
                results["chunks"] += n
            else:
                results["skipped"] += 1
        except Exception as e:
            logger.error(f"Failed {pdf.name}: {e}")
            results["failed"] += 1
    return results
