#!/usr/bin/env python3
"""
extract_assays.py — barren confirmation and intercept extraction (C5.2).

Purpose, from PLAN_C5 5.2: upgrade C2.4's Tier-1 provisional negatives to
**Tier-2 confirmed**, and extract the positive corollary — intercepts on ground
that was drilled and dropped. Those are the two most valuable things a
sixty-year-old assessment report contains, and neither is in any index.

**Step 1 turned out to be free.** The plan says to get `(hole_id, report_id)`
pairs "from OAFD/ARIS index metadata linking reports to drill programs, or from
5.1 retrieval when the link is implicit". Ontario publishes the link directly:
every one of the **172,259 rows in `ON_OMEIS_DRILLHOLE` carries a `TECH_ID`**,
which is the assessment report it came from. No inference, no retrieval
heuristics — a join.

**Step 2 does not work as specified, and says so.** The plan wants pdfplumber
then camelot on the table-dense pages. Neither is installed, and more to the
point PyMuPDF's own table finder returns **zero** tables on these pages: they
are scans without ruling lines, so there is no table structure to find, only
OCR'd text in roughly tabular order. So the LLM pass reads the text of
table-dense pages directly. That is weaker than parsed tables and the confidence
recorded on each verdict reflects it.

**The QA gate is binding and it is human.** PLAN_C5 5.2 step 5: a 10% random
sample of every batch is human-reviewed before any Tier-2 label enters model
training, and extraction that cannot hit ≥95% verdict precision on that sample
stays out of training. This module therefore writes `review_status="auto"` and
`training_eligible=False` on everything it produces, and only a human running
`--review` can change that. Nothing here promotes itself.

Usage:
    python -m dd.extract_assays --plan --min-holes 5 --max-reports 14
    python -m dd.extract_assays --run --max-reports 14
    python -m dd.extract_assays --review           # the binding human QA gate
    python -m dd.extract_assays --status
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from dd import ingest as I
from dd import rag as RAG

OUT_DIR = C.PROCESSED_DIR / "dd"
NONBARREN = C.PROCESSED_DIR / "nonbarren_holes.parquet"
TIER2 = C.PROCESSED_DIR / "negatives_tier2.parquet"
BATCHES = OUT_DIR / "assay_batches.json"

VERDICTS = ("barren", "mineralized", "ambiguous")

SCHEMA_PROMPT = """You are reading a mineral exploration assessment report to decide, \
for ONE drill hole, whether it hit mineralization worth reporting.

Return ONE JSON object and nothing else:

{{"hole_id_in_report": "<hole name as written in the report, or null>",
  "depth_from": <number or null>, "depth_to": <number or null>,
  "commodities_assayed": ["Au", ...],
  "best_result_per_commodity": {{"Au": "<verbatim figure with units>", ...}},
  "verdict": "barren" | "mineralized" | "ambiguous",
  "verdict_quote": "<verbatim sentence from the excerpts that justifies the verdict>",
  "page": <page number the quote is on>,
  "confidence": "high" | "medium" | "low"}}

RULES:
- Quote figures and the justifying sentence VERBATIM. Never round or convert.
- "barren" means the report says the hole returned no significant values.
  "mineralized" means a reported assay or intercept worth noting.
  "ambiguous" means the excerpts do not settle it — use this freely; it is the
  safe answer and a human reads it.
- If the excerpts never mention this hole, verdict is "ambiguous",
  verdict_quote is null, confidence "low".
- These pages are OCR'd scans. If a figure is garbled, copy it as printed and
  set confidence "low" rather than guessing what it meant.

HOLE: {hole}

EXCERPTS:
{context}

