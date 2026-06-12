from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

NTS_PATTERN = re.compile(
    r'\b(\d{2}[A-Z](?:\/\d{1,2})?(?:\s*[A-Za-z]?\d{0,2})?)\b'
)

COMPANY_PATTERN = re.compile(
    r'(?:COMPANY|CLIENT|PROPERTY\s+OF|SUBMITTED\s+BY|OWNER|OPERATOR)\s*[:;]?\s*(.+?)(?:\n|$)',
    re.IGNORECASE | re.MULTILINE,
)

COMMODITY_PATTERN = re.compile(
    r'\b(gold|silver|copper|zinc|lead|nickel|cobalt|lithium'
    r'|uranium|molybdenum|tungsten|tin|iron|platinum|palladium'
    r'|rare\s*earth|diamond|graphite|phosphate|potash|salt'
    r'|manganese|vanadium|chromium|antimony|bismuth)\b',
    re.IGNORECASE,
)

REPORT_TYPE_PATTERN = re.compile(
    r'\b(diamond\s*drill|drill\s*(hole|log|program|report|result)'
    r'|geochemical|geochem|soil\s*geochem|lake\s*geochem'
    r'|geophysical|geophys|magnetometer|electromagnetic|EM|IP\s*survey'
    r'|assay|assessment\s*work|progress\s*report'
    r'|compilation|data\s*compilation'
    r'|preliminary\s*economic|PEA|feasibility|scoping)\b',
    re.IGNORECASE,
)

_REPORT_TYPE_MAP = {
    "drill": "assessment_report_drill",
    "assay": "assessment_report_drill",
    "geochem": "assessment_report_geochem",
    "geophysical": "assessment_report_geophys",
    "magnetometer": "assessment_report_geophys",
    "electromagnetic": "assessment_report_geophys",
}


def extract_text(path: str | Path) -> str | None:
    import fitz
    try:
        doc = fitz.open(str(path))
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text.strip() or None
    except Exception as e:
        logger.warning(f"PyMuPDF failed on {path}: {e}")
        return None


def parse_nts(text: str) -> str:
    matches = NTS_PATTERN.findall(text)
    return matches[0] if matches else ""


def parse_company(text: str) -> str:
    for match in COMPANY_PATTERN.finditer(text):
        candidate = match.group(1).strip().rstrip(".")
        if candidate and len(candidate) < 100:
            return candidate
    return ""


def parse_commodities(text: str) -> list[str]:
    return list(set(m.group(0).lower() for m in COMMODITY_PATTERN.finditer(text)))


def parse_report_type(text: str) -> str:
    for match in REPORT_TYPE_PATTERN.finditer(text):
        raw = match.group(0).lower()
        for key, doc_type in _REPORT_TYPE_MAP.items():
            if key in raw:
                return doc_type
    return "assessment_report_general"


def extract_metadata(path: str | Path, text: str | None = None) -> dict:
    if text is None:
        text = extract_text(path)
        if text is None:
            return {"nts_sheet": "", "company": "", "commodities": [], "report_type": "assessment_report_general"}
    first_page = text[:3000]
    return {
        "nts_sheet": parse_nts(first_page),
        "company": parse_company(first_page),
        "commodities": parse_commodities(first_page),
        "report_type": parse_report_type(first_page),
    }
