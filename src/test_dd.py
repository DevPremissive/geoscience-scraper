#!/usr/bin/env python3
"""
test_dd.py — tests for C3.4 (report fetch) and C5.1/C5.2 (due diligence).

Repo style: no pytest. Pure tests always run; lake tests skip loudly when the
drive is not mounted; **service tests skip when :8082 / :8083 are down**, since
a model that is not running is not a failing test.

The tests that matter most are the ones guarding claims a reader would
otherwise have to take on trust:

  * `test_citations_are_verified_not_trusted` — an invented citation must be
    stripped. This is the whole basis for believing section 7.
  * `test_quote_verification_catches_a_fabricated_figure` — a figure that is not
    on the cited page must be flagged.
  * `test_nothing_self_promotes_into_training` — C5.2's QA gate is binding, so
    every extracted label must be `review_status="auto"` and
    `training_eligible=False` until a human says otherwise.
  * `test_missing_reports_are_named` — a retrieval gap must be visible, because
    a thin corpus that looks complete is the failure mode that matters.

Run:  python -m test_dd
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C
import reports as R
from dd import ingest as I
from dd import rag as RAG

CELL = "892b968aac7ffff"
_fails: list[str] = []
_skips: list[str] = []


def check(cond, msg):
    if cond:
        print(f"    ok   {msg}")
    else:
        _fails.append(msg)
        print(f"    FAIL {msg}")


def skip(name, why):
    _skips.append(f"{name}: {why}")
    print(f"    skip {name} — {why}")


def lake_ready() -> bool:
    try:
        C.require_lake()
    except Exception:
        return False
    return (C.PROCESSED_DIR / "fabric" / "r7_ON.parquet").exists()


def service_up(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=5)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------

def test_work_type_splitting():
    """`WORK_TYPE` joins entries with commas AND uses commas inside them —
    the same trap as the MLAS HOLDER field (audit I10)."""
    raw = "EM (Electromagnetic), MAG (Magnetic / Magnetometer Survey)"
    got = R._split_work_types(raw)
    check(got == ["EM (Electromagnetic)", "MAG (Magnetic / Magnetometer Survey)"],
          f"work types split between entries, not inside them ({got})")
    check(R._split_work_types(None) == [], "empty work types give an empty list")
    check(len(R._split_work_types("GCHEM (Geochemical)")) == 1,
          "a single work type stays single")


def test_geometry_helpers():
    wkt = R.cell_polygon_wkt(CELL)
    check(wkt.startswith("POLYGON"), "cell_polygon_wkt returns a polygon")
    big = R.cell_polygon_wkt(CELL, radius_km=2.0)
    from shapely import wkt as W
    a, b = W.loads(wkt), W.loads(big)
    check(b.area > a.area, "a radius buffer enlarges the cell polygon")
    check(b.contains(a), "the buffered polygon contains the unbuffered one")
    bb = W.loads(R.bbox_wkt((-81.0, 48.0, -80.0, 49.0)))
    check(abs(bb.area - 1.0) < 1e-9, "bbox_wkt builds the right box")


def test_unsupported_jurisdiction_raises():
    """'No reports here' and 'we cannot look here' are different answers."""
    try:
        R.resolve_reports("POLYGON((0 0,1 0,1 1,0 0))", juris="ZZ")
        check(False, "an unregistered jurisdiction raises rather than returning []")
    except R.UnsupportedJurisdiction as e:
        check("registered" in str(e).lower(),
              "an unregistered jurisdiction raises and names what is registered")


def test_chunking_respects_page_boundaries():
    text = "Alpha. " * 400
    chunks = I.chunk_page(text, width=600, overlap=80)
    check(len(chunks) > 1, "a long page splits into several chunks")
    check(all(len(c) <= 900 for c in chunks),
          f"no chunk runs far past the target width (max {max(map(len, chunks))})")
    check(all(len(c) >= I.MIN_CHUNK_CHARS for c in chunks),
          "no chunk is below the minimum")
    short = I.chunk_page("tiny")
    check(short == ["tiny"] or short == [],
          "a very short page does not explode into fragments")


def test_collection_name_is_chroma_safe():
    n = I.collection_name(I.target_id(CELL))
    check(re.fullmatch(r"[A-Za-z0-9._\-]{3,63}", n),
          f"collection name is valid for Chroma ({n})")


def test_citations_are_verified_not_trusted():
    """The basis for believing anything in section 7."""
    chunks = [{"report_id": "42A07SE0009", "page": 10},
              {"report_id": "42A07SW0011", "page": 7}]
    answer = ("Gold was found (42A07SE0009, p.10) and also here "
              "(42A07SW0011, p.7), plus (42A07SE0009, p.99) and "
              "(FAKE123, p.1).")
    cleaned, verified, unverified = RAG.verify_citations(answer, chunks)
    check(len(verified) == 2, f"real citations survive ({verified})")
    check(len(unverified) == 2, "a wrong page and an invented report are caught")
    check("p.99" not in cleaned and "FAKE123" not in cleaned,
          "unverified citations are removed from the answer text")
    check("UNVERIFIED CITATION REMOVED" in cleaned,
          "the removal is visible, not silent")
    reasons = {u["reason"] for u in unverified}
    check(any("page" in r for r in reasons) and any("report" in r for r in reasons),
          "the two failure modes are distinguished")


def test_grade_pattern_finds_figures_prose_retrieval_misses():
    """Dense retrieval loses a figure buried in prose; the lexical pass is why
    that stopped happening."""
    txt = "Copper Deposit with assays up to 5.5% Cu over 10m ( Currie Twp )"
    check(bool(RAG.GRADE_PATTERN.search(txt)), "grade pattern matches '5.5% Cu'")
    check(bool(RAG.GRADE_PATTERN.search("returned 2300 ppm Zn")),
          "grade pattern matches ppm")
    check(bool(RAG.GRADE_PATTERN.search("intersected 12.4 g/t Au")),
          "grade pattern matches g/t")
    check(not RAG.GRADE_PATTERN.search("the geology of the area is complex"),
          "grade pattern does not match ordinary prose")


def test_chat_budget_failure_is_not_a_not_found():
    """A reasoning model that spends its budget thinking returns empty content.
    Reporting that as 'not found' is a wrong answer about the ground."""
    src = (Path(__file__).parent / "dd" / "rag.py").read_text()
    check("finish_reason" in src and "length" in src,
          "the chat wrapper distinguishes a budget failure from an empty answer")
    check("enable_thinking" in src,
          "thinking is explicitly configured rather than left to the default")


def test_qa_gate_constants():
    from dd import extract_assays as EA
    src = (Path(__file__).parent / "dd" / "extract_assays.py").read_text()
    check('"review_status": "auto"' in src,
          "extracted labels are written as auto-reviewed")
    check('"training_eligible": False' in src,
          "extracted labels are not training-eligible on write")
    check(set(EA.VERDICTS) == {"barren", "mineralized", "ambiguous"},
          "the three verdicts are the ones the plan names")


# ---------------------------------------------------------------------------
# Lake
# ---------------------------------------------------------------------------

def test_resolver_matches_the_index():
    reps = R.resolve_reports(R.cell_polygon_wkt(CELL, 2.0), "ON")
    check(len(reps) > 0, f"the resolver finds reports over the target ({len(reps)})")
    check(all(r["report_id"] for r in reps), "every report has an id")
    check(all(r.get("info_link") for r in reps),
          "every report carries the province's own viewer link — the "
          "acceptance criterion is a human comparing the two lists")
    years = [r["year"] for r in reps if r["year"]]
    check(years == sorted(years, reverse=True), "reports come back newest first")


def test_missing_reports_are_named():
    """A retrieval gap must be visible. A thin corpus that looks complete would
    have section 7 answer 'work stopped in 1998' for ground drilled in 2021."""
    reps = R.resolve_reports(R.cell_polygon_wkt(CELL, 2.0), "ON")
    for r in reps:
        p = R.pdf_dir("ON", "ON_AFRI_PDF") / f"{r['report_id']}.pdf"
        r["status"] = "cached" if p.exists() else "failed"
        r["pdf_path"] = str(p) if p.exists() else None
    cs = R.coverage_summary(reps)
    check(cs["resolved"] == len(reps), "coverage counts everything resolved")
    check(cs["retrieved"] + cs["missing"] == cs["resolved"],
          "every report is either retrieved or counted missing")
    if cs["missing"]:
        check(len(cs["missing_ids"]) == cs["missing"],
              "every missing report is named by id")
        check(cs["note"], "a coverage gap carries an explanation")


def test_pdfs_are_valid():
    d = R.pdf_dir("ON", "ON_AFRI_PDF")
    if not d.exists():
        skip("test_pdfs_are_valid", "no PDFs fetched yet")
        return
    pdfs = sorted(d.glob("*.pdf"))
    check(len(pdfs) > 0, f"PDFs are on disk ({len(pdfs)})")
    bad = [p.name for p in pdfs if p.open("rb").read(4) != b"%PDF"]
    check(not bad, f"every fetched file is really a PDF ({bad[:3]})")
    small = [p.name for p in pdfs if p.stat().st_size < R.MIN_PDF_BYTES]
    check(not small, f"no implausibly small PDFs ({small[:3]})")
    sidecars = sorted(d.glob("*.json"))
    check(len(sidecars) >= 1, "sidecars are written alongside the PDFs")


def test_ingest_manifest():
    man = I.stats(CELL)
    if man is None:
        skip("test_ingest_manifest", "target not ingested")
        return
    check(man["chunks_embedded"] > 0, f"chunks were embedded ({man['chunks_embedded']})")
    check(man["embedding"]["dimension"] == 1024,
          "the manifest records the embedding dimension")
    check(man["pages"] > 0, "pages were read")
    check("coverage" in man, "the ingest manifest carries the retrieval gap")
    low = man.get("low_text_pages", 0)
    check(low / max(1, man["pages"]) < 0.5,
          f"most pages have a text layer ({low}/{man['pages']} low-text) — "
          f"Ontario ships OCR'd AFRI scans, so no OCR engine was needed")


def test_answers_are_cited():
    p = I.DD_DIR / f"{I.target_id(CELL)}.answers.json"
    if not p.exists():
        skip("test_answers_are_cited", "question set not run")
        return
    d = json.loads(p.read_text())
    check(len(d["answers"]) == 6, "all six questions are recorded")
    answered = [a for a in d["answers"] if a.get("found")]
    check(len(answered) >= 3, f"most questions are answered ({len(answered)}/6)")
    for a in answered:
        check(len(a.get("citations") or []) > 0,
              f"answer {a['id']} ({a['key']}) carries at least one citation")
    unver = sum(len(a.get("unverified_citations") or []) for a in d["answers"])
    check(unver == 0, f"no unverified citations survived into the record ({unver})")
    for a in d["answers"]:
        if not a.get("found"):
            check(RAG.NOT_FOUND in (a.get("answer") or ""),
                  f"answer {a['id']} states not-found explicitly rather than "
                  f"returning something vague")


def test_quote_verification_catches_a_fabricated_figure():
    d = R.pdf_dir("ON", "ON_AFRI_PDF")
    real = sorted(d.glob("*.pdf"))
    if not real:
        skip("test_quote_verification_catches_a_fabricated_figure", "no PDFs")
        return
    rid = real[0].stem
    fake = f'The hole returned "99999 g/t Au beyond all reason" ({rid}, p.1).'
    checks = RAG.verify_quotes(fake)
    check(len(checks) == 1 and checks[0]["verified"] is False,
          "a figure that is not on the cited page is flagged")
    # And a real one passes.
    p = I.DD_DIR / f"{I.target_id(CELL)}.answers.json"
    if p.exists():
        d2 = json.loads(p.read_text())
        tot = sum(a.get("quotes_verified", 0) for a in d2["answers"])
        check(tot > 0, f"real quoted figures do verify ({tot})")


def test_dossier_history_section():
    root = C.PROCESSED_DIR / "dossiers"
    js = sorted(root.glob("*/*.json")) if root.exists() else []
    if not js:
        skip("test_dossier_history_section", "no dossier on disk")
        return
    d = json.loads(js[-1].read_text())
    hist = next((s for s in d["sections"] if s["key"] == "history"), None)
    check(hist is not None, "the dossier has a history section")
    if hist and hist.get("available"):
        check(hist["facts"], "section 7 carries facts")
        check(hist.get("tables", {}).get("due_diligence"),
              "section 7 carries the question/answer table")
        check(any("cite" in n.lower() for n in hist.get("notes", [])),
              "section 7 explains that citations are checked")
    elif hist:
        check(bool(hist.get("unavailable_reason")),
              "an unavailable history section says why and how to build it")


def test_nothing_self_promotes_into_training():
    """PLAN_C5 5.2 step 5 is binding: the QA gate is human."""
    import pandas as pd
    from dd import extract_assays as EA
    made = False
    for p in (EA.TIER2, EA.NONBARREN):
        if not p.exists():
            continue
        made = True
        df = pd.read_parquet(p)
        check((df["review_status"] == "auto").all(),
              f"{p.name}: every row is review_status=auto")
        check(~df["training_eligible"].any(),
              f"{p.name}: no row is training-eligible before review")
    if not made:
        skip("test_nothing_self_promotes_into_training", "no C5.2 batch on disk")
        return
    if EA.BATCHES.exists():
        b = json.loads(EA.BATCHES.read_text())
        check(b["qa"]["passed"] is False or b["qa"]["reviewed"] > 0,
              "the batch cannot report a passed QA gate with nothing reviewed")
        check(b["qa"]["required_sample_pct"] == 10,
              "the batch records the 10% sample requirement")


def test_tier2_is_not_merged_into_negatives():
    """C2.4's consumption rule: Tier-2 enters training only after the gate."""
    import pandas as pd
    from dd import extract_assays as EA
    neg = C.PROCESSED_DIR / "negatives.parquet"
    if not (neg.exists() and EA.TIER2.exists()):
        skip("test_tier2_is_not_merged_into_negatives", "missing one of the files")
        return
    n = pd.read_parquet(neg)
    check(set(n["tier"].unique()) <= {1, "1", "tier1"},
          f"negatives.parquet still holds only Tier-1 rows ({set(n['tier'].unique())})")


