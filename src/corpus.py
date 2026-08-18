#!/usr/bin/env python3
"""
corpus.py — per-cell geological text and its embeddings (C2.1).

Assembles a document per r7 cell from map-unit description text, then embeds it
with the local mxbai server for the text→prospectivity pathway.

**What the corpus is actually made of.** PLAN_C2 2.1 lists CGMC legend
descriptions and "MRDS free-text fields (304,632 records)" as pillars. Both were
checked and neither holds:

  * the CGMC "legend GPKG" is a QGIS style file whose 34 entries are class
    NAMES averaging 12 characters, not descriptions (audit I6);
  * MRDS is 87% United States. It holds **1,551 Canadian records**, with
    `dep_type` populated on 125 of them and `ore_ctrl` on 89.

What does exist is provincial bedrock attribute text, measured per layer:
BC `unit_desc` (~137 chars/feature), ON `ROCKTYPE_P` (~84), QC `NOM_ETQT_LITH`
(~62). That is the corpus — thinner than the plan assumed, and real.

**Chunking is not optional and not a fixed size.** The server's limit is ~512
TOKENS, so the safe character count depends on vocabulary: prose and
comma-separated lithology lists tokenize differently. Measured here, 2,000
characters embed and 2,600 return HTTP 500. So chunks are conservative AND the
embedder halves a chunk that is rejected, rather than assuming a constant.

Embeddings are written WIDE (`embeddings.parquet`), never to the long feature
store — 1,024 dims × 1.76 M cells would be 1.8 billion long rows for one feature
family (Master §4 dense-embedding exception).

Usage:
    python src/corpus.py --build ON
    python src/corpus.py --embed ON
    python src/corpus.py --stats
"""
from __future__ import annotations
import argparse, json, re, sys, time
import datetime as dt
from pathlib import Path

import config as C

CORPUS_DIR = C.PROCESSED_DIR / "corpus"
EMBED_URL = "http://localhost:8083/embedding"

#: Conservative chunk size in characters. The true limit is ~512 tokens; this
#: sits well under the measured 2,600-character failure point, and the embedder
#: halves anything the server still rejects.
CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 150

#: Text fields per source layer, in the order they should appear in a cell
#: document. Chosen by measuring average populated length per column, not by
#: guessing from the field name.
TEXT_SOURCES = {
    "ON": [
        ("ON__ON_GEOL_BEDROCK__Geopoly", ["ROCKTYPE_P", "UNITNAME_P", "ERA_P"]),
        ("ON__ON_GEOL_SURFICIAL__sgu_poly_PAL", ["GEOLOGIC_DEPOSIT", "MATERIAL_DESCRIP"]),
    ],
    "BC": [("BC__BC_GEOL", ["unit_desc", "strat_unit"])],
    "QC": [("QC__QC_SIGEOM_BEDROCK__F3E04_ZONE_GEOLOGIQUE",
            ["NOM_ETQT_LITH_ANGLA", "NOM_ETQT_LITH"])],
}

#: SIGÉOM embeds font markup in its label fields — `<FNT name='SIGEOM2010...'>`.
#: Embedding presentation markup would spend the model's 512 tokens on font
#: names instead of geology.
_MARKUP = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean(text) -> str:
    if text is None:
        return ""
    s = _MARKUP.sub(" ", str(text))
    s = s.replace("\x00", " ")
    return _WS.sub(" ", s).strip()


def build(juris: str = "ON", write: bool = True):
    """One text document per r7 cell, with the sources that contributed."""
    import geopandas as gpd
    import pandas as pd
    import fabric as F
    import gridify as G

    cells = F.load_r7(juris)
    meta = json.loads((F.FABRIC_DIR / f"r7_{juris}.json").read_text())
    print(f"  fabric {meta['fabric_version']}: {len(cells):,} cells")

    docs: dict = {}
    sources: dict = {}
    for layer, fields in TEXT_SOURCES.get(juris, []):
        try:
            g = gpd.read_file(C.GPKG_PATH, layer=layer,
                              columns=[f for f in fields])
        except Exception as e:                                  # noqa: BLE001
            print(f"   ! {layer}: {str(e)[:70]}")
            continue
        present = [f for f in fields if f in g.columns]
        if not present:
            print(f"   ! {layer}: none of {fields} present")
            continue
        g["_text"] = g[present].apply(
            lambda r: " ".join(clean(v) for v in r if clean(v)), axis=1)
        g = g[g["_text"].str.len() > 0]
        if g.empty:
            print(f"   ! {layer}: no usable text")
            continue
        out = G.gridify_vector(g[["_text", "geometry"]], cells, "text_concat",
                               field="_text")
        n = 0
        for r in out.itertuples(index=False):
            t = clean(r.text)
            if not t:
                continue
            docs.setdefault(r.cell_id, []).append(t)
            sources.setdefault(r.cell_id, []).append(layer.split("__")[-1])
            n += 1
        print(f"   {layer.split('__')[-1]:<34}{len(g):>8,} features → {n:>7,} cells")

    rows = []
    for cid, parts in docs.items():
        text = " ".join(parts)
        rows.append({"cell_id": cid, "text": text, "n_chars": len(text),
                     "sources": json.dumps(sorted(set(sources[cid])))})
    df = pd.DataFrame(rows)
    if df.empty:
        sys.exit("no corpus text produced")
    print(f"\n  {len(df):,} cells with text "
          f"({100*len(df)/len(cells):.1f}% of the fabric)")
    print(f"  document length: median {df['n_chars'].median():.0f} "
          f"mean {df['n_chars'].mean():.0f} max {df['n_chars'].max():,} chars")
    over = int((df["n_chars"] > CHUNK_CHARS).sum())
    print(f"  {over:,} documents exceed one chunk ({100*over/len(df):.0f}%) — "
          f"chunking is load-bearing, not a nicety")
    if write:
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        p = CORPUS_DIR / f"{juris}_r7_docs.parquet"
        df.to_parquet(p, index=False, compression="zstd")
        print(f"  → {p}")
    return df


