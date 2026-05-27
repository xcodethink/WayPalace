#!/usr/bin/env python3
"""sparse_store.py — SQLite-backed store for bge-m3 lexical_weights (sparse vectors).

Why a separate store: ChromaDB 1.5.7 doesn't natively persist sparse vectors
alongside dense embeddings, so we keep a parallel SQLite table keyed by
chunk_id. The dense vector continues to live in ChromaDB (where HNSW makes
ANN cheap); sparse vectors live here for inner-product retrieval.

Schema:
  chunk_sparse_weights(
    chunk_id TEXT PRIMARY KEY,
    weights TEXT,        -- compact JSON {token_id_str: float_weight}
    wing TEXT,           -- for filtered queries
    updated_at TEXT
  )

Inner-product query: O(N) over filtered rows. With 5782 chunks and
~20 non-zero tokens per chunk, this is ~115k multiplications — trivially fast
(<50 ms in pure Python; faster if we numpy-ize later).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Iterable

DB_PATH = os.path.expanduser("~/.mempalace-zh/sparse.sqlite3")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunk_sparse_weights (
            chunk_id TEXT PRIMARY KEY,
            weights TEXT NOT NULL,
            wing TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sparse_wing ON chunk_sparse_weights(wing)")
    conn.commit()
    return conn


def _encode_weights(weights: dict) -> str:
    """Encode {token_id: weight} as compact JSON. token_id is str-keyed for JSON."""
    return json.dumps({str(k): float(v) for k, v in weights.items()},
                      ensure_ascii=False, separators=(",", ":"))


def _decode_weights(blob: str) -> dict[str, float]:
    return {k: v for k, v in json.loads(blob).items()}


def upsert_batch(rows: Iterable[tuple[str, dict, str]]) -> int:
    """Batch upsert. Each row = (chunk_id, weights_dict, wing). Returns count."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn = _conn()
    cur = conn.cursor()
    n = 0
    for chunk_id, weights, wing in rows:
        cur.execute("""
            INSERT INTO chunk_sparse_weights (chunk_id, weights, wing, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                weights = excluded.weights,
                wing    = excluded.wing,
                updated_at = excluded.updated_at
        """, (chunk_id, _encode_weights(weights), wing, now))
        n += 1
    conn.commit()
    conn.close()
    return n


def delete_chunks(chunk_ids: Iterable[str]) -> int:
    """Delete sparse entries — call this when chromadb chunks are deleted."""
    conn = _conn()
    cur = conn.cursor()
    n = 0
    for cid in chunk_ids:
        cur.execute("DELETE FROM chunk_sparse_weights WHERE chunk_id = ?", (cid,))
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n


def sparse_recall(query_weights: dict, wing: str | None = None,
                  n: int = 50) -> list[tuple[str, float]]:
    """Inner-product retrieval against the sparse store.

    Returns [(chunk_id, score)] sorted desc by score. Score = Σ q_w[t] * d_w[t]
    for tokens t appearing in both query and doc.

    Filtering by wing happens at SQL level — only candidates from the wing
    (or global) make it into the Python inner loop.
    """
    if not query_weights:
        return []
    conn = _conn()
    cur = conn.cursor()
    if wing:
        cur.execute("SELECT chunk_id, weights FROM chunk_sparse_weights WHERE wing IN (?, 'global')",
                    (wing,))
    else:
        cur.execute("SELECT chunk_id, weights FROM chunk_sparse_weights")
    rows = cur.fetchall()
    conn.close()

    q = {str(k): float(v) for k, v in query_weights.items()}

    scored = []
    for chunk_id, blob in rows:
        d = _decode_weights(blob)
        if len(q) < len(d):
            s = sum(qw * d.get(t, 0.0) for t, qw in q.items())
        else:
            s = sum(d_w * q.get(t, 0.0) for t, d_w in d.items())
        if s > 0:
            scored.append((chunk_id, s))

    scored.sort(key=lambda x: -x[1])
    return scored[:n]


def get_stats() -> dict:
    """Return basic stats for mp-health / mp-status."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT wing) FROM chunk_sparse_weights")
    total, wings = cur.fetchone()
    cur.execute("SELECT wing, COUNT(*) FROM chunk_sparse_weights GROUP BY wing ORDER BY 2 DESC LIMIT 10")
    by_wing = dict(cur.fetchall())
    conn.close()
    return {"total": total, "wings": wings, "top_wings": by_wing}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        s = get_stats()
        print(f"sparse store: {s['total']} chunks in {s['wings']} wings")
        for w, n in s['top_wings'].items():
            print(f"  {w:30s}  {n}")
    else:
        print(__doc__)
