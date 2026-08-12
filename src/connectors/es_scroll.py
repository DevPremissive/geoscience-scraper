"""
connectors/es_scroll.py — Elasticsearch scroll-style connector for structured data.

The GeoHub ES API (and similar wrappers) don't support native ES scroll. Instead
we page via search_after with _doc sort (index-order scan), which is:
- More efficient than from/size (no deep-paging penalty)
- Not limited by ES max_result_window (default 10000)
- Deterministic (no duplicates or gaps in a single scan)

Output: JSONL to the raw data directory.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import config as C

logger = logging.getLogger(__name__)

_GEO_ONTARIO_API_KEY = C.ON_API_KEY


def _fetch_page(endpoint: str, api_key: str, index: str, body: dict) -> dict:
    api_key = api_key or C.require_on_api_key()
    payload = json.dumps({
        "indexes": index,
        "body": json.dumps(body),
    }).encode()
    req = Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": api_key,
            "User-Agent": C.USER_AGENT,
        },
    )
    with urlopen(req, timeout=C.TIMEOUT) as resp:
        return json.loads(resp.read())


def fetch_scroll(
    endpoint: str,
    index: str,
    api_key: str = _GEO_ONTARIO_API_KEY,
    dest: Optional[Path] = None,
    page_size: int = 1000,
    query: Optional[dict] = None,
    max_records: Optional[int] = None,
) -> dict:
    query = query or {"match_all": {}}
    page = 0
    total_fetched = 0
    search_after: list = []
    has_more = True
    fh = None

    if dest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fh = open(dest, "w")

    try:
        while has_more:
            es_body: dict = {
                "size": page_size,
                "query": query,
                "sort": ["_doc"],
            }
            if search_after:
                es_body["search_after"] = search_after

            data = _fetch_page(endpoint, api_key, index, es_body)
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)

            for hit in hits:
                source = hit.get("_source", {})
                source.setdefault("_es_index", index)
                source.setdefault("_es_id", hit.get("_id", ""))
                source.setdefault("_es_score", hit.get("_score"))
                if fh:
                    fh.write(json.dumps(source, default=str) + "\n")
                total_fetched += 1

            page += 1
            if page % 10 == 0:
                logger.info(
                    "es_scroll %s: page %d, fetched %d / %d",
                    index, page, total_fetched, total,
                )

            has_more = len(hits) == page_size
            if has_more:
                search_after = hits[-1].get("sort", [])
                if not search_after:
                    has_more = False
                if max_records and total_fetched >= max_records:
                    has_more = False
            time.sleep(C.REQUEST_GAP)

    finally:
        if fh:
            fh.close()

    return {
        "index": index,
        "total": total,
        "fetched": total_fetched,
        "pages": page,
        "path": str(dest) if dest else None,
    }


def discover(endpoint: str, index: str, api_key: str = _GEO_ONTARIO_API_KEY) -> dict:
    """Get total count for an index without fetching all records."""
    data = _fetch_page(endpoint, api_key, index, {
        "size": 0,
        "query": {"match_all": {}},
    })
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    return {"index": index, "total": total}
