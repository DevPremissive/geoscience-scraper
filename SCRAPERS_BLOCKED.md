# Blocked PDF Scrapers — Design & Remediation Plan

## Overview

7 legacy assessment-report systems cannot be harvested via simple HTTP GET. Each is
blocked by a distinct anti-scraping mechanism. Below is the per-system analysis and
remediation strategy.

| Code | Jurisdiction | Records | Blocking Mechanism | Effort |
|------|-------------|---------|-------------------|--------|
| NL_GEOFILES_PDF | NL | 5,000+ | ASP.NET handler, 500/404 on known batch dirs | 2-3 days |
| SK_SMAD_PDF | SK | ~14,889 | ASP.NET viewstate / CAPTCHA on download page | 3-4 days |
| NS_DCDH | NS | 28,341 drillholes | JSF search portal (NovaScan), no direct URL | 2-3 days |
| AB_AGS | AB | 20,000+ | No public PDF URL — AGS only serves own publications | 1-2 days |
| NB_GEOSCIDB | NB | reports of work | PARIS web portal, detail page must be scraped | 1-2 days |
| NTGS | NT/NU | unknown | ASP.NET postback search, no REST API, empty ID list | 2-3 days |
| MB_MAPGALLERY | MB | unknown | No scrape code written, no ArcGIS REST endpoint found | 2-3 days |

## Per-Scraper Analysis

### NL_GEOFILES_PDF
- **Enumeration works**: 5,000+ GEOFILE_NO IDs via GeoAtlas MapServer layer 2
- **Block**: PDF server (`gis.geosurv.gov.nl.ca`) runs ASP.NET handler that returns 500 or 404
  for all 14 known batch directory patterns (`Batch2008` through `Batch2024`, `WBox040`)
- **Strategy**: The GeoAtlas web app (`gis.gov.nl.ca/minesen/geofiles/`) has a download
  button that probably triggers a POST to a handler URL with a session token. Need to:
  1. Open GeoAtlas in a browser with devtools (network tab)
  2. Interact with the search/download UI to capture the real request
  3. Replay with a session cookie
- **Fallback**: MapServer `query` with `f=html` returns HTML tables with download links
  when queried with `returnAttachments=true`

### SK_SMAD_PDF
- **Enumeration works**: ~14,889 FILENUMBER IDs via ER GIS FeatureServer (3 layers)
- **Block**: The SK mineral assessment search portal (`mineral-assessment.saskatchewan.ca`)
  uses ASP.NET WebForms with `__VIEWSTATE` + `__EVENTVALIDATION` hidden fields. A GET to
  the `pdf_url` returns the search result page, not the PDF binary.
- **Strategy**: Two-phase approach:
  1. GET the search page, extract viewstate fields
  2. POST search form with FILENUMBER + viewstate
  3. Parse the results page for a download link (likely an ASP.NET `Download.aspx` handler)
- **Risk**: The site may use CAPTCHA or rate limiting after N requests.
  Consider Camoufox browser with cookie persistence.

### NS_DCDH
- **Enumeration works**: OBJECTIDs from 3-layer FeatureServer (drillholes, references, core)
- **Block**: PDFs are served through NovaScan (`gesner.novascotia.ca/novascan/`) — a
  JSF (Java Server Faces) application. No direct download URL pattern discovered.
- **Strategy**: Similar to NL — use devtools in a browser session to identify the
  actual download endpoint triggered by the "View" button in NovaScan.
- **Enrichment path**: The drillhole references table (layer 1) contains `REPORT_NO` fields
  that reference assessment report numbers. A tabular join gives the report→drillhole mapping
  without PDF download, enabling RAG embedding of the metadata alone.

### AB_AGS
- **Enumeration works**: 20,000+ AssessmentNumber IDs via ABMARS MapServer
- **Block**: The Alberta Energy Regulator / AGS does NOT serve mineral assessment report
  PDFs through any public web endpoint. The `pdf_url` function returns the ABMARS query
  result (JSON), not a PDF.
- **Strategy**: 
  1. Confirm with AGS whether assessment report PDFs exist publicly
  2. The `static.ags.aer.ca/files/document/OFR/OFR_YYYY_XX.pdf` pattern only serves
     AGS-published open-file reports (not industry-submitted assessments)
  3. For now, mark as "no public access" and embed just the metadata

### NB_GEOSCIDB
- **Enumeration works**: REPORT IDs via NBGS Reports Of Work FeatureServer
- **Block**: PDFs are served through the PARIS web portal at
  `dnr-mrn.gnb.ca/ParisWeb/Assessmentreportdetails.aspx?Num={id}`. The detail page
  likely contains a download link or an embedded PDF viewer.
- **Strategy**:
  1. GET the detail page, parse for download links (`href` containing `.pdf` or
     `Download.aspx`)
  2. If the PDF is embedded in an `<iframe>`, extract the `src` attribute
  3. Simple — likely achievable with plain `urllib` + HTML parsing

### NTGS (Northwest Territories / Nunavut)
- **Enumeration is empty**: `_nt_enumerate()` returns `[]` because the NTGS search runs
  on ASP.NET postback — no clean pagination API exists.
- **Block**: The entire enumeration phase is blocked. Without IDs, no PDFs can be fetched.
- **Strategy**: 
  1. Reverse-engineer the `ReferenceSearch.aspx` page form submissions
  2. OR compile a known reference number list from public NTGS publications lists
     (refnum format: `082133`, `2016-05`, `2015-014`)
  3. OR use Camoufox to fill in the search form and iterate through results

### MB_MAPGALLERY (Manitoba)
- **No scrape code exists**: No `_mb_*` functions in `scrape.py`
- **Status**: Manitoba's MapGallery (`manitoba.ca/geo-env/geoscience/mapgallery`) is an
  interactive web map application. No ArcGIS REST API, CKAN, or bulk download found.
- **Strategy**: 
  1. Investigate whether the web app makes XHR requests to a hidden API
  2. Check `maps.gov.mb.ca` for undiscovered ArcGIS services
  3. Contact MB Mineral Resources Division for data access

## Shared Infrastructure

For scrapers requiring session handling (NL, SK, NS), the recommended approach:

### Camoufox Session Manager

Follow the approach from the mining-scraper project (see `CLAUDE.md` § Sprint F):

```
src/connectors/browser/
    session_manager.py     # Session pool with cookie persistence
    identities.py          # OS/screen/timezone configs
```

Key design points:
- **Rate limit**: 1 req/10s per identity, 5 identities ⇒ ~200 req/day
- **Session replay**: Before using the browser, try direct HTTP GET with stored cookies
- **Fallback chain**: Browser → requests with cookies → plain HTTP

### Estimated Total Effort

| Scraper | Browser Devtools | Implementation | Testing | Total |
|---------|-----------------|----------------|---------|-------|
| NL_GEOFILES_PDF | 0.5 day | 1 day | 0.5 day | 2 days |
| SK_SMAD_PDF | 0.5 day | 1.5 days | 0.5 day | 2.5 days |
| NS_DCDH | 0.5 day | 1 day | 0.5 day | 2 days |
| NB_GEOSCIDB | — | 0.5 day | 0.5 day | 1 day |
| NTGS | 1 day | 1 day | 0.5 day | 2.5 days |
| MB_MAPGALLERY | 0.5 day | 1 day | 0.5 day | 2 days |
| AB_AGS | — | 0.5 day | — | 0.5 day |
| Camoufox infra | — | 1 day | 0.5 day | 1.5 days |
| **Total** | **3 days** | **7.5 days** | **3 days** | **~14 days** |
