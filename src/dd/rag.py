#!/usr/bin/env python3
"""
rag.py — the fixed due-diligence question set, answered with citations (C5.1).

PLAN_C5 5.1 specifies six questions, run per target, "each answer with citations
`(report_id, page)`", generated "by the local chat model over retrieved chunks
with a strict 'cite or say not-found' prompt contract". C4 renders the result as
dossier section 7.

**Citations are verified structurally, not trusted.** The prompt contract asks
for `(report_id, page)` and the model obliges; that is not the same as the
citation being real. Every citation an answer emits is checked against the
chunks that were actually retrieved for that question, and any that does not
match is stripped and recorded in `unverified_citations`. This catches the two
failure modes that matter — a page number drifting by a few, and a report id
invented from context — without pretending to catch the third, which is a real
citation attached to a misread figure. That one is the human spot-check the
plan's acceptance criterion asks for, and it stays a human's job.

**The chat model is a reasoning model.** `qwen3.6-35b` on :8082 returns its
chain in `reasoning_content` and the answer in `content`; asked for 10 tokens it
spends them thinking and returns an empty answer. Budget accordingly and read
the right field.

Usage:
    python -m dd.rag --cell 892b968aac7ffff
    python -m dd.rag --cell 892b968aac7ffff --question 2
    python -m dd.rag --cell 892b968aac7ffff --ask "was the IP anomaly drilled?"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from dd import ingest as I

CHAT_URL = "http://localhost:8082/v1/chat/completions"
CHAT_MODEL = "qwen3.6-35b"

#: Thinking is OFF for this task, and that is a measured choice rather than a
#: preference. `qwen3.6-35b` is a reasoning model: over a 21k-char excerpt block
#: it spent all 2,000 completion tokens on `reasoning_content`, returned
#: `finish_reason: length` and an EMPTY `content`, and every one of the six
#: questions came back "not found" with no error to explain it. Raising the
#: budget to 6,000 fixes it — 11,406 characters of reasoning and a good answer
#: in 113 s — but `enable_thinking: false` gives an equally good answer in 8 s.
#: Extraction-with-citation is not a task that needs a chain of thought; it
#: needs the model to copy figures accurately and refuse when they are absent.
CHAT_EXTRA = {"chat_template_kwargs": {"enable_thinking": False}}
MAX_TOKENS = 1200

#: The six questions, verbatim in intent from PLAN_C5 5.1. `retrieval` is what
#: gets embedded — phrased as the language a 1990s assessment report would
#: actually use, not as the question, because the corpus is the report and not
#: an FAQ about it.
QUESTIONS = [
    {"id": 1, "key": "work_history",
     "question": "What work was done on or near this ground, when, and by whom?",
     "retrieval": "work performed survey program conducted by company year "
                  "grid established line cutting mapping sampling drilling"},
    {"id": 2, "key": "best_results", "lexical": True,
     "question": "What were the best historical results — assays, drill intercepts, "
                 "or geophysical/geochemical anomalies? Quote the figures verbatim.",
     "retrieval": "assay results gold grade g/t ppb ounces intercept intersected "
                  "over metres anomaly values best highest returned"},
    {"id": 3, "key": "hypotheses",
     "question": "What exploration hypothesis or target model was being tested, "
                 "and did it change between campaigns?",
     "retrieval": "target model exploration concept structure shear zone "
                  "mineralization controls hypothesis interpretation conclusions"},
    {"id": 4, "key": "why_stopped",
     "question": "Why did work apparently stop? Look for stated plans never "
                 "executed, funding language, or results judged not to warrant "
                 "follow-up.",
     "retrieval": "recommend further work not warranted results disappointing "
                  "option terminated agreement dropped funding no further "
                  "expenditure programme discontinued"},
    {"id": 5, "key": "untested", "lexical": True,
     "question": "What targets or recommendations were proposed but never "
                 "followed up — recommended-but-never-drilled ground in particular?",
     "retrieval": "recommendations recommended further drilling proposed test "
                  "untested target warrants follow-up should be drilled priority"},
    {"id": 6, "key": "this_ground",
     "question": "Is there any mention of the specific ground under consideration "
                 "— by claim number, grid line, or place name?",
     "retrieval": "claim number grid line location property boundary township "
                  "lot concession showing occurrence"},
]

PROMPT = """You are reading historical mineral exploration assessment reports to \
brief someone deciding whether to acquire ground. Answer ONLY from the excerpts.