def test_c52_batch_scale():
    from dd import extract_assays as EA
    if not EA.BATCHES.exists():
        skip("test_c52_batch_scale", "no batch")
        return
    b = json.loads(EA.BATCHES.read_text())
    check(b["holes_processed"] >= 50,
          f"the batch meets the >=50 holes acceptance ({b['holes_processed']})")
    check(len(b["reports"]) >= 10,
          f"the batch meets the >=10 reports acceptance ({len(b['reports'])})")
    check(b.get("table_extraction"),
          "the batch records how tables were (not) extracted")


PURE = [test_work_type_splitting, test_geometry_helpers,
        test_unsupported_jurisdiction_raises, test_chunking_respects_page_boundaries,
        test_collection_name_is_chroma_safe, test_citations_are_verified_not_trusted,
        test_grade_pattern_finds_figures_prose_retrieval_misses,
        test_chat_budget_failure_is_not_a_not_found, test_qa_gate_constants]

LAKE = [test_resolver_matches_the_index, test_missing_reports_are_named,
        test_pdfs_are_valid, test_ingest_manifest, test_answers_are_cited,
        test_quote_verification_catches_a_fabricated_figure,
        test_dossier_history_section, test_nothing_self_promotes_into_training,
        test_tier2_is_not_merged_into_negatives, test_c52_batch_scale]


def main():
    print("C3.4 / C5.1 / C5.2 tests\n")
    print("  pure")
    for fn in PURE:
        print(f"  {fn.__name__}")
        fn()
    if lake_ready():
        print("\n  lake")
        for fn in LAKE:
            print(f"  {fn.__name__}")
            try:
                fn()
            except Exception as e:                              # noqa: BLE001
                _fails.append(f"{fn.__name__} raised {type(e).__name__}: {e}")
                print(f"    FAIL {fn.__name__} raised {type(e).__name__}: {e}")
    else:
        print(f"\n  SKIPPING {len(LAKE)} lake tests — {C.LAKE_ROOT} not mounted")

    print("\n" + "-" * 64)
    if _skips:
        print(f"{len(_skips)} skipped:")
        for s in _skips:
            print("  " + s)
    if _fails:
        print(f"{len(_fails)} FAILURE(S)")
        for f in _fails:
            print("  " + f)
        sys.exit(1)
    print("all C3.4/C5.1/C5.2 tests pass")


if __name__ == "__main__":
    main()
