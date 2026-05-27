#!/usr/bin/env python3
"""
memory_core.py — 本地记忆系统核心层

职责：
- 加载 bge-m3 嵌入模型
- 初始化 ChromaDB PersistentClient
- 文本 chunking（800 字符 + 100 字符重叠）
- 确定性 ID 生成
- 基础 CRUD
"""
import hashlib
import os
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

# ========== 常量 ==========

PALACE_PATH = os.path.expanduser("~/.mempalace-zh/chromadb")
COLLECTION_NAME = "memory"
MODEL_NAME = "BAAI/bge-m3"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
MIN_CHUNK_SIZE = 50

# ========== 单例 ==========

_embedder = None
_client = None
_collection = None


def load_embedder():
    """懒加载嵌入模型。首次调用会下载模型（约 2 GB），之后从本地读取。"""
    global _embedder
    if _embedder is None:
        _embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=MODEL_NAME
        )
    return _embedder


def get_client():
    """获取 ChromaDB 客户端。"""
    global _client
    if _client is None:
        os.makedirs(PALACE_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=PALACE_PATH)
    return _client


def get_collection():
    """获取或创建集合。使用 cosine 距离。"""
    global _collection
    if _collection is None:
        client = get_client()
        ef = load_embedder()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


# ========== Chunking ==========

