#!/usr/bin/env python3
"""
memory_mine.py — 矿挖逻辑

职责：
- 扫描目录或单文件
- 敏感文件过滤（黑名单 + 内容扫描）
- 增量矿挖（mtime 对比）
- 批量入库
"""
import fcntl
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import memory_core


def _emit_metric(event: str, **fields) -> None:
    """Local-only metrics; fail-silent."""
    try:
        from mp_metrics import record_event
        record_event(event, **fields)
    except Exception:
        pass

# Advisory file lock to prevent concurrent ChromaDB writes from corrupting
# the HNSW index. Tier 1 hourly batch + Tier 2 PostToolUse hook may try to
# write simultaneously — historical incident: "两次 mine 同时跑 → 索引并发损坏".
# fcntl is POSIX-standard; on macOS it works on regular files.
_MINE_LOCK_PATH = os.path.expanduser("~/.mempalace-zh/mine.lock")


@contextmanager
def _mine_lock(timeout: int = 120):
    """Acquire exclusive advisory file lock. Block up to `timeout` seconds.

    Designed for cross-process mutex (Tier 1 launchd batch vs Tier 2 hook spawn).
    On timeout, raises RuntimeError so caller can decide whether to skip.
    """
    os.makedirs(os.path.dirname(_MINE_LOCK_PATH), exist_ok=True)
    fd = os.open(_MINE_LOCK_PATH, os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise RuntimeError(f"could not acquire mine lock within {timeout}s")
                time.sleep(0.5)
        os.write(fd, f"pid={os.getpid()} ts={time.time():.0f}\n".encode())
        os.fsync(fd)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)

# ========== 安全过滤 ==========

# 文件名黑名单（绝对不挖）
BLACKLIST_PATTERNS = [
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
    "wrangler.toml",
    "credentials.json",
    "service-account.json",
    "private_key",
    ".pem",
    ".key",
]

# 内容敏感模式（正则）
SENSITIVE_CONTENT_PATTERNS = [
    re.compile(r"(?i)password\s*[:=]\s*['\"]?[a-zA-Z0-9!@#$%^&*]{6,}"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"(?i)secret\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"(?i)token\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"sk-[a-zA-Z0-9]{32,}"),  # OpenAI / Anthropic style keys
    re.compile(r"sk_live_[a-zA-Z0-9]{20,}"),  # Stripe live keys
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),  # GitHub personal token
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),  # Private keys
]


def is_blacklisted_file(path: str) -> bool:
    """检查文件名是否在黑名单。"""
    name = os.path.basename(path)
    name_lower = name.lower()
    for pattern in BLACKLIST_PATTERNS:
        if pattern in name_lower:
            return True
    return False


def has_sensitive_content(content: str) -> tuple[bool, str]:
    """
    扫描内容中的敏感模式。
    返回 (是否敏感, 触发的模式描述)
    """
    for pattern in SENSITIVE_CONTENT_PATTERNS:
        match = pattern.search(content)
        if match:
            return True, pattern.pattern[:50]
    return False, ""


# ========== 文件扫描 ==========

def find_markdown_files(directory: str) -> list[str]:
    """
    递归查找目录下的所有 .md 文件。
    返回绝对路径列表（已 realpath 去重），已过滤黑名单和隐藏目录。
    使用 os.walk 不跟随符号链接，并对每个文件做 realpath 去重以防止循环符号链接。
    """
    files_set = set()
    directory = os.path.expanduser(directory)
    if not os.path.isdir(directory):
        return []

    # followlinks=False，避免循环符号链接（如 ~/.claude/skills <-> ClaudeCodeSkills）
    for root, dirs, filenames in os.walk(directory, followlinks=False):
        # 跳过隐藏目录和 node_modules 等
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and d not in ("node_modules", "venv", "venv-zh", "__pycache__", "dist", "build", "_待合并")
        ]
        for fn in filenames:
            if fn.endswith(".md") and not is_blacklisted_file(fn):
                full_path = os.path.join(root, fn)
                # realpath 去重，处理符号链接指向同一文件的情况
                real_path = os.path.realpath(full_path)
                files_set.add(real_path)
    return sorted(files_set)


