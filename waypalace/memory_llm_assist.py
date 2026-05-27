#!/usr/bin/env python3
"""memory_llm_assist.py — Local LLM assistant for the long-term memory system.

Talks to the mlx_lm.server (com.user.waypalace.mlx-llm.daemon, port 8081) over an
OpenAI-compatible API. The model is Qwen3.6-35B-A3B-UD-MLX-4bit running on
M5 Max via MLX (~120 tok/s decode, ~21 GB RAM resident).

Provides 4 standalone functions to enhance the existing memory system without
modifying its core (memory_core.py / memory_search.py / memory_mine.py):

  - classify_wing(content, candidate_wings) -> str
      Decide which project wing this content belongs to.
  - summarize_chunk(text, max_words=30) -> str
      One-line summary added to chunk metadata for better retrieval.
  - should_archive(text, last_used_days_ago, usage_count) -> bool
      Smart aging: is this chunk still relevant?
  - merge_conflict(old_text, new_text) -> dict
      When new memory conflicts with old: keep / replace / merge.

Design notes:
  - Always uses chat_template_kwargs={"enable_thinking": False} — Qwen3.6
    defaults to thinking mode and the /no_think prompt marker is unreliable.
  - Single-shot calls (no streaming) — these are background helpers, not UX.
  - 5s timeout per call. If LLM is down (daemon offline), each function
    returns a sensible fallback so the memory system still works.
  - All prompts in Chinese — memory store is bilingual but skews Chinese.

Usage from another module:
  from memory_llm_assist import classify_wing, summarize_chunk
  wing = classify_wing("<project-c> 部署到 Cloudflare...", ["<project-c>", "<project-a>", "global"])

CLI smoke test:
  python memory_llm_assist.py classify "<project-c> stripe deploy" <project-c>,<project-a>,global
  python memory_llm_assist.py summarize "今天用 launchd 起了 Qwen3.6 ..."
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "http://127.0.0.1:8081/v1/chat/completions"
MODEL = "unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit"
TIMEOUT_SEC = 30  # First call after cold start can be slow; warm calls ~1s

# Decision audit log — every classify_wing call records input/reasoning/wing
# so we can later audit misclassifications and iterate on the prompt.
CLASSIFY_LOG = "${WAYPALACE_DATA}/logs/classify_decisions.jsonl"


def _chat(messages: list[dict], max_tokens: int = 100, temp: float = 0.3) -> str | None:
    """One-shot chat completion. Returns content string or None on failure."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temp,
        "top_p": 0.80,
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        sys.stderr.write(f"[memory_llm_assist] LLM call failed: {e}\n")
        return None


_WING_CLASSIFY_FEW_SHOT = """\
示例 1
内容: "git rebase -i 跨项目通用操作经验"
推理: git 操作是通用知识，不绑定某个项目。
分类: global

示例 2
内容: "<project-c> 部署到 Cloudflare Workers，wrangler.toml 加了 STRIPE_SECRET_KEY"
推理: 明确提到 <project-c> 项目和其特定密钥配置。
分类: <project-c>

示例 3
内容: "OAuth 部署铁律: NEXTAUTH_URL 必须跟实际域名一致"
推理: OAuth 部署经验适用于所有项目，是通用铁律。
分类: global

示例 4
内容: "<project-a> safescan 模块新增可疑域名检测逻辑"
推理: 明确提到 <project-a> 项目的特定模块。
分类: <project-a>

示例 5 (重要 — 工具/系统讨论内容)
内容: "Tier 2 PostToolUse hook 异步触发 mp-mine 矿入 ChromaDB 的测试笔记"
推理: 内容主体是在讨论记忆系统的 hook 机制和测试，虽然提到 mp-mine/ChromaDB
      等工具术语，但不是任何具体项目的特定内容，是系统工具讨论。
分类: global

示例 6 (重要 — 区分"项目主题"vs"工具术语夹带")
内容: "用 launchd 起 mlx_lm.server 跑 Qwen 模型常驻内存，加 memory_llm_assist.py
      调用提供 wing 分类"
推理: 内容主体是配置本地 AI 工具栈（launchd / mlx / Qwen / memory_llm_assist），
      跨项目通用基础设施，不是某个具体业务项目。
分类: global

判断规则总结:
- 内容的"主体讨论对象"是项目业务（产品/客户/具体功能）→ 对应项目 wing
- 内容的"主体讨论对象"是工具/系统/方法论/基础设施 → global
  （即使内容里出现了某些项目术语作为例子）
- 模糊或难以判断 → global（保守归类，避免污染项目 wing）
"""