JSON:"""


# ---------------------------------------------------------------------------

def candidate_reports(min_holes: int = 5, max_holes: int = 40,
                      year_from: int = 1985, year_to: int = 1999,
                      limit: int = 20) -> list[dict]:
    """Reports with enough linked holes to be worth a batch.

    Bounded to the era whose PDFs actually retrieve — audit P1 measured 0/12 for
    2010+ — because a batch of unfetchable reports is not a batch."""
    import duckdb
    con = duckdb.connect(str(C.CATALOG_DB), read_only=True)
    con.execute("LOAD spatial")
    try:
        rows = con.execute(f'''
            SELECT d."TECH_ID" AS report_id, count(*) AS holes,
                   max(d."YEAR_DRILLED") AS year,
                   any_value(t."PROPERTY") AS property
            FROM geo_ON__ON_OMEIS_DRILLHOLE d
            JOIN geo_ON__ON_OMEIS_TECHFILE t ON t."TECH_ID" = d."TECH_ID"
            WHERE d."YEAR_DRILLED" BETWEEN {year_from} AND {year_to}
            GROUP BY 1
            HAVING count(*) BETWEEN {min_holes} AND {max_holes}
            ORDER BY holes DESC LIMIT {limit}''').fetchall()
        names = [d[0] for d in con.description]
    finally:
        con.close()
    return [dict(zip(names, r)) for r in rows]


def holes_for(report_id: str) -> list[dict]:
    import duckdb
    con = duckdb.connect(str(C.CATALOG_DB), read_only=True)
    con.execute("LOAD spatial")
    try:
        rows = con.execute('''
            SELECT "HOLE_IDENT", "COMPANY_HOLE_IDENT", "YEAR_DRILLED", "LENGTH",
                   "ELEMENTS", "COMPANY_NAME", "PROPERTY_NAME",
                   "LATITUDE_DD", "LONGITUDE_DD"
            FROM geo_ON__ON_OMEIS_DRILLHOLE WHERE "TECH_ID" = ?''',
            [report_id]).fetchall()
        names = [d[0] for d in con.description]
    finally:
        con.close()
    return [dict(zip(names, r)) for r in rows]


# ---------------------------------------------------------------------------

def _json_from(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:                                           # noqa: BLE001
        return None


def extract_hole(tid: str, hole: dict, k: int = 8) -> dict:
    """One hole → one verdict, cited."""
    name = hole.get("COMPANY_HOLE_IDENT") or hole.get("HOLE_IDENT") or ""
    query = (f"drill hole {name} assay results core intersected "
             f"{hole.get('ELEMENTS') or ''} sampled section from to metres "
             f"no significant values")
    chunks = RAG.retrieve(tid, query, k=k)
    lex = [c for c in RAG.lexical_hits(tid, limit=6)
           if c["chunk_id"] not in {x["chunk_id"] for x in chunks}]
    chunks += lex
    if name:
        named = [c for c in RAG._all_chunks(tid)
                 if name.lower() in c["text"].lower()
                 and c["chunk_id"] not in {x["chunk_id"] for x in chunks}]
        chunks += named[:4]
    if not chunks:
        return {"verdict": "ambiguous", "confidence": "low",
                "verdict_quote": None, "reason": "no chunks retrieved"}

    context = "\n\n".join(f"[p.{c['page']}]\n{c['text']}" for c in chunks)
    label = (f"{name} (company id) / {hole.get('HOLE_IDENT')} (OMEIS id), "
             f"{hole.get('YEAR_DRILLED')}, length {hole.get('LENGTH')}, "
             f"elements {hole.get('ELEMENTS')}")
    raw = RAG._chat(SCHEMA_PROMPT.format(hole=label, context=context),
                    max_tokens=800)
    obj = _json_from(raw)
    if obj is None:
        return {"verdict": "ambiguous", "confidence": "low",
                "verdict_quote": None, "reason": "model returned no parsable JSON"}
    if obj.get("verdict") not in VERDICTS:
        obj["verdict"] = "ambiguous"
        obj["confidence"] = "low"
    # DERIVE the page; never ask for it.
    #
    # The 2026-08-21 review found wrong page numbers on 4 of 18 rows — the
    # largest single failure class, larger than misread figures, and not an OCR
    # problem at all. The model was being handed chunks labelled `[p.N]` and
    # asked to report which page its quote came from, which is a question it can
    # get wrong. We already know the answer: every chunk carries its true page
    # in metadata. So find the chunk that actually contains the quote and take
    # the page from there. A citation should never be a model output.
    q = obj.get("verdict_quote")
    model_page = obj.get("page")
    obj["page_reported_by_model"] = model_page
    obj["quote_verified"] = None
    obj["page_source"] = None
    norm = lambda t: re.sub(r"[^a-z0-9./%+]+", " ", (t or "").lower()).strip()
    if q:
        nq = norm(q)[:60]
        hit = next((c for c in chunks if nq and nq in norm(c["text"])), None)
        if hit is not None:
            obj["page"] = hit["page"]
            obj["quote_verified"] = True
            obj["page_source"] = "derived from the chunk containing the quote"
            if model_page not in (None, hit["page"]):
                obj["page_corrected_from"] = model_page
        else:
            # The quote is in none of the excerpts, so it was not copied from
            # them. That is a fabrication signal, not a page problem.
            obj["quote_verified"] = False
            obj["confidence"] = "low"
            obj["page_source"] = "model-reported; quote found in no excerpt"

    # Attribution: the note must be about THIS hole. The review found one row
    # where GE-54 carried a sentence about GE-55.
    # Attribution. The review found GE-54 carrying a sentence about GE-55, so
    # the question worth asking is not "does the quote name this hole" —
    # plenty of legitimate quotes say "the hole intersected", and that check
    # fired on 4 of 6 rows, which makes it noise. The question is whether the
    # quote names a DIFFERENT hole from the same programme. That is the actual
    # failure and it is rare enough to be worth flagging.
    name = (hole.get("COMPANY_HOLE_IDENT") or hole.get("HOLE_IDENT") or "").strip()
    obj["hole_named_in_quote"] = None
    obj["other_holes_in_quote"] = None
    if name and q:
        obj["hole_named_in_quote"] = name.lower() in q.lower()
        # Hole ids in this corpus look like GE-54, H89-36, CB-111, K-88-32.
        # Case-SENSITIVE and hyphen-required. Without both, "to 553" parses as
        # a hole id and the warning fires on almost everything.
        ids = set(re.findall(r"\b[A-Z]{1,3}\d{0,2}-\d{1,3}[A-Z]?\b", q))
        others = sorted({i for i in ids
                         if i.replace(" ", "-").lower() != name.replace(" ", "-").lower()
                         and not obj["hole_named_in_quote"]})
        if others:
            obj["other_holes_in_quote"] = others
            obj["confidence"] = "low"
            obj["attribution_warning"] = (
                f"the quote names {', '.join(others[:3])} but not {name} — it "
                f"may describe a different hole in the same programme")
    return obj


def run_batch(max_reports: int = 14, min_holes: int = 5, radius_km: float = 0.0,
              limit_holes: int | None = None, quiet: bool = False,
              year_from: int = 1985, year_to: int = 1999) -> dict:
    """Fetch, ingest and extract over a batch of drilling reports."""
    import pandas as pd
    import reports as R

    cands = candidate_reports(min_holes=min_holes, limit=max_reports * 3,
                              year_from=year_from, year_to=year_to)
    batch_reports, rows = [], []
    processed = 0

    for cand in cands:
        if len(batch_reports) >= max_reports:
            break
        rid = cand["report_id"]
        # A batch corpus per report: the report IS the target here, not a cell.
        tid = f"ON-report-{rid}"
        pdf = R.pdf_dir("ON", "ON_AFRI_PDF") / f"{rid}.pdf"
        if not pdf.exists():
            # Ask the publisher what the file is called. The legacy
            # `{id}.Pdf` pattern only covers the pre-2000 scanned era, and
            # calling sc.pdf_url() here was the reason every modern report came
            # back "not retrievable" — the P9 fix had been applied to
            # fetch_reports() and not to this path.
            from connectors import scrape as SC
            sc = SC.SCRAPERS["ON_AFRI_PDF"]
            base = sc.pdf_url(rid).rsplit("/", 1)[0]
            got = False
            for fn in R.resolve_pdf_files(rid, "ON"):
                try:
                    R._download(f"{base}/{fn}", pdf)
                    got = True
                    break
                except Exception:                               # noqa: BLE001
                    continue
            if not got:
                if not quiet:
                    print(f"  - {rid}: not retrievable")
                continue
        # Ingest this single report into its own collection.
        try:
            pages, st = I.extract_pages(pdf)
        except Exception as e:                                  # noqa: BLE001
            if not quiet:
                print(f"  ! {rid}: {type(e).__name__}: {e}")
            continue
        col = I._chroma(tid, reset=True)
        ids, docs, metas, vecs = [], [], [], []
        for pg in pages:
            if pg["low_text"]:
                continue
            for j, ch in enumerate(I.chunk_page(pg["text"])):
                v = I._embed_one(ch)
                if v is None:
                    continue
                ids.append(f"{rid}:p{pg['page']}:c{j}")
                docs.append(ch)
                metas.append({"report_id": rid, "page": pg["page"],
                              "year": int(cand["year"] or 0), "juris": "ON",
                              "title": cand.get("property") or "",
                              "work_types": "", "table_dense": bool(pg["table_dense"]),
                              "info_link": ""})
                vecs.append(v)
        if not ids:
            continue
        for kk in range(0, len(ids), 256):
            col.upsert(ids=ids[kk:kk+256], documents=docs[kk:kk+256],
                       metadatas=metas[kk:kk+256], embeddings=vecs[kk:kk+256])

        holes = holes_for(rid)
        if limit_holes:
            holes = holes[:limit_holes]
        vd = {"barren": 0, "mineralized": 0, "ambiguous": 0}
        for h in holes:
            r = extract_hole(tid, h)
            vd[r.get("verdict", "ambiguous")] += 1
            rows.append({
                "report_id": rid, "hole_id": h.get("HOLE_IDENT"),
                "company_hole_id": h.get("COMPANY_HOLE_IDENT"),
                "year_drilled": h.get("YEAR_DRILLED"),
                "length_m": h.get("LENGTH"),
                "elements_indexed": h.get("ELEMENTS"),
                "latitude": h.get("LATITUDE_DD"), "longitude": h.get("LONGITUDE_DD"),
                "hole_id_in_report": r.get("hole_id_in_report"),
                "depth_from": r.get("depth_from"), "depth_to": r.get("depth_to"),
                "commodities_assayed": json.dumps(r.get("commodities_assayed") or []),
                "best_result": json.dumps(r.get("best_result_per_commodity") or {}),
                "verdict": r.get("verdict"), "verdict_quote": r.get("verdict_quote"),
                "page": r.get("page"), "confidence": r.get("confidence"),
                "quote_verified": r.get("quote_verified"),
                "page_source": r.get("page_source"),
                "page_corrected_from": r.get("page_corrected_from"),
                "hole_named_in_quote": r.get("hole_named_in_quote"),
                "other_holes_in_quote": json.dumps(r.get("other_holes_in_quote") or []),
                "attribution_warning": r.get("attribution_warning"),
                # Binding: nothing self-promotes into training (PLAN_C5 5.2 step 5).
                "review_status": "auto", "training_eligible": False,
                "reviewed_by": None, "review_verdict": None,
            })
            processed += 1
        batch_reports.append({"report_id": rid, "holes": len(holes),
                              "pages": st["pages"], "chunks": len(ids),
                              "verdicts": vd, "property": cand.get("property")})
        if not quiet:
            print(f"  {rid:14s} {len(holes):3d} holes  {st['pages']:4d}p  "
                  f"barren={vd['barren']:2d} mineralized={vd['mineralized']:2d} "
                  f"ambiguous={vd['ambiguous']:2d}")

    if not rows:
        return {"error": "no holes processed"}

    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Positives: intercepts on ground someone drilled and walked away from.
    nb = df[df["verdict"] == "mineralized"]
    if len(nb):
        nb.to_parquet(NONBARREN, index=False)
    # Tier-2 candidates. NOT merged into negatives.parquet: C2.4's consumption
    # rule says Tier-2 enters training only after the human QA gate.
    bar = df[df["verdict"] == "barren"]
    if len(bar):
        bar.to_parquet(TIER2, index=False)

    batch = {
        "batch_id": f"assay-{len(batch_reports)}r-{processed}h",
        "era": f"{year_from}-{year_to}",
        "reports": batch_reports, "holes_processed": processed,
        "verdicts": df["verdict"].value_counts().to_dict(),
        "quote_verified": int(df["quote_verified"].fillna(False).sum()),
        "confidence": df["confidence"].value_counts().to_dict(),
        "tier2_candidates": int(len(bar)), "nonbarren": int(len(nb)),
        "qa": {"required_sample_pct": 10, "reviewed": 0, "precision": None,
               "gate": "PLAN_C5 5.2 step 5 — 10% human-reviewed at >=95% verdict "
                       "precision before any Tier-2 label enters training",
               "passed": False},
        "training_eligible": False,
        "table_extraction": "text of table-dense pages only; pdfplumber and "
                            "camelot are not installed and PyMuPDF's table "
                            "finder returns 0 tables on these ruled-line-free "
                            "scans (audit P3)",
    }
    BATCHES.write_text(json.dumps(batch, indent=1))
    if not quiet:
        print(f"\n  {processed} holes across {len(batch_reports)} reports")
        print(f"  verdicts: {batch['verdicts']}")
        print(f"  {batch['tier2_candidates']} Tier-2 candidates → {TIER2.name}")
        print(f"  {batch['nonbarren']} intercepts → {NONBARREN.name}")
        print(f"  QA GATE NOT PASSED — run --review; nothing is training-eligible")
    return batch


def _page_no(v) -> str:
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return "?"


def _page_of(pdf: Path) -> str:
    try:
        import fitz
        d = fitz.open(str(pdf))
        n = len(d)
        d.close()
        return f" of {n}"
    except Exception:                                           # noqa: BLE001
        return ""


def manifest(write_csv: str | None = None):
    """Every reviewable row with the exact document and page to check it against.

    A verdict a reviewer cannot trace to a page is a verdict they cannot audit,
    so this is printed as a standing table rather than only inside the
    interactive flow — and it doubles as the check that every citation actually
    resolves, which is worth knowing BEFORE sitting down to review."""
    import pandas as pd
    import reports as R

    frames = [pd.read_parquet(p) for p in (TIER2, NONBARREN) if p.exists()]
    if not frames:
        print("  no batch on disk — run `--run` first")
        return None
    df = pd.concat(frames, ignore_index=True)
    pdf_dir = R.pdf_dir("ON", "ON_AFRI_PDF")

    rows, missing = [], 0
    for r in df.itertuples(index=False):
        pdf = pdf_dir / f"{r.report_id}.pdf"
        pg = _page_no(r.page)
        ok = pdf.exists() and pg != "?"
        if not ok:
            missing += 1
        rows.append({
            "row": len(rows) + 1,
            "report_id": r.report_id,
            "hole": r.company_hole_id or r.hole_id,
            "verdict": r.verdict,
            "confidence": r.confidence,
            "quote_verified": r.quote_verified,
            "page": pg,
            "source_pdf": str(pdf),
            "resolves": ok,
        })

    print(f"\n  {len(rows)} reviewable rows. Check each verdict "
          f"against the page named here.\n")
    print(f"  {'#':>3s} {'report':14s} {'hole':10s} {'verdict':12s} {'conf':7s} "
          f"{'qv':5s} page")
    for j, x in enumerate(rows, 1):
        print(f"  {j:>3d} {x['report_id']:14s} {str(x['hole']):10s} "
              f"{x['verdict']:12s} {str(x['confidence']):7s} "
              f"{str(x['quote_verified']):5s} {x['page']:>5s}"
              + ("" if x["resolves"] else "   <-- UNRESOLVED"))
    print(f"\n  all rows live under: {pdf_dir}")
    print(f"  {len(rows)-missing}/{len(rows)} resolve to a real file and page")
    if write_csv:
        pd.DataFrame(rows).to_csv(write_csv, index=False)
        print(f"  → {write_csv}")
    return rows


def review(sample_pct: int = 10, seed: int = 0, sample_n: int | None = None) -> None:
    """The binding human QA gate. Interactive by design."""
    import pandas as pd
    if not TIER2.exists() and not NONBARREN.exists():
        sys.exit("no batch to review — run --run first")
    frames = [pd.read_parquet(p) for p in (TIER2, NONBARREN) if p.exists()]
    df = pd.concat(frames, ignore_index=True)
    n = sample_n or max(1, round(len(df) * sample_pct / 100))
    n = min(n, len(df))
    if n >= len(df):
        # Reviewing everything: keep manifest order. `random.sample(range(N), N)`
        # is a permutation, so the old code shuffled a full review for no reason
        # and the rows stopped lining up with `--manifest`, which is the table a
        # reviewer has open beside them.
        idx = list(range(len(df)))
        how = "all rows, in manifest order"
    else:
        random.seed(seed)
        idx = sorted(random.sample(range(len(df)), n))
        how = f"random sample, seed {seed}, shown in manifest order"
    pct = 100.0 * n / max(1, len(df))
    print(f"\nQA gate: reviewing {n} of {len(df)} ({pct:.0f}%) — {how}.")
    if n < 10:
        print(f"\n  WARNING: {n} rows cannot establish 95% precision. The 10% "
              f"rule in\n  PLAN_C5 5.2 assumes a batch far bigger than "
              f"{len(df)}. Either widen the\n  batch first —\n"
              f"      python -m dd.extract_assays --run --max-reports 30\n"
              f"  — or review a fixed count:\n"
              f"      python -m dd.extract_assays --review --sample-n 20\n")
    print("You are judging the VERDICT (barren / mineralized / ambiguous),")
    print("not the prose. 'ambiguous' is the SAFE answer and is correct")
    print("whenever the excerpts genuinely do not settle it — do not mark it")
    print("wrong for being cautious.\n")
    correct = judged = 0
    import reports as R
    pdf_dir = R.pdf_dir("ON", "ON_AFRI_PDF")
    for n_done, i in enumerate(idx, 1):
        r = df.iloc[i]
        pdf = pdf_dir / f"{r['report_id']}.pdf"
        print(f"\n[{n_done}/{n}] manifest row {i+1} --- {r['report_id']} hole "
              f"{r['company_hole_id'] or r['hole_id']} ({r['year_drilled']})")
        print(f"    VERDICT   : {r['verdict']}")
        print(f"    figures   : {r['best_result']}")
        print(f"    quote     : {str(r['verdict_quote'])[:220]}")
        print(f"    confidence: {r['confidence']}   "
              f"quote_verified: {r['quote_verified']}")
        if r["quote_verified"] is True:
            print("      NOTE quote_verified means the TEXT is on that page. It "
                  "does NOT mean\n           the numbers were read correctly — "
                  "OCR'd assay grids are where\n           that breaks. Check "
                  "the figures against the page.")
        # The whole point of the gate is comparing against the source, so make
        # opening the source a copy-paste rather than a hunt.
        pg = _page_no(r["page"])
        print(f"    SOURCE    : {pdf}")
        print(f"                page {pg}{_page_of(pdf)}")
        print(f"                xdg-open '{pdf}'")
        ans = input("    verdict correct? [y/n/s=skip] ").strip().lower()
        if ans == "y":
            correct += 1
            judged += 1
        elif ans == "n":
            judged += 1
    if not judged:
        print("nothing judged; gate unchanged")
        return
    precision = correct / judged
    batch = json.loads(BATCHES.read_text()) if BATCHES.exists() else {}
    batch.setdefault("qa", {})
    batch["qa"].update({"reviewed": judged, "correct": correct,
                        "precision": round(precision, 3),
                        "passed": precision >= 0.95})
    batch["training_eligible"] = precision >= 0.95
    BATCHES.write_text(json.dumps(batch, indent=1))
    print(f"\n  precision {precision:.0%} over {judged} judged — "
          f"{'GATE PASSED' if precision >= 0.95 else 'GATE NOT PASSED'}")
    if precision < 0.95:
        print("  Tier-2 labels stay out of training (dossier veto flags may "
              "still show them, marked auto).")


def status() -> None:
    if not BATCHES.exists():
        print("no batch on disk")
        return
    print(json.dumps(json.loads(BATCHES.read_text()), indent=1))


def main():
    ap = argparse.ArgumentParser(description="C5.2 — barren confirmation")
    ap.add_argument("--plan", action="store_true", help="list candidate reports")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--review", action="store_true", help="the human QA gate")
    ap.add_argument("--sample-n", type=int, default=None,
                    help="review a fixed count instead of 10%%")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--manifest", action="store_true",
                    help="every reviewable row with its source document and page")
    ap.add_argument("--csv", help="write the manifest to a CSV")
    ap.add_argument("--max-reports", type=int, default=14)
    ap.add_argument("--min-holes", type=int, default=5)
    ap.add_argument("--limit-holes", type=int, default=None,
                    help="cap holes per report (for a quick pass)")
    ap.add_argument("--year-from", type=int, default=1985)
    ap.add_argument("--year-to", type=int, default=1999)
    args = ap.parse_args()
    C.require_lake()

    if args.plan:
        for c in candidate_reports(min_holes=args.min_holes, limit=args.max_reports,
                                   year_from=args.year_from, year_to=args.year_to):
            print(f"  {c['report_id']:14s} {c['holes']:3d} holes  "
                  f"{int(c['year'] or 0)}  {(c.get('property') or '')[:40]}")
        return
    if args.run:
        run_batch(args.max_reports, args.min_holes,
                  limit_holes=args.limit_holes,
                  year_from=args.year_from, year_to=args.year_to)
        return
    if args.review:
        review(sample_n=args.sample_n)
        return
    if args.manifest:
        manifest(args.csv)
        return
    if args.status:
        status()
        return
    ap.print_help()


if __name__ == "__main__":
    main()