# ========== 矿挖核心 ==========

def _get_candidate_wings() -> list[str]:
    """Return list of existing wings + 'global' fallback for LLM classification."""
    try:
        stats = memory_core.get_wing_stats()
        wings = sorted(stats.keys())
    except Exception:
        wings = []
    if "global" not in wings:
        wings.append("global")
    return wings


def mine_file(
    file_path: str,
    wing: str | None = None,
    force: bool = False,
    verbose: bool = True,
    llm_classify: bool = False,
    llm_summarize: bool = False,
) -> dict:
    """
    矿挖单个文件。
    返回 {status, chunks, reason, wing?}
    status: "indexed" | "skipped" | "blocked" | "empty" | "error"

    wing:
      - 显式传入则使用
      - None 且 llm_classify=True → 用 LLM 分类到已存在 wings + global
      - None 且 llm_classify=False → 抛 ValueError (调用者必须给一个)

    llm_summarize: True 时给每个 chunk 调 memory_llm_assist.summarize_chunk()
                   生成 summary 字段，写入 ChromaDB metadata（每个 chunk +1.3s）。
    """
    file_path = os.path.abspath(os.path.expanduser(file_path))

    if not os.path.isfile(file_path):
        return {"status": "error", "reason": "文件不存在"}

    # 黑名单检查
    if is_blacklisted_file(file_path):
        return {"status": "blocked", "reason": "文件名在黑名单"}

    # 读文件（先读才能给 LLM 分类）
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"status": "error", "reason": f"读取失败: {e}"}

    if not content.strip():
        return {"status": "empty", "reason": "文件为空"}

    # 敏感内容扫描（在 LLM 调用前做：敏感文件不应发到 LLM）
    is_sensitive, pattern = has_sensitive_content(content)
    if is_sensitive:
        return {"status": "blocked", "reason": f"含敏感内容: {pattern}"}

    # LLM wing 分类（仅当显式开启）
    if wing is None:
        if not llm_classify:
            return {"status": "error", "reason": "未指定 wing 且未启用 --llm-classify"}
        try:
            from memory_llm_assist import classify_wing
        except Exception as e:
            return {"status": "error", "reason": f"无法 import memory_llm_assist: {e}"}
        candidates = _get_candidate_wings()
        # 用文件开头 1500 字符作分类输入（够上下文，不浪费 token）
        wing = classify_wing(content[:1500], candidates)

    # mtime 增量检查
    mtime = os.path.getmtime(file_path)
    if not force:
        stored = memory_core.get_stored_mtime(wing, file_path)
        if stored is not None and abs(stored - mtime) < 1:  # 1 秒精度
            # 普通增量跳过 OK
            # 但当请求 --llm-summarize 时，需要进一步检查已存 chunks 是否都有 summary。
            # 若有缺：之前可能是 LLM daemon 临时挂掉导致 summary 没写但 mtime 记上了，
            # 不重 mine 这些 chunks 永远不会有 summary。
            if llm_summarize:
                try:
                    col = memory_core.get_collection()
                    existing = col.get(
                        where={"$and": [{"wing": wing}, {"source_file": file_path}]},
                        include=["metadatas"],
                    )
                    metas = existing.get("metadatas", []) or []
                    if metas and not all("summary" in m for m in metas):
                        # 至少有一个 chunk 缺 summary — 强制重 mine
                        force = True  # fall through to chunking + summarize + store
                    else:
                        return {"status": "skipped", "reason": "未变化", "wing": wing}
                except Exception:
                    # 检查失败时保守跳过，避免无脑重 mine
                    return {"status": "skipped", "reason": "未变化", "wing": wing}
            else:
                return {"status": "skipped", "reason": "未变化", "wing": wing}

    # Chunking
    chunks = memory_core.chunk_text(content)
    if not chunks:
        return {"status": "empty", "reason": "chunk 为空"}

    # V2.3: Semantic dedup check (non-fatal — failures fall through to normal store)
    dedup_report = None
    try:
        import memory_dedup
        dedup_report = memory_dedup.dedup_incoming_chunks(wing, file_path, chunks)
        chunks = dedup_report["filtered_chunks"]
    except Exception:
        pass  # Dedup is best-effort; never block ingestion

    # LLM 摘要（仅当显式开启；放在 dedup 之后避免给被去重的 chunk 浪费 LLM 调用）
    if llm_summarize and chunks:
        try:
            from memory_llm_assist import summarize_chunk
            for c in chunks:
                s = summarize_chunk(c["text"], max_words=30)
                if s:
                    c["summary"] = s
        except Exception as e:
            sys.stderr.write(f"[mine_file] llm_summarize 失败（非致命）: {e}\n")

    # 入库 — 用 advisory lock 防 Tier 1/Tier 2 并发写损坏 HNSW 索引
    try:
        with _mine_lock(timeout=120):
            n = memory_core.store_chunks(wing, file_path, chunks, mtime)
        result = {"status": "indexed", "chunks": n, "wing": wing}
        if llm_summarize:
            result["summarized"] = sum(1 for c in chunks if c.get("summary"))
        if dedup_report:
            if dedup_report["duplicates"]:
                result["duplicates"] = len(dedup_report["duplicates"])
            if dedup_report["potential_conflicts"]:
                result["potential_conflicts"] = len(dedup_report["potential_conflicts"])
                # Defer AI conflict detection to cron to avoid blocking mine
                try:
                    import memory_conflict
                    memory_conflict.queue_conflicts(dedup_report["potential_conflicts"])
                except Exception:
                    pass
        return result
    except Exception as e:
        return {"status": "error", "reason": f"入库失败: {e}"}