RULES — these are absolute:
1. Every factual claim must carry a citation in the form (REPORT_ID, p.PAGE),
   taken from the excerpt headers below. Never cite a report or page not shown.
2. Quote figures — grades, widths, depths, dates — verbatim from the excerpt.
   Never round, convert, or infer a number.
3. If the excerpts do not answer the question, reply exactly:
   NOT FOUND IN THE RETRIEVED REPORTS
   Do not speculate, and do not fill the gap with general geological knowledge.
4. Be concise. Six sentences at most.

QUESTION: {question}

EXCERPTS:
{context}

ANSWER (with (REPORT_ID, p.PAGE) citations):"""

NOT_FOUND = "NOT FOUND IN THE RETRIEVED REPORTS"
_CITE = re.compile(r"\(\s*([A-Za-z0-9_.\-]+)\s*,\s*p\.?\s*(\d+)\s*\)", re.I)

#: A grade or an intercept has a precise lexical signature — a number against a
#: unit — and dense retrieval is bad at exactly that. Asked for "best assay
#: results", the embedding returns pages *about* assaying (how many samples,
#: which lab) and misses the one sentence reading "assays up to 5.5% Cu over
#: 10m", because that sentence sits in a chunk whose overall subject is regional
#: geology. Every question that must quote a figure therefore runs a lexical
#: pass too, and the two result sets are merged.
GRADE_PATTERN = re.compile(
    r"(?:\d[\d.,]*)\s*(?:%|g/t|gpt|g/tonne|oz/ton|oz\s*per\s*ton|ppb|ppm)"
    r"|\bassays?\s+(?:up\s+to|of|ranging)"
    r"|\bintersect(?:ed|ion)\b|\bgrading\b|\bover\s+\d[\d.]*\s*m\b",
    re.I)


# ---------------------------------------------------------------------------

def _all_chunks(tid: str) -> list[dict]:
    """Every chunk in the target's collection.

    Cheap because the corpus is per-target by design — 581 chunks for the first
    target, not a province. This is precisely what C5.1 buys over C5.3."""
    import chromadb
    client = chromadb.PersistentClient(path=str(C.CHROMA_DB))
    col = client.get_collection(I.collection_name(tid))
    got = col.get(include=["documents", "metadatas"])
    out = []
    for i, cid in enumerate(got["ids"]):
        m = got["metadatas"][i]
        out.append({"chunk_id": cid, "text": got["documents"][i],
                    "report_id": m.get("report_id"), "page": int(m.get("page") or 0),
                    "year": int(m.get("year") or 0) or None,
                    "table_dense": bool(m.get("table_dense")),
                    "info_link": m.get("info_link") or "", "distance": None})
    return out


def lexical_hits(tid: str, pattern: re.Pattern = GRADE_PATTERN,
                 limit: int = 8) -> list[dict]:
    """Chunks matching a lexical pattern, densest match first."""
    scored = []
    for c in _all_chunks(tid):
        n = len(pattern.findall(c["text"]))
        if n:
            scored.append((n, c))
    scored.sort(key=lambda t: -t[0])
    return [c for _, c in scored[:limit]]


def retrieve(tid: str, query: str, k: int = 12,
             table_only: bool = False) -> list[dict]:
    """Nearest chunks to `query` within this target's collection."""
    import chromadb

    client = chromadb.PersistentClient(path=str(C.CHROMA_DB))
    name = I.collection_name(tid)
    try:
        col = client.get_collection(name)
    except Exception as e:                                      # noqa: BLE001
        raise RuntimeError(
            f"no collection {name!r} — run `python -m dd.ingest --cell ...` "
            f"first ({type(e).__name__})")
    vec = I._embed_one(query)
    if vec is None:
        raise RuntimeError("embedding service refused the query")
    kw = {"where": {"table_dense": True}} if table_only else {}
    res = col.query(query_embeddings=[vec], n_results=k, **kw)
    out = []
    for i in range(len(res["ids"][0])):
        m = res["metadatas"][0][i]
        out.append({
            "chunk_id": res["ids"][0][i],
            "text": res["documents"][0][i],
            "report_id": m.get("report_id"),
            "page": int(m.get("page") or 0),
            "year": int(m.get("year") or 0) or None,
            "table_dense": bool(m.get("table_dense")),
            "info_link": m.get("info_link") or "",
            "distance": (res.get("distances") or [[None]])[0][i],
        })
    return out


