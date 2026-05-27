#!/usr/bin/env python3
"""hybrid_embedder.py — BGE-M3 hybrid embedder (dense + sparse).

Wraps FlagEmbedding.BGEM3FlagModel to produce both dense_vecs (1024d float16)
and lexical_weights (sparse dict {token_id_str: float_weight}) in a single
forward pass.

Why a separate module from memory_core:
  - memory_core's chromadb collection uses SentenceTransformer for the dense
    path (existing 5782 chunks are embedded with that). We don't want to
    invalidate them by switching frameworks.
  - This embedder is invoked alongside the existing dense path to write the
    sparse store. Same bge-m3 weights → fully compatible.

Singleton: model loads in ~60s on M5 Max (FP16). Keep one instance per
process. Caller is responsible for keeping the embedder alive (or accept
the cold start).
"""
from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_EMBEDDER: Any = None


class BGEM3HybridEmbedder:
    """Thin wrapper around FlagEmbedding.BGEM3FlagModel."""

    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = True,
                 max_length: int = 8192) -> None:
        from FlagEmbedding import BGEM3FlagModel
        self.model = BGEM3FlagModel(model_name, use_fp16=use_fp16)
        self.max_length = max_length

    def embed(self, texts: list[str], dense: bool = True,
              sparse: bool = True, colbert: bool = False,
              batch_size: int = 16) -> dict:
        """Encode a batch.

        Returns dict with keys (depending on flags):
          - "dense": list of np.ndarray shape (1024,) float16
          - "sparse": list of dict {token_id_str: float weight}
          - "colbert": list of np.ndarray (only if colbert=True; large)
        """
        if not texts:
            return {"dense": [], "sparse": [], "colbert": []}
        out = self.model.encode(
            texts,
            batch_size=batch_size,
            max_length=self.max_length,
            return_dense=dense,
            return_sparse=sparse,
            return_colbert_vecs=colbert,
        )
        result = {}
        if dense:
            result["dense"] = out.get("dense_vecs", [])
        if sparse:
            # Normalize defaultdict → plain dict with str keys for JSON safety
            result["sparse"] = [
                {str(k): float(v) for k, v in w.items()}
                for w in out.get("lexical_weights", [])
            ]
        if colbert:
            result["colbert"] = out.get("colbert_vecs", [])
        return result

    def embed_query(self, query: str, dense: bool = False, sparse: bool = True) -> dict:
        """Convenience for single-query encoding.

        Defaults to sparse-only because sparse_recall is the typical use case
        from memory_search.py (dense path still goes through chromadb).
        """
        r = self.embed([query], dense=dense, sparse=sparse, colbert=False)
        out = {}
        if dense:
            out["dense"] = r["dense"][0]
        if sparse:
            out["sparse"] = r["sparse"][0]
        return out


def get_embedder() -> BGEM3HybridEmbedder:
    """Lazy thread-safe singleton."""
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    with _LOCK:
        if _EMBEDDER is None:
            _EMBEDDER = BGEM3HybridEmbedder()
    return _EMBEDDER


if __name__ == "__main__":
    # Smoke test
    import json, sys, time
    t0 = time.time()
    emb = get_embedder()
    print(f"load: {time.time()-t0:.1f}s")

    sample = sys.argv[1] if len(sys.argv) > 1 else "OAuth 部署铁律"
    t0 = time.time()
    out = emb.embed_query(sample, dense=True, sparse=True)
    print(f"embed: {time.time()-t0:.2f}s")
    print(f"  dense shape: {out['dense'].shape if 'dense' in out else None}")
    print(f"  sparse non-zero count: {len(out['sparse'])}")
    top_sparse = sorted(out['sparse'].items(), key=lambda x: -x[1])[:5]
    for token_id, w in top_sparse:
        try:
            tok = emb.model.tokenizer.decode([int(token_id)])
        except Exception:
            tok = "?"
        print(f"    {token_id} weight={w:.4f}  token='{tok}'")