def mine_directory(
    directory: str,
    wing: str | None,
    force: bool = False,
    verbose: bool = True,
    llm_classify: bool = False,
    llm_summarize: bool = False,
) -> dict:
    """
    递归矿挖目录下的所有 markdown 文件。
    返回汇总统计。
    """
    files = find_markdown_files(directory)
    if not files:
        return {
            "wing": wing,
            "directory": directory,
            "total": 0,
            "indexed": 0,
            "skipped": 0,
            "blocked": 0,
            "empty": 0,
            "errors": 0,
            "chunks": 0,
        }

    stats = {
        "wing": wing,
        "directory": directory,
        "total": len(files),
        "indexed": 0,
        "skipped": 0,
        "blocked": 0,
        "empty": 0,
        "errors": 0,
        "chunks": 0,
        "blocked_files": [],
        "error_files": [],
    }

    for i, fp in enumerate(files, 1):
        result = mine_file(
            fp, wing,
            force=force, verbose=False,
            llm_classify=llm_classify, llm_summarize=llm_summarize,
        )
        status = result["status"]

        if status == "indexed":
            stats["indexed"] += 1
            stats["chunks"] += result.get("chunks", 0)
            mark = "+"
        elif status == "skipped":
            stats["skipped"] += 1
            mark = "."
        elif status == "blocked":
            stats["blocked"] += 1
            stats["blocked_files"].append((fp, result.get("reason", "")))
            mark = "B"
        elif status == "empty":
            stats["empty"] += 1
            mark = "_"
        else:  # error
            stats["errors"] += 1
            stats["error_files"].append((fp, result.get("reason", "")))
            mark = "X"

        if verbose:
            rel = os.path.relpath(fp, os.path.expanduser(directory)) if os.path.isdir(os.path.expanduser(directory)) else os.path.basename(fp)
            print(f"  [{mark}] [{i:>4}/{stats['total']}] {rel[:70]}", file=sys.stderr)

    return stats