def _chat(prompt: str, max_tokens: int = MAX_TOKENS) -> str:
    import urllib.request

    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        **CHAT_EXTRA,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(CHAT_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    ch = d["choices"][0]
    # `content`, never `reasoning_content` — the chain of thought is not an
    # answer, and an empty content with finish_reason "length" is a budget
    # failure that must not be reported as "not found".
    text = (ch["message"].get("content") or "").strip()
    if not text and ch.get("finish_reason") == "length":
        raise RuntimeError(
            "chat returned no content and stopped on length — the model spent "
            f"its {max_tokens}-token budget before answering")
    return text


def verify_citations(answer: str, chunks: list[dict]) -> tuple[str, list, list]:
    """Keep citations that point at a retrieved (report_id, page); strip the rest.

    Returns `(cleaned_answer, verified, unverified)`."""
    allowed = {(c["report_id"], c["page"]) for c in chunks}
    verified, unverified = [], []
    for m in _CITE.finditer(answer):
        rid, page = m.group(1), int(m.group(2))
        key = (rid, page)
        if key in allowed:
            if key not in verified:
                verified.append(key)
        else:
            unverified.append({"citation": m.group(0), "report_id": rid,
                               "page": page,
                               "reason": ("page not retrieved for this report"
                                          if any(r == rid for r, _ in allowed)
                                          else "report not among those retrieved")})
    cleaned = answer
    for u in unverified:
        cleaned = cleaned.replace(u["citation"], "[UNVERIFIED CITATION REMOVED]")
    return cleaned, [{"report_id": r, "page": p} for r, p in verified], unverified


_QUOTED = re.compile(r'"([^"\n]{4,120})"')


def verify_quotes(answer: str, juris: str = "ON") -> list[dict]:
    """Check each quoted string against the cited page of the source PDF.

    The citation check upstream proves a citation points at a chunk that was
    retrieved. It cannot prove the figure inside the quotation marks is the one
    on the page — and that is the failure that matters most here, because the
    whole value of section 7 is verbatim numbers a buyer can check.

    Badly-OCR'd map and table pages are where this bites: asked for survey
    parameters off a scanned plan, the model returned "50 m" and "700 m" for
    line spacings that do not appear as those strings anywhere on the page.
    Those are now flagged rather than shipped.

    This does not replace the human spot-check the acceptance criterion asks
    for. It removes the mechanical half of it so the human is judging whether a
    figure is the RIGHT one, not whether it is present."""
    import fitz
    import reports as R

    pdf_dir = R.pdf_dir(juris, R.INDEX_LAYERS[juris]["code"])
    out = []
    for m in _QUOTED.finditer(answer or ""):
        quote = m.group(1)
        tail = answer[m.end():m.end() + 240]
        cite = _CITE.search(tail)
        if not cite:
            continue
        rid, page = cite.group(1), int(cite.group(2))
        pdf = pdf_dir / f"{rid}.pdf"
        rec = {"quote": quote, "report_id": rid, "page": page}
        if not pdf.exists():
            rec["verified"] = None
            rec["reason"] = "source PDF not on disk"
            out.append(rec)
            continue
        try:
            doc = fitz.open(str(pdf))
            text = doc[page - 1].get_text() if 0 < page <= len(doc) else ""
            doc.close()
        except Exception as e:                                  # noqa: BLE001
            rec["verified"] = None
            rec["reason"] = f"{type(e).__name__}: {e}"
            out.append(rec)
            continue
        norm = lambda t: re.sub(r"[^a-z0-9./%+]+", " ", t.lower()).strip()
        rec["verified"] = norm(quote).rstrip(" .,") in norm(text)
        if not rec["verified"]:
            rec["reason"] = "quoted string not present on the cited page"
        out.append(rec)
    return out


def ask(tid: str, question: str, retrieval: str | None = None,
        k: int = 12, lexical: bool = False, lexical_k: int = 8) -> dict:
    chunks = retrieve(tid, retrieval or question, k=k)
    if lexical:
        seen = {c["chunk_id"] for c in chunks}
        for c in lexical_hits(tid, limit=lexical_k):
            if c["chunk_id"] not in seen:
                chunks.append(c)
                seen.add(c["chunk_id"])
    if not chunks:
        return {"question": question, "answer": NOT_FOUND, "citations": [],
                "unverified_citations": [], "chunks_used": []}
    context = "\n\n".join(
        f"[{c['report_id']}, p.{c['page']}"
        + (f", {c['year']}" if c["year"] else "")
        + (", TABLE" if c["table_dense"] else "")
        + f"]\n{c['text']}"
        for c in chunks)
    raw = _chat(PROMPT.format(question=question, context=context))
    if not raw:
        raw = NOT_FOUND
    cleaned, verified, unverified = verify_citations(raw, chunks)
    quotes = verify_quotes(cleaned)
    return {
        "question": question,
        "answer": cleaned,
        "found": NOT_FOUND not in cleaned,
        "citations": verified,
        "unverified_citations": unverified,
        "quote_checks": quotes,
        "quotes_verified": sum(1 for q in quotes if q.get("verified")),
        "quotes_unverified": sum(1 for q in quotes if q.get("verified") is False),
        "chunks_used": [{"chunk_id": c["chunk_id"], "report_id": c["report_id"],
                         "page": c["page"]} for c in chunks],
    }


def auto_k(chunks: int) -> int:
    """Retrieval width scaled to corpus size.

    A fixed k silently degrades as the corpus grows. When filename resolution
    took this target from 7 reports to 12 — 581 chunks to 2,141 — the SAME k=12
    dropped the run from 5/6 answered with 21 citations to 4/6 with 15, and
    quote verification from 17/20 to 7/11: the top twelve now spread across
    twelve reports instead of seven, and the relevant pages were crowded out by
    merely-adjacent ones. At k=24 it recovers to 5/6, 19 citations, 19/20 quotes.

    A bigger corpus is not automatically a better answer. It is a better answer
    only if you go looking through more of it."""
    return max(12, min(32, round(chunks / 90)))


def run_all(cell_id: str, juris: str = "ON", k: int | None = None,
            quiet: bool = False) -> dict:
    """The full six-question set, stored so a dossier regeneration is pinned."""
    tid = I.target_id(cell_id, juris)
    man = I.stats(cell_id, juris)
    if man is None:
        sys.exit(f"no ingested corpus for {tid} — run `python -m dd.ingest "
                 f"--cell {cell_id}` first")
    if k is None:
        k = auto_k(man.get("chunks_embedded") or 0)
        if not quiet:
            print(f"  corpus {man.get('chunks_embedded')} chunks → k={k}")

    answers = []
    for q in QUESTIONS:
        if not quiet:
            print(f"  [{q['id']}/6] {q['key']} …", flush=True)
        try:
            a = ask(tid, q["question"], q["retrieval"], k=k,
                    lexical=q.get("lexical", False))
        except Exception as e:                                  # noqa: BLE001
            a = {"question": q["question"], "answer": None,
                 "error": f"{type(e).__name__}: {e}", "found": False,
                 "citations": [], "unverified_citations": [], "chunks_used": []}
        a["id"], a["key"] = q["id"], q["key"]
        answers.append(a)
        if not quiet:
            n = len(a.get("citations") or [])
            u = len(a.get("unverified_citations") or [])
            qb = a.get("quotes_unverified", 0)
            state = "not found" if not a.get("found") else f"{n} cited"
            print(f"        {state}"
                  + (f", {u} unverified stripped" if u else "")
                  + (f", {qb} quote(s) NOT on the cited page" if qb else ""))

    out = {
        "target_id": tid, "cell_id": cell_id, "juris": juris,
        "collection": I.collection_name(tid),
        "corpus": {"reports": len(man.get("reports") or []),
                   "pages": man.get("pages"),
                   "chunks": man.get("chunks_embedded")},
        "coverage": man.get("coverage"),
        "model": CHAT_MODEL, "retrieval_k": k,
        "answers": answers,
    }
    path = I.DD_DIR / f"{tid}.answers.json"
    path.write_text(json.dumps(out, indent=1))
    if not quiet:
        found = sum(1 for a in answers if a.get("found"))
        cites = sum(len(a.get("citations") or []) for a in answers)
        unver = sum(len(a.get("unverified_citations") or []) for a in answers)
        qok = sum(a.get("quotes_verified", 0) for a in answers)
        qbad = sum(a.get("quotes_unverified", 0) for a in answers)
        print(f"\n  {found}/6 answered, {cites} verified citations, "
              f"{unver} unverified stripped")
        print(f"  quoted figures: {qok} found verbatim on the cited page, "
              f"{qbad} NOT found")
        print(f"  → {path}")
    return out


def main():
    ap = argparse.ArgumentParser(description="C5.1 — due-diligence question set")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--juris", default="ON")
    ap.add_argument("-k", type=int, default=None,
                    help="chunks retrieved per question (default: scaled to corpus)")
    ap.add_argument("--question", type=int, help="run one question (1-6)")
    ap.add_argument("--ask", help="free-form question against this target")
    ap.add_argument("--show", action="store_true", help="print stored answers")
    args = ap.parse_args()
    C.require_lake()
    tid = I.target_id(args.cell, args.juris)

    if args.show:
        p = I.DD_DIR / f"{tid}.answers.json"
        if not p.exists():
            sys.exit("no stored answers for this target")
        d = json.loads(p.read_text())
        for a in d["answers"]:
            print(f"\n--- {a['id']}. {a['key']} ---\n{a.get('answer')}")
            if a.get("citations"):
                print("    cited: " + ", ".join(
                    f"{c['report_id']} p.{c['page']}" for c in a["citations"]))
        return
    if args.ask:
        man = I.stats(args.cell, args.juris) or {}
        a = ask(tid, args.ask, k=args.k or auto_k(man.get("chunks_embedded") or 0))
        print(a["answer"])
        if a["citations"]:
            print("\ncited: " + ", ".join(
                f"{c['report_id']} p.{c['page']}" for c in a["citations"]))
        return
    if args.question:
        q = next((x for x in QUESTIONS if x["id"] == args.question), None)
        if not q:
            sys.exit("question must be 1-6")
        man = I.stats(args.cell, args.juris) or {}
        a = ask(tid, q["question"], q["retrieval"],
                k=args.k or auto_k(man.get("chunks_embedded") or 0),
                lexical=q.get("lexical", False))
        print(f"{q['question']}\n\n{a['answer']}")
        return
    run_all(args.cell, args.juris, k=args.k)


if __name__ == "__main__":
    main()
