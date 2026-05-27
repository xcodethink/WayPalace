#!/usr/bin/env python3
"""
memory_dedup.py - Semantic deduplication layer (V2.3)

Before storing new chunks, compare against existing chunks in the same wing.
If similarity >= 0.92, treat as duplicate and apply resolution policy.

Resolutions:
  - auto_superseded: new chunk supersedes old (newer source_mtime)
  - kept_newer: keep new, drop existing as archived
  - flagged: 0.80-0.92 similarity → record as potential conflict for review
  - dropped: new is an exact duplicate of existing → skip storing
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory_core
import memory_aging

DEDUP_THRESHOLD = 0.92       # exact duplicate
CONFLICT_THRESHOLD = 0.80    # potential conflict, needs AI check (V2.4 hook)
TOP_K_CANDIDATES = 5


def cosine_from_distance(d):
    """ChromaDB cosine distance → similarity."""
    return 1.0 - d if d is not None else 0.0


def check_chunk_for_duplicates(wing: str, text: str, exclude_file: str | None = None):
    """
    Search existing chunks in the same wing for near-duplicates of `text`.
    Returns list of candidates: {chunk_id, similarity, source_file, text}
    Filters out chunks from exclude_file (usually the incoming file, to avoid
    matching previous versions of the same file during re-indexing).
    """
    try:
        embedder = memory_core.load_embedder()
        col = memory_core.get_collection()
        query_emb = embedder.encode([text], show_progress_bar=False)[0].tolist()

        # Filter: same wing, optionally exclude the file being re-indexed
        where_clause = {"wing": wing}
        # ChromaDB doesn't support "AND NOT source_file = X" easily; filter post-hoc
        result = col.query(
            query_embeddings=[query_emb],
            n_results=TOP_K_CANDIDATES,
            where=where_clause,
            include=["distances", "documents", "metadatas"],
        )
        if not result or not result.get("ids") or not result["ids"][0]:
            return []

        candidates = []
        ids = result["ids"][0]
        dists = result["distances"][0]
        docs = result["documents"][0]
        metas = result["metadatas"][0]
        for cid, dist, doc, meta in zip(ids, dists, docs, metas):
            src = meta.get("source_file", "") if meta else ""
            if exclude_file and src == exclude_file:
                continue
            sim = cosine_from_distance(dist)
            candidates.append({
                "chunk_id": cid,
                "similarity": sim,
                "source_file": src,
                "text": doc,
            })
        return candidates
    except Exception:
        return []


def dedup_incoming_chunks(wing: str, source_file: str, chunks: list[dict]) -> dict:
    """
    For a batch of incoming chunks, check each against existing ones.
    Returns dict with:
      - filtered_chunks: chunks that should actually be stored
      - duplicates: list of detected dupes with resolutions
      - potential_conflicts: list of 0.80-0.92 matches (for V2.4 AI check)

    The policy: on re-indexing the SAME source_file, we don't flag anything
    (we assume the user updated content). Only flag cross-file duplicates.
    """
    filtered = []
    duplicates = []
    potential_conflicts = []

    for chunk in chunks:
        text = chunk.get("text", "")
        idx = chunk.get("chunk_index", 0)
        # Skip very short chunks (likely stubs, not worth dedup)
        if len(text) < 200:
            filtered.append(chunk)
            continue

        candidates = check_chunk_for_duplicates(wing, text, exclude_file=source_file)
        if not candidates:
            filtered.append(chunk)
            continue

        best = max(candidates, key=lambda c: c["similarity"])
        incoming_id = memory_core.make_chunk_id(wing, source_file, idx)

        if best["similarity"] >= DEDUP_THRESHOLD:
            # Strong duplicate — supersede the old chunk, keep the new
            memory_aging.record_duplicate(
                chunk_id=incoming_id,
                duplicate_of=best["chunk_id"],
                wing=wing,
                similarity=best["similarity"],
                resolution="auto_superseded",
            )
            # Mark old chunk as superseded via chunk_aging
            try:
                conn = memory_aging.get_db()
                conn.execute(
                    "UPDATE chunk_aging SET superseded_by = ? WHERE chunk_id = ?",
                    (incoming_id, best["chunk_id"]),
                )
                conn.commit()
            except Exception:
                pass
            duplicates.append({**best, "incoming_id": incoming_id})
            filtered.append(chunk)  # still store the new one
        elif best["similarity"] >= CONFLICT_THRESHOLD:
            # Potential conflict — store, but flag for AI review
            potential_conflicts.append({
                "incoming_id": incoming_id,
                "incoming_text": text,
                "existing_id": best["chunk_id"],
                "existing_text": best["text"],
                "similarity": best["similarity"],
                "existing_source": best["source_file"],
                "wing": wing,
            })
            filtered.append(chunk)
        else:
            filtered.append(chunk)

    return {
        "filtered_chunks": filtered,
        "duplicates": duplicates,
        "potential_conflicts": potential_conflicts,
    }


if __name__ == "__main__":
    import json, argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--wing", required=True)
    parser.add_argument("--text", required=True, help="Text to check")
    args = parser.parse_args()

    candidates = check_chunk_for_duplicates(args.wing, args.text)
    for c in candidates:
        print(f"[{c['similarity']:.3f}] {c['chunk_id']} — {os.path.basename(c['source_file'])}")
        print(f"  {c['text'][:100]}...")