# ========== CLI ==========

def main():
    """命令行入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="矿挖文件到记忆系统")
    parser.add_argument("path", help="文件或目录路径")
    parser.add_argument("--wing", default=None, help="目标 wing 名称（与 --llm-classify 二选一）")
    parser.add_argument("--force", action="store_true", help="强制重新矿挖（忽略 mtime）")
    parser.add_argument("--quiet", action="store_true", help="只打印汇总")
    parser.add_argument(
        "--llm-classify",
        action="store_true",
        help="opt-in: 用本地 LLM 自动分类每个文件到 wing（需要 mlx-llm daemon 运行）。"
             "可不传 --wing；候选 = 已存在 wings + global。每文件 ~2s 延迟",
    )
    parser.add_argument(
        "--llm-summarize",
        action="store_true",
        help="opt-in: 用本地 LLM 给每个 chunk 生成摘要写入 metadata（提升检索精度）。"
             "每 chunk ~1.3s，大文件会很慢",
    )
    args = parser.parse_args()

    if args.wing is None and not args.llm_classify:
        parser.error("必须指定 --wing 或开 --llm-classify")

    path = os.path.expanduser(args.path)
    _t0 = time.time()

    if os.path.isfile(path):
        result = mine_file(
            path, args.wing,
            force=args.force, verbose=not args.quiet,
            llm_classify=args.llm_classify, llm_summarize=args.llm_summarize,
        )
        print(f"\n{path}")
        print(f"  状态: {result['status']}")
        if "wing" in result:
            print(f"  Wing: {result['wing']}")
        if "reason" in result:
            print(f"  原因: {result['reason']}")
        if "chunks" in result:
            print(f"  Chunks: {result['chunks']}")
        if "summarized" in result:
            print(f"  LLM 摘要: {result['summarized']}/{result['chunks']}")
        _emit_metric("mine", file=path, wing=result.get("wing", "?"),
                     status=("ok" if result.get("status") == "indexed" else result.get("status", "fail")),
                     chunks=result.get("chunks", 0),
                     total_ms=int((time.time() - _t0) * 1000),
                     llm_summarize=bool(args.llm_summarize))
    elif os.path.isdir(path):
        stats = mine_directory(
            path, args.wing,
            force=args.force, verbose=not args.quiet,
            llm_classify=args.llm_classify, llm_summarize=args.llm_summarize,
        )
        _emit_metric("mine_batch", dir=path, wing=(args.wing or "*"),
                     files_total=stats["total"], files_indexed=stats["indexed"],
                     files_skipped=stats["skipped"], files_blocked=stats["blocked"],
                     files_errors=stats["errors"], chunks=stats["chunks"],
                     total_ms=int((time.time() - _t0) * 1000),
                     llm_classify=bool(args.llm_classify), llm_summarize=bool(args.llm_summarize),
                     status=("ok" if stats["errors"] == 0 else "partial"))
        print(f"\n=== 矿挖完成 [wing={args.wing}] ===")
        print(f"目录: {stats['directory']}")
        print(f"文件总数: {stats['total']}")
        print(f"  入库: {stats['indexed']}")
        print(f"  跳过(未变化): {stats['skipped']}")
        print(f"  拦截(敏感): {stats['blocked']}")
        print(f"  空文件: {stats['empty']}")
        print(f"  错误: {stats['errors']}")
        print(f"总 chunks: {stats['chunks']}")

        if stats['blocked_files']:
            print(f"\n被拦截的文件:")
            for fp, reason in stats['blocked_files'][:10]:
                print(f"  - {fp}: {reason}")
        if stats['error_files']:
            print(f"\n出错的文件:")
            for fp, reason in stats['error_files'][:10]:
                print(f"  - {fp}: {reason}")
    else:
        print(f"路径不存在: {path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
