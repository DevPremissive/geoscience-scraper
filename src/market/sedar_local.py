#!/usr/bin/env python3
"""
sedar_local.py — read the SEDAR+ filing corpus that already exists on this machine.

C3.5 plans scheduled SEDAR+ pulls "reuse `mining-scraper`". Before building a
connector it was worth checking what that project had already captured, and the
answer is: **filing indexes for 1,401 issuers**, one CSV each, covering every
document SEDAR+ lists for the issuer with its type and submission date.

So this module makes no network call. It reads that corpus read-only over the
local filesystem, which means this repo cannot trip the anti-bot posture the
scraper project maintains, and C6.2 has no external dependency at all.

**What the index does and does not give you.** It gives document *type* and
*date*, never document *content* and never a headline — every news release is
literally `News release - English.pdf`. That is enough for the dated, filed
events C6.2 actually needs:

    Report of exempt distribution (45-106F1)  a private placement that CLOSED
    Final prospectus                          a public offering that completed
    Material change report                    a deal or program announcement
    Technical report (NI 43-101)              serious, disclosed exploration work
    Early warning report                      someone crossed 10% of an issuer

and it is **not** enough for treasury, burn, or drill-program status, which live
inside the PDFs. Those fields are emitted as null with `missing_because` set
rather than estimated — a treasury figure with no filing behind it would violate
the provenance-or-fail rule the rest of the lake runs on.

Usage:
    python -m market.sedar_local --stats
    python -m market.sedar_local --issuer 000047781
"""
from __future__ import annotations
import argparse, csv, re, sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

#: SEDAR+ writes dates like "Thu Aug 06 06:51:21 EDT 2026". %Z round-trips
#: EDT/EST unreliably across platforms, so the zone token is dropped and the
#: date taken as filed — a filing's calendar date is what matters here, not its
#: second.
_DATE_RE = re.compile(r"^\w{3}\s+(\w{3})\s+(\d{1,2})\s+[\d:]+\s+[A-Z]{2,5}\s+(\d{4})$")

#: Document-type classification. Ordered: first match wins, so the narrow
#: patterns must precede the broad ones.
_DOC_CLASSES = (
    ("financing_closed",  re.compile(r"report of exempt distribution", re.I)),
    ("financing_public",  re.compile(r"\bfinal\b.*prospectus", re.I)),
    ("financing_pending", re.compile(r"preliminary.*prospectus", re.I)),
    ("material_change",   re.compile(r"material change report", re.I)),
    ("technical_report",  re.compile(r"technical report \(ni 43-101\)", re.I)),
    ("qp_document",       re.compile(r"qualified person \(ni 43-101\)", re.I)),
    ("early_warning",     re.compile(r"early warning report|alternative monthly report", re.I)),
    ("financials_annual", re.compile(r"audited annual financial statements", re.I)),
    ("financials_interim",re.compile(r"interim financial statements", re.I)),
    ("mda_annual",        re.compile(r"annual md&a", re.I)),
    ("mda_interim",       re.compile(r"interim md&a", re.I)),
    ("news",              re.compile(r"news release", re.I)),
)

_HEADER = "Profile(s)"


def _parse_date(s: str):
    m = _DATE_RE.match((s or "").strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)),
                       dt.datetime.strptime(m.group(1), "%b").month,
                       int(m.group(2)))
    except ValueError:
        return None


def classify(document: str) -> str:
    for name, rx in _DOC_CLASSES:
        if rx.search(document or ""):
            return name
    return "other"


def _read_one(path: Path):
    """Return (issuer_id, profile_name, [filing rows]) from one index CSV."""
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.reader(fh))
    issuer_id = profile = ""
    head_at = None
    for i, r in enumerate(rows[:12]):
        if r and r[0].strip() == _HEADER:
            head_at = i
            break
        if len(r) > 1:
            v = r[1].strip()
            if v.isdigit() and len(v) >= 6 and not issuer_id:
                issuer_id = v
            elif "/" in v and not profile:
                profile = v
    if head_at is None or not issuer_id:
        return None
    out = []
    for r in rows[head_at + 1:]:
        if len(r) < 5:
            continue
        d = _parse_date(r[2])
        if d is None:
            continue
        out.append({"issuer_id": issuer_id, "document": r[1].strip(),
                    "doc_class": classify(r[1]), "filed": d,
                    "jurisdiction": r[3].strip()})
    return issuer_id, profile, out


