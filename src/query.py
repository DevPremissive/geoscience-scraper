#!/usr/bin/env python3
"""
query.py — semantic search over the geoscience RAG vector store.

Usage:
    python src/query.py "gold drill holes over 5 g/t"
    python src/query.py "copper occurrences in BC" --limit 20
    python src/query.py "NTS 093A assessment reports" --filter '{"jurisdiction": "BC"}'
"""
from __future__ import annotations
import argparse, json

from vector_store import VectorStore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="Natural-language search query")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--filter", default=None, help="Metadata filter as JSON (e.g. '{\"jurisdiction\": \"ON\"}')")
    args = ap.parse_args()
    filters = json.loads(args.filter) if args.filter else None

    store = VectorStore(collection_name="geo_canada")
    results = store.search(args.query, filters=filters, limit=args.limit)

    print(f"\n{'='*60}")
    print(f"Query: {args.query}")
    if filters:
        print(f"Filter: {filters}")
    print(f"{'='*60}")
    for i, r in enumerate(results, 1):
        score = 1 - r["score"]
        print(f"\n--- Result {i} (score: {score:.3f}) ---")
        print(f"  Source: {r['id'][:16]}...")
        print(f"  Text:   {r['text'][:200]}...")
    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
