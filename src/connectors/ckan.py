"""
connectors/ckan.py — generic CKAN harvester.

Works against ANY CKAN portal (open.canada.ca, data.ontario.ca,
catalogue.data.gov.bc.ca, ...). CKAN's package_search/package_show API is identical
everywhere, so one implementation covers every CKAN jurisdiction in sources.py.
"""
from __future__ import annotations
import json, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config as C


def _action(portal: str, action: str, **params) -> dict:
    url = portal.rstrip("/") + C.CKAN_ACTION_PATH + "/" + action
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": C.USER_AGENT})
    with urlopen(req, timeout=C.TIMEOUT) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if not payload.get("success"):
        raise RuntimeError(f"CKAN {action} failed on {portal}: {payload.get('error')}")
    return payload["result"]


def _label(title: str, match: dict[str, list[str]]) -> str | None:
    t = title.lower()
    for code, needles in match.items():
        if any(n.lower() in t for n in needles):
            return code
    return None


def discover(spec: dict, jurisdiction: str) -> list[dict]:
    """Return one record per (dataset, resource) for a CKAN source spec."""
    portal = spec["portal"]
    fq = spec.get("fq", "")
    match = spec.get("match", {})
    out: list[dict] = []
    start = 0
    while True:
        res = _action(portal, "package_search", fq=fq, rows=100, start=start)
        results = res.get("results", [])
        for pkg in results:
            title = pkg.get("title") or pkg.get("name", "")
            code = _label(title, match)
            if match and code is None:
                continue                       # filtered out by this source's match map
            code = code or f"{jurisdiction}_MISC"
            for r in pkg.get("resources", []):
                fmt = (r.get("format") or "").strip().lower().lstrip(".")
                out.append({
                    "jurisdiction": jurisdiction,
                    "connector": "ckan",
                    "code": code,
                    "dataset": title,
                    "resource_id": r.get("id"),
                    "resource_name": r.get("name") or r.get("id"),
                    "format": fmt,
                    "url": r.get("url"),
                    "last_modified": r.get("last_modified") or r.get("created"),
                    "size": r.get("size"),
                    "license": pkg.get("license_title"),
                    "portal": portal,
                })
        start += len(results)
        if not results or start >= res.get("count", 0):
            break
        time.sleep(C.REQUEST_GAP)
    return out