def classify_wing(content: str, candidate_wings: list[str]) -> str:
    """Classify content into a wing. Returns 'global' for general/uncertain content.

    Strategy (industry best-practice prompt engineering):
      1. JSON output with explicit reasoning field — forces step-by-step before answer
      2. Separates 'global' (general fallback) from project wings to avoid
         serial-position bias where LLM picks list-first item
      3. Few-shot examples covering both global and project-specific cases
      4. Strict validation: parsed wing must be in candidate_wings or
         we fall back to 'global'
    """
    if not candidate_wings:
        return "global"

    # Normalize candidates + extract project_wings (global handled separately)
    has_global = any(w.lower() == "global" for w in candidate_wings)
    project_wings = [w for w in candidate_wings if w.lower() != "global"]

    # Edge case: no project wings, only global → trivially return global
    if not project_wings:
        return next(w for w in candidate_wings if w.lower() == "global")

    fallback = "global" if has_global else project_wings[0]

    messages = [
        {
            "role": "system",
            "content": (
                "你是记忆系统的 wing 分类器，输出严格 JSON。\n"
                "分类规则:\n"
                "1. 通用经验/跨项目操作/与具体项目无关的知识 → wing 必须为 global\n"
                "2. 明确提到某个项目名 或 仅适用该项目的内容 → 对应项目 wing\n"
                "3. 边界模糊/不确定 → wing 必须为 global（保守归类，避免污染项目 wing）\n"
                '输出格式: {"reasoning": "一句话推理", "wing": "wing 名"}\n'
                "wing 名必须是小写英文，且必须出现在候选列表中。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"{_WING_CLASSIFY_FEW_SHOT}\n"
                f"---\n\n"
                f"现在分类以下内容：\n\n"
                f"内容:\n{content[:1500]}\n\n"
                f"候选项目 wings: {project_wings}\n"
                + (f"通用 wing: global（适用于通用/跨项目/不确定内容）\n" if has_global else "")
                + f"\n严格按 JSON 输出："
            ),
        },
    ]
    result = _chat(messages, max_tokens=200, temp=0.3)
    if result is None:
        return fallback

    # Parse JSON (model may add prose before/after)
    chosen: str
    reasoning: str
    parse_status: str
    try:
        start = result.index("{")
        end = result.rindex("}") + 1
        parsed = json.loads(result[start:end])
        wing_raw = str(parsed.get("wing", "")).lower().strip().strip('"').strip("'").strip(".")
        reasoning = str(parsed.get("reasoning", ""))
        # Strict validation: must be in candidate_wings
        matched = None
        for w in candidate_wings:
            if w.lower() == wing_raw:
                matched = w
                break
        if matched is not None:
            chosen = matched
            parse_status = "ok"
        else:
            chosen = fallback
            parse_status = f"wing_not_in_candidates:{wing_raw!r}"
            sys.stderr.write(
                f"[memory_llm_assist] classify_wing returned {wing_raw!r} not in {candidate_wings}, "
                f"falling back to {fallback!r}. Reasoning: {reasoning!r}\n"
            )
    except (ValueError, json.JSONDecodeError) as e:
        chosen = fallback
        reasoning = ""
        parse_status = f"json_parse_failed:{e}"
        sys.stderr.write(
            f"[memory_llm_assist] classify_wing JSON parse failed: {e}. Raw: {result!r}\n"
        )

    # Audit log — every decision is durable on disk so we can later grep for
    # mis-classifications and iterate on prompt without losing history.
    try:
        import datetime
        os.makedirs(os.path.dirname(CLASSIFY_LOG), exist_ok=True)
        with open(CLASSIFY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "wing": chosen,
                "parse_status": parse_status,
                "reasoning": reasoning[:200],
                "content_preview": content[:120].replace("\n", " "),
                "candidates": candidate_wings,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass  # audit log is best-effort; never break the function
    return chosen


def summarize_chunk(text: str, max_words: int = 30) -> str:
    """Return a one-line summary suitable for chunk metadata. Returns '' on failure."""
    messages = [
        {
            "role": "system",
            "content": f"你是文档摘要器。用最多 {max_words} 个中文字总结，不要前言，不要标点结尾。",
        },
        {"role": "user", "content": f"原文:\n{text[:3000]}\n\n输出摘要："},
    ]
    result = _chat(messages, max_tokens=80, temp=0.3)
    if result is None:
        return ""
    # Strip common preamble tokens
    return result.replace("摘要：", "").replace("摘要:", "").strip()


def should_archive(text: str, last_used_days_ago: int, usage_count: int) -> bool:
    """Decide if this chunk should be archived (stale / no longer relevant).

    Returns True to archive, False to keep. Conservative: prefers keep on
    LLM failure.
    """
    # Cheap shortcut: very recently used → keep
    if last_used_days_ago < 30 or usage_count >= 5:
        return False

    messages = [
        {
            "role": "system",
            "content": "你判断记忆条目是否过时该归档。只输出 KEEP 或 ARCHIVE。",
        },
        {
            "role": "user",
            "content": (
                f"内容:\n{text[:1500]}\n\n"
                f"上次使用: {last_used_days_ago} 天前\n"
                f"历史使用次数: {usage_count}\n\n"
                f"判断: 还有参考价值 → KEEP；明显过时/一次性 → ARCHIVE"
            ),
        },
    ]
    result = _chat(messages, max_tokens=10, temp=0.0)
    if result is None:
        return False  # Conservative: keep on failure
    return result.strip().upper().startswith("ARCHIVE")


def merge_conflict(old_text: str, new_text: str) -> dict:
    """When new memory conflicts with old, decide action.

    Returns: {"action": "keep_old"|"keep_new"|"merge", "merged": str (if action=merge)}
    Falls back to keep_old on LLM failure (conservative: don't overwrite).
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是记忆冲突仲裁器。判断旧记忆 vs 新记忆，输出 JSON："
                '{"action": "keep_old"|"keep_new"|"merge", "merged": "如果是 merge，给合并后的文本"}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"旧记忆:\n{old_text[:2000]}\n\n"
                f"新记忆:\n{new_text[:2000]}\n\n"
                f"输出 JSON："
            ),
        },
    ]
    result = _chat(messages, max_tokens=500, temp=0.3)
    if result is None:
        return {"action": "keep_old"}
    # Extract JSON from response (model may add prose)
    try:
        start = result.index("{")
        end = result.rindex("}") + 1
        parsed = json.loads(result[start:end])
        if parsed.get("action") in ("keep_old", "keep_new", "merge"):
            return parsed
    except (ValueError, json.JSONDecodeError):
        pass
    sys.stderr.write(f"[memory_llm_assist] merge_conflict unparseable: {result!r}\n")
    return {"action": "keep_old"}


def ping() -> dict:
    """Health check. Returns {ok, latency_ms, model}."""
    import time

    t0 = time.time()
    result = _chat([{"role": "user", "content": "ping"}], max_tokens=3)
    elapsed_ms = int((time.time() - t0) * 1000)
    return {
        "ok": result is not None,
        "latency_ms": elapsed_ms,
        "model": MODEL,
        "reply": result,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: memory_llm_assist.py <ping|classify|summarize|aging|merge> [args...]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "ping":
        print(json.dumps(ping(), ensure_ascii=False, indent=2))
    elif cmd == "classify" and len(sys.argv) >= 4:
        content = sys.argv[2]
        wings = sys.argv[3].split(",")
        print(classify_wing(content, wings))
    elif cmd == "summarize" and len(sys.argv) >= 3:
        print(summarize_chunk(sys.argv[2]))
    elif cmd == "aging" and len(sys.argv) >= 5:
        print(should_archive(sys.argv[2], int(sys.argv[3]), int(sys.argv[4])))
    elif cmd == "merge" and len(sys.argv) >= 4:
        print(json.dumps(merge_conflict(sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=2))
    else:
        print(f"bad args for {cmd}")
        sys.exit(1)
