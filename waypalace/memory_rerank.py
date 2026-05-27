#!/usr/bin/env python3
"""
memory_rerank.py - Stage 3 reranking layer

Uses BAAI/bge-reranker-v2-m3 to re-score search results based on
full content+query analysis (cross-encoder), much more accurate than
vector similarity alone.

Lazy-loaded: model is only loaded on first call.
Failure-safe: if rerank fails, returns original results unchanged.
"""
import os
import sys

MODEL_NAME = "BAAI/bge-reranker-v2-m3"

_reranker = None


def load_reranker():
    """Lazy-load the cross-encoder. First call downloads ~570MB."""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(MODEL_NAME, max_length=512)
        except Exception as e:
            sys.stderr.write(f"[memory_rerank] failed to load model: {e}\n")
            _reranker = False  # mark as failed, don't retry
    return _reranker if _reranker is not False else None


def rerank(query, candidates, top_k=5):
    """
    Re-rank candidates by full cross-encoder scoring.

    Args:
        query: the search query string
        candidates: list of dicts with 'text' field
        top_k: number to return after rerank

    Returns:
        Sorted list of candidates with 'rerank_score' field added.
        On failure: returns original candidates (truncated to top_k) unchanged.
    """
    if not candidates:
        return []

    if len(candidates) == 1:
        candidates[0]["rerank_score"] = candidates[0].get("similarity", 0)
        return candidates

    model = load_reranker()
    if model is None:
        # Fallback: return original order, truncated
        return candidates[:top_k]

    try:
        # CrossEncoder expects list of [query, doc] pairs
        pairs = [[query, c["text"]] for c in candidates]
        scores = model.predict(pairs, show_progress_bar=False)

        # Attach scores
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)

        # Sort by rerank score (descending)
        sorted_candidates = sorted(candidates, key=lambda c: -c["rerank_score"])
        return sorted_candidates[:top_k]
    except Exception as e:
        sys.stderr.write(f"[memory_rerank] rerank failed: {e}\n")
        # Fallback to original order
        return candidates[:top_k]


def is_available():
    """Check if reranker can be loaded without actually loading it."""
    global _reranker
    if _reranker is False:
        return False
    if _reranker is not None:
        return True
    # Try a probe import
    try:
        from sentence_transformers import CrossEncoder
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # CLI test mode
    import argparse
    parser = argparse.ArgumentParser(description="Test reranker")
    parser.add_argument("query")
    parser.add_argument("--docs", nargs="+", help="Test documents")
    args = parser.parse_args()

    if not args.docs:
        args.docs = [
            "This document is about Stripe payment processing.",
            "How to handle Stripe webhook events for refunds.",
            "General payment integration best practices.",
        ]

    candidates = [{"text": d, "similarity": 0.5} for d in args.docs]
    print("Loading reranker (first time downloads ~570MB)...")
    results = rerank(args.query, candidates, top_k=len(candidates))
    print(f"\nQuery: {args.query}\n")
    for i, r in enumerate(results, 1):
        score = r.get("rerank_score", r.get("similarity", 0))
        print(f"  [{i}] score={score:.4f}: {r['text'][:80]}")
