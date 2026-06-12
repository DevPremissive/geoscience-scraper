from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# Geoscience-specific document types for assessment reports
ASSESSMENT_DOC_TYPES = {
    "assessment_report_drill": "Drill log / assay results",
    "assessment_report_geochem": "Geochemical survey",
    "assessment_report_geophys": "Geophysical survey",
    "assessment_report_general": "General exploration report",
}

# Structured data feature types (for tabular per-row embeddings)
FEATURE_TYPES = {
    "drill_hole": "Drill hole record",
    "mineral_occurrence": "Mineral occurrence / deposit",
    "mineral_claim": "Mineral tenure claim",
    "geochem_sample": "Geochemical sample",
    "publication": "OGS / BCGS publication",
    "abandoned_mine": "Abandoned mine site",
}


@dataclass
class RAGChunk:
    id: str
    chunk_text: str
    jurisdiction: str
    source_code: str
    doc_type: str
    chunk_index: int
    source_url: str = ""
    report_id: str = ""
    commodity: str = ""
    company: str = ""
    nts_sheet: str = ""
    feature_type: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_record(self) -> dict:
        return {
            "id": self.id,
            "text": self.chunk_text,
            "metadata": {
                "jurisdiction": self.jurisdiction,
                "source_code": self.source_code,
                "doc_type": self.doc_type,
                "chunk_index": self.chunk_index,
                "source_url": self.source_url,
                "report_id": self.report_id,
                "commodity": self.commodity,
                "company": self.company,
                "nts_sheet": self.nts_sheet,
                "feature_type": self.feature_type,
                **self.metadata,
            },
        }


@dataclass
class CoverageProfile:
    jurisdiction: str
    populated: dict = field(default_factory=dict)
    expected_codes: list[str] = field(default_factory=list)

    def add_source(self, code: str, count: int = 1) -> None:
        if code not in self.populated:
            self.populated[code] = 0
        self.populated[code] += count

    def score(self) -> float:
        if not self.expected_codes:
            return 0.0
        covered = sum(1 for c in self.expected_codes if c in self.populated)
        return round(covered / len(self.expected_codes), 2)

    def gaps(self) -> list[str]:
        return [c for c in self.expected_codes if c not in self.populated]