def _split_aliases(profile: str):
    """SEDAR+ profile names carry former names inline.

    `1844 Resources Inc., formerly, Gespeg Resources Ltd. / 1844 Resources ...`
    The left and right halves are the English/French profile; the `formerly,`
    clause is a real former name and therefore a real alias, which is exactly
    what an ownership register full of stale holder names needs.
    """
    half = profile.split(" / ")[0].strip()
    parts = [p.strip(" ,") for p in re.split(r",?\s*formerly,?\s*", half) if p.strip(" ,")]
    return parts[0] if parts else "", parts[1:]


def load_corpus():
    """Every issuer index on disk → (issuers DataFrame, filings DataFrame)."""
    import pandas as pd
    d = C.SEDAR_INDEX_DIR
    if not d.is_dir():
        sys.exit(f"SEDAR corpus not found at {d} — set CANADA_GEO_SEDAR_CORPUS")
    issuers, filings, skipped = {}, [], []
    for f in sorted(d.glob("sedarplus_*.csv")):
        got = _read_one(f)
        if not got:
            skipped.append(f.name)
            continue
        iid, profile, rows = got
        name, aliases = _split_aliases(profile)
        prev = issuers.get(iid)
        # The corpus holds repeat captures of some issuers; keep the newest.
        if prev is None or len(rows) > prev["n_filings"]:
            issuers[iid] = {"issuer_id": iid, "name": name, "aliases": aliases,
                            "n_filings": len(rows), "index_csv": str(f),
                            "captured": dt.date.fromtimestamp(f.stat().st_mtime)}
            filings = [r for r in filings if r["issuer_id"] != iid] + rows
    idf = pd.DataFrame(issuers.values())
    fdf = pd.DataFrame(filings)
    if not fdf.empty:
        fdf["filed"] = pd.to_datetime(fdf["filed"])
    return idf, fdf, skipped


def activity(fdf, as_of: dt.date | None = None, months: int = 24):
    """Per-issuer filed-event counts over a window. Every count is of a filing
    that exists, is dated, and can be pulled by anyone who wants to check."""
    import pandas as pd
    as_of = as_of or dt.date.today()
    cut = pd.Timestamp(as_of) - pd.DateOffset(months=months)
    w = fdf[fdf["filed"] >= cut]
    piv = (w.pivot_table(index="issuer_id", columns="doc_class", values="document",
                         aggfunc="count").fillna(0).astype(int))
    piv.columns = [f"n_{c}_{months}mo" for c in piv.columns]
    last = fdf.groupby("issuer_id")["filed"].max().rename("last_filing")
    first = fdf.groupby("issuer_id")["filed"].min().rename("first_filing")
    tot = fdf.groupby("issuer_id").size().rename("n_filings_total")
    out = piv.join([last, first, tot], how="outer").fillna(0)
    out["news_cadence_per_quarter"] = (
        out.get(f"n_news_{months}mo", 0) / (months / 3.0)).round(2)
    return out.reset_index()


def latest_financials(fdf):
    """Most recent annual/interim financial statement per issuer — the document
    a treasury figure would have to come from, recorded now so 6.2's null
    treasury field says exactly which filing would fill it."""
    fin = fdf[fdf["doc_class"].isin(["financials_annual", "financials_interim"])]
    if fin.empty:
        return fin.assign(treasury_source_filing=None)
    ix = fin.groupby("issuer_id")["filed"].idxmax()
    out = fin.loc[ix, ["issuer_id", "document", "filed"]].rename(
        columns={"document": "treasury_source_doc", "filed": "treasury_source_date"})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--issuer")
    a = ap.parse_args()
    idf, fdf, skipped = load_corpus()
    if a.issuer:
        import pandas as pd
        pd.set_option("display.width", 140)
        r = idf[idf.issuer_id == a.issuer]
        print(r.to_string(index=False))
        f = fdf[fdf.issuer_id == a.issuer].sort_values("filed", ascending=False)
        print(f"\n{len(f):,} filings")
        print(f["doc_class"].value_counts().to_string())
        print("\nmost recent:"); print(f.head(8).to_string(index=False))
        return
    print(f"  {len(idf):,} issuers, {len(fdf):,} filings "
          f"({fdf.filed.min().date()} → {fdf.filed.max().date()})")
    print(f"  {len(skipped)} index file(s) unparseable: {skipped[:3]}")
    print("\n  document classes:")
    for k, v in fdf["doc_class"].value_counts().items():
        print(f"    {k:<20}{v:>8,}")
    act = activity(fdf)
    print(f"\n  issuers with a closed financing in 24mo: "
          f"{int((act.get('n_financing_closed_24mo', 0) > 0).sum()):,}")
    print(f"  issuers with a material change report in 24mo: "
          f"{int((act.get('n_material_change_24mo', 0) > 0).sum()):,}")


if __name__ == "__main__":
    main()