def chunk_text(content: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """
    切分文本，优先按段落（\\n\\n），其次按行。
    保持 overlap 字符的重叠以避免边界信息丢失。

    返回：[{"text": str, "chunk_index": int}]
    """
    content = content.strip()
    if len(content) < MIN_CHUNK_SIZE:
        return [{"text": content, "chunk_index": 0}] if content else []

    # 如果整个文件小于 chunk size，不切
    if len(content) <= size:
        return [{"text": content, "chunk_index": 0}]

    # 按段落切分
    paragraphs = content.split("\n\n")
    chunks = []
    current = ""

    for para in paragraphs:
        # 如果当前积累的 + 新段落不超过 size，继续积累
        if len(current) + len(para) + 2 <= size:
            current = (current + "\n\n" + para) if current else para
        else:
            # 保存当前 chunk
            if current and len(current) >= MIN_CHUNK_SIZE:
                chunks.append(current)

            # 如果单个段落本身就超过 size，按行切
            if len(para) > size:
                lines = para.split("\n")
                current = ""
                for line in lines:
                    if len(current) + len(line) + 1 <= size:
                        current = (current + "\n" + line) if current else line
                    else:
                        if current and len(current) >= MIN_CHUNK_SIZE:
                            chunks.append(current)
                        current = line
            else:
                current = para

    # 保存最后一个 chunk
    if current and len(current) >= MIN_CHUNK_SIZE:
        chunks.append(current)

    # 添加 overlap — 取前一 chunk 末尾拼到当前 chunk 开头，改善边界召回
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            # 只在前一段足够长时取尾部，否则跳过（避免短段全量重复）
            if len(prev) > overlap * 2:
                overlapped.append(prev[-overlap:] + "\n\n" + chunks[i])
            else:
                overlapped.append(chunks[i])
        chunks = overlapped

    return [{"text": chunk, "chunk_index": i} for i, chunk in enumerate(chunks)]


# ========== ID 生成 ==========

def make_chunk_id(wing: str, source_file: str, chunk_index: int) -> str:
    """生成确定性 chunk ID。"""
    hash_input = f"{source_file}:{chunk_index}".encode("utf-8")
    h = hashlib.md5(hash_input).hexdigest()[:16]
    return f"{wing}_{h}"


# ========== CRUD ==========

def store_chunks(
    wing: str,
    source_file: str,
    chunks: list[dict],
    source_mtime: float,
) -> int:
    """
    将 chunks 入库到指定 wing。
    使用 upsert，重复的 ID 会被覆盖（支持增量更新）。
    如果文件缩短导致 chunk 数变少，自动清理多余的旧 chunk。

    返回：入库的 chunk 数量
    """
    if not chunks:
        return 0

    col = get_collection()
    now = datetime.now().isoformat()

    documents = [c["text"] for c in chunks]
    new_ids = [make_chunk_id(wing, source_file, c["chunk_index"]) for c in chunks]
    metadatas = [
        {
            "wing": wing,
            "source_file": source_file,
            "source_name": os.path.basename(source_file),
            "chunk_index": c["chunk_index"],
            "chunk_total": len(chunks),
            "source_mtime": source_mtime,
            "filed_at": now,
            # Optional LLM-generated summary passthrough — when mp-mine is run
            # with --llm-summarize, each chunk dict gets a 'summary' key. ChromaDB
            # metadata is schema-free so this is fully backward compatible:
            # older chunks just won't have the field.
            **({"summary": c["summary"]} if c.get("summary") else {}),
        }
        for c in chunks
    ]

    col.upsert(documents=documents, ids=new_ids, metadatas=metadatas)

    # 清理多余旧 chunk：如果文件从 N chunks 缩短到 M chunks，删除 index M..N-1
    new_id_set = set(new_ids)
    try:
        existing = col.get(
            where={"$and": [{"wing": wing}, {"source_file": source_file}]},
            include=[],
        )
        stale_ids = [eid for eid in existing.get("ids", []) if eid not in new_id_set]
        if stale_ids:
            col.delete(ids=stale_ids)
    except Exception:
        pass  # 清理失败不阻塞入库

    # D003: dual-write wing_meta — auto-register + bump last_mine_at.
    # We do NOT increment chunk_count here because upsert may overwrite
    # existing chunks (not add new ones). True chunk_count is reconciled
    # by mp-wings-review (live query chromadb) and the weekly cleanup.
    # Fail-silent so a wing_meta DB issue cannot break ingestion.
    try:
        import wing_lifecycle
        wing_lifecycle.update_wing_activity(wing, "mine")
    except Exception:
        pass

    return len(chunks)


def get_stored_mtime(wing: str, source_file: str) -> float | None:
    """
    查询已存储的文件 mtime，用于增量矿挖。
    返回 None 表示文件未入库过。
    """
    col = get_collection()
    try:
        result = col.get(
            where={"$and": [{"wing": wing}, {"source_file": source_file}]},
            limit=1,
            include=["metadatas"],
        )
        if result["metadatas"]:
            return result["metadatas"][0].get("source_mtime")
    except Exception:
        pass
    return None


def delete_file_chunks(wing: str, source_file: str) -> int:
    """删除指定文件的所有 chunks。用于文件被删除时清理。"""
    col = get_collection()
    try:
        result = col.get(
            where={"$and": [{"wing": wing}, {"source_file": source_file}]},
            include=[],
        )
        ids = result.get("ids", [])
        if ids:
            col.delete(ids=ids)
            return len(ids)
    except Exception:
        pass
    return 0


def delete_wing(wing: str) -> int:
    """删除指定 wing 的所有数据。危险操作，谨慎使用。"""
    col = get_collection()
    result = col.get(where={"wing": wing}, include=[])
    ids = result.get("ids", [])
    if ids:
        col.delete(ids=ids)
    return len(ids)


# ========== 状态查询 ==========

def get_wing_stats() -> dict:
    """统计每个 wing 的文件数和 chunk 数。只拉 metadata，不拉 documents/embeddings。"""
    col = get_collection()
    try:
        # 只取 metadatas — 不加载 documents 和 embeddings，节省内存
        result = col.get(include=["metadatas"], limit=col.count() or 1)
        metadatas = result.get("metadatas", [])
    except Exception:
        return {}

    stats = {}
    seen_files: dict[str, set] = {}
    for meta in metadatas:
        wing = meta.get("wing", "unknown")
        src = meta.get("source_file", "")
        if wing not in stats:
            stats[wing] = {"chunks": 0, "files": 0}
            seen_files[wing] = set()
        stats[wing]["chunks"] += 1
        if src not in seen_files[wing]:
            seen_files[wing].add(src)
            stats[wing]["files"] += 1
    return stats


def get_total_count() -> int:
    """总 chunk 数。"""
    col = get_collection()
    try:
        return col.count()
    except Exception:
        return 0