def chunk(text: str, size: int = CHUNK_CHARS):
    """Split on sentence-ish boundaries, never mid-token if avoidable."""
    text = text.strip()
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for piece in re.split(r"(?<=[.;])|(?<=\]) ", text):
        if len(cur) + len(piece) + 1 > size and cur:
            out.append(cur.strip())
            cur = ""
        cur += piece + " "
        while len(cur) > size:                 # a single huge piece
            out.append(cur[:size].strip())
            cur = cur[size:]
    if cur.strip():
        out.append(cur.strip())
    return [c for c in out if c]


def _embed_one(text: str, retries: int = 4):
    """Embed one chunk, halving it if the server rejects the length.

    The 512-token limit maps to a variable character count, so a fixed chunk
    size cannot be safe for every vocabulary. Rather than guess low and waste
    context on every document, back off only when actually refused.
    """
    import json as _json
    import numpy as np
    import urllib.request
    cur = text
    for _ in range(retries):
        try:
            req = urllib.request.Request(
                EMBED_URL, data=_json.dumps({"content": cur}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = _json.loads(r.read())
            v = d[0]["embedding"][0] if isinstance(d, list) else d["embedding"]
            return np.asarray(v, dtype="float32")
        except Exception:                                       # noqa: BLE001
            if len(cur) <= MIN_CHUNK_CHARS:
                return None
            cur = cur[: max(MIN_CHUNK_CHARS, len(cur) // 2)]
    return None


def embed(juris: str = "ON", limit: int | None = None, write: bool = True):
    """Chunk, embed and mean-pool each cell document into a 1,024-d vector."""
    import numpy as np
    import pandas as pd
    import fabric as F

    p = CORPUS_DIR / f"{juris}_r7_docs.parquet"
    if not p.exists():
        sys.exit(f"no corpus for {juris} — run --build first")
    df = pd.read_parquet(p)
    if limit:
        df = df.head(limit)
    meta = json.loads((F.FABRIC_DIR / f"r7_{juris}.json").read_text())

    t0 = time.time()
    vecs, ids, failed, n_chunks = [], [], 0, 0
    for i, r in enumerate(df.itertuples(index=False), 1):
        parts = chunk(r.text)
        n_chunks += len(parts)
        got = [v for v in (_embed_one(c) for c in parts) if v is not None]
        if not got:
            failed += 1
            continue
        # Mean-pool across chunks. Equal weight: the area-share tokens are
        # already inside the text, so weighting again would double-count.
        vecs.append(np.mean(np.vstack(got), axis=0))
        ids.append(r.cell_id)
        if i % 2000 == 0:
            el = time.time() - t0
            print(f"   {i:,}/{len(df):,} cells  {el:.0f}s  "
                  f"{i/el:.0f} cells/s")

    if not vecs:
        sys.exit("nothing embedded")
    arr = np.vstack(vecs).astype("float32")
    dim = arr.shape[1]
    print(f"\n  embedded {len(ids):,} cells, {n_chunks:,} chunks, dim {dim}")
    print(f"  {failed:,} cell(s) failed entirely")
    print(f"  {time.time()-t0:.0f}s total")

    out = pd.DataFrame(arr, columns=[f"textemb_{i:04d}" for i in range(dim)])
    out.insert(0, "cell_id", ids)
    if write:
        snap = dt.date.today().isoformat()
        d = C.PROCESSED_DIR / "features" / meta["fabric_version"] / snap
        d.mkdir(parents=True, exist_ok=True)
        fp = d / "embeddings.parquet"
        out.to_parquet(fp, index=False, compression="zstd")
        (d / "embeddings_manifest.json").write_text(json.dumps({
            "fabric_version": meta["fabric_version"], "snapshot": snap,
            "jurisdiction": juris,
            "model": "mxbai-embed-large-v1.Q4_K_M", "endpoint": EMBED_URL,
            "dimension": int(dim), "cells": len(ids), "chunks": n_chunks,
            "chunk_chars": CHUNK_CHARS, "pooling": "mean",
            "failed_cells": failed,
            "corpus": str(p),
            "note": "WIDE float32 array per Master §4 dense-embedding exception; "
                    "never written to the long-format feature store.",
        }, indent=2), encoding="utf-8")
        print(f"  → {fp}  ({fp.stat().st_size/1e6:.0f} MB)")
    return out


def stats():
    import pandas as pd
    for p in sorted(CORPUS_DIR.glob("*_docs.parquet")):
        df = pd.read_parquet(p)
        print(f"  {p.stem}: {len(df):,} cells, "
              f"median {df['n_chars'].median():.0f} chars")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", metavar="JURIS")
    ap.add_argument("--embed", metavar="JURIS")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    C.require_lake()
    if args.build:
        build(args.build)
    if args.embed:
        embed(args.embed, args.limit)
    if args.stats:
        stats()
    if not any([args.build, args.embed, args.stats]):
        ap.print_help()


if __name__ == "__main__":
    main()
