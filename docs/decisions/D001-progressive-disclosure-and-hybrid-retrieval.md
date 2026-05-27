# D001 — Progressive Disclosure + Hybrid Retrieval + Conditional SessionStart

**Status**: Accepted (implementing 2026-05-26)
**Owners**: (maintainer)
**Context**: Local long-term memory system on M5 Max / 128 GB / Claude Code 4-profile setup
**Supersedes**: none
**Related**: `~/Developer/<personal-skill-library>/_archive/memory-system-design/INSPIRED-BY-claude-mem.md` (design)

## TL;DR

After深度调研业界数据 + 实测当前系统使用模式，决定按 4 phase 实施记忆系统升级。**核心收益估算 ~$1,370/年 token 省钱 + 中文检索 +15-30% recall + 4-profile 工作流跨对话连续性自动化**。每个 Phase 独立可验收。

## Decision

| Phase | 改动 | 决策 | 理由 |
|---|---|---|---|
| 0 | ADR 留档 | Do | 决策追溯 |
| 1 | P0+P3: Progressive disclosure (search 三档 + timeline + get_observations) | Do | 11-18x token saving (claude-mem 官方) + 防 auto-compact |
| 2 | P1: BM25+dense hybrid retrieval w/ RRF fusion | Do | +15-30% recall (2026 业界 benchmark) + 你的 50% 中英混合 + 命名敏感场景 |
| 3 | P2: SessionStart hook 但是**条件触发**（非 claude-mem 的无条件版） | Do | 防注入污染 + 针对 4 profile 多项目切换工作流 |
| 4 | 全库重 mine 让 summary metadata 覆盖率 0.8% → ~100% | Do | 给 Phase 2 hybrid 充足 BM25 检索源 |
| — | 跨设备 sync / 跨 agent (mem0 / memsearch 路径) | Don't | 偏离场景 + 增加复杂度 |

## Context (你的真实数据)

| 维度 | 实测 | 含义 |
|---|---|---|
| 总 chunks | 5,782 | 已成规模 |
| global wing | 85.8% (4959) | global 几乎是"全库搜"；wing 隔离主要保护小项目（<project-a>/<project-e>/...） |
| 中文 chunks | 50.5% | 双语场景，hybrid 有用 |
| 平均 chunk | 712 char (p95=895) | chunking 设置 (800+100 overlap) 合理 |
| Summary metadata | 0.8% | 历史 chunks 99% 无 summary → Phase 4 修 |
| Per-query cost | ~1750 tokens × ~200 次/天 | $1370/年 输入 token 浪费 (Claude Opus $15/M) |

## Phase 1: Progressive Disclosure

### 改动文件
- `~/.claude/scripts/memory_search.py` — 加 `detail_level: index|summary|full`, 默认 `index`
- `~/.claude/scripts/memory_mcp_server.py` — 加 2 个新工具 + search schema 加参数
- `~/.claude/scripts/memory_timeline.py` (新) — date-window query
- 17-case test 跑通确认 backward compat

### Schema 设计
```python
# memory_search(query, wing=None, n=10, detail_level="index")
# detail_level:
#   "index"   : 50-100 tok/result, fields: id, source_file, score, snippet(80c), summary_short
#   "summary" : 200-300 tok/result, fields: + full_summary, mtime
#   "full"    : 700+ tok/result (current default), fields: + full text (backward compat)

# memory_timeline(wing=None, start_date=None, end_date=None)
#   → [{date, source_file, summary, chunk_ids[]}]

# memory_get_observations(ids: list[str])
#   → 按 ID 批量取完整 chunk
```

### Backward compat
- mp-search CLI 默认 detail_level="full" 保留命令行旧行为
- MCP `memory_search` 默认 detail_level="index"（Claude 调用走新路径省 token）

## Phase 2: BM25+Dense Hybrid

### 设计
- ChromaDB 1.5.7 已内置 fulltext_search 表（chroma.sqlite3 schema 含 embedding_fulltext_search）
- 路径 A: dense query (current) → top 50
- 路径 B: chromadb fulltext (BM25-style) → top 50
- Fusion: Reciprocal Rank Fusion (RRF), k=60 业界默认
- Re-rank (existing bge-reranker) top 50 → top n

### 改动文件
- `~/.claude/scripts/memory_search.py` — 加 `hybrid: bool = False` 参数，opt-in 一周稳定后转默认
- 实测 RRF 后召回率提升再决定是否默认开

## Phase 3: Conditional SessionStart

### 不抄 claude-mem 的无条件注入

**Claude-mem 做法**：每次 SessionStart → 无条件查最近 conversation → additionalContext 注入到上下文。问题：Claude **不能 deprioritize** 这些内容；如果质量不高 → 每个对话都被污染。

**针对你工作流的改造**——条件触发：

```python
# memory-session-start.py
trigger = False
cwd = os.getcwd()
project_match = re.match(r"^${USER_WORKSPACE}/([^/]+)", cwd)

if project_match:
    project_name = project_match.group(1)
    # 条件 1: 该项目上次对话 > 24h
    last_session_path = f"~/.claude/state/last-session-{project_name}.txt"
    last_session = read_timestamp(last_session_path)
    if (now - last_session).total_seconds() > 86400:
        trigger = True

# 条件 2: 项目目录有 tasks/current-task.md → 注入它 + 该项目最近 conversation-log
if os.path.exists(f"{cwd}/tasks/current-task.md"):
    trigger = True

if trigger:
    # 注入：最近 1 篇 conversation-log + tasks/current-task.md (如有) + top 3 当前项目 memory
    inject_minimal_context()
else:
    # 不注入，保护对话开端清白
    pass
```

### 改动文件
- `~/.claude/hooks/memory-session-start.py` (新)
- `~/.claude/settings.json` — 加 SessionStart 段

## Phase 3 实施结果 (2026-05-26)

**状态**: Done

**最终落地形态**（与原 plan 对比微调）:
- 触发器从"上次对话 > 24h"简化为更可观察的 3 条件 (OR 关系):
  1. cwd 在 `~/Developer/<project>/` 且有 fresh conversation-log (<14d)
  2. cwd 有 `tasks/current-task.md`
  3. cwd 有 fresh `docs/HANDOFF.md` (<7d)
- 不引入 `state/last-session-*.txt` 单独状态机 — 改用文件 mtime 作为新鲜度信号（少一个数据源 = 少一个 bug 面）
- 注入预算：TOTAL_BUDGET=6000 chars (Claude Code 10k 限的 60%，留 40% 安全余量)
- Per-section cap=2000 chars，避免单文件爆掉
- Fail-silent：任何异常 → 空注入 + 日志，不阻塞 session 启动

**改动文件**:
- `~/.claude/hooks/memory-session-start.py` (新, ~150 LOC, +x)
- `~/.claude/settings.json` 加 `hooks.SessionStart` 段，timeout=2s

**验证**:
- 4 个 cwd 场景：<project-a>=6018 chars / <project-f>=4202 chars / /tmp=empty / _archive=empty — 全通过
- 端到端 stdout JSON 合法
- 触发耗时 0.02s × 3 trials (远低于 500ms 目标)
- mp-health 全栈通过：5 launchd + 2 endpoints + chromadb 5803 chunks + 16 symlinks + log freshness 全 OK
- 新 hook 跟既有 6 hook (PreToolUse Edit/Bash + PostToolUse Edit) 互不冲突

**日志位置**: `~/.mempalace-zh/logs/session-start.log`

## Phase 4: Full Corpus Re-mine

### 必要性
- 当前 5782 chunks 中只有 0.8% 有 summary metadata（只是 Phase 2 hook 部署后新写的）
- Phase 2 hybrid 用 summary 作 BM25 sparse 源会更准
- 也补齐 LLM 助手的"摘要-检索"全链路

### 操作
```bash
# 后台跑（不卡用户）
nohup bash -c "
for d in ~/.claude/skills ~/.claude/projects/-user/memory \
         ~/.claude/projects/-user-workspace-*/memory; do
  mp-mine \"\$d\" --llm-summarize --force --quiet 2>&1 | tail -5
done
" > ~/.mempalace-zh/logs/full-remine.log 2>&1 &
# 预计 ~125 分钟（5782 chunks × 1.3s/chunk LLM summarize / 60s）
```

### 风险
- ChromaDB 大量写入 — fcntl 锁会自动序列化 Tier 2 hook + Tier 1 batch + 这个重 mine
- 21 GB mlx-llm daemon 持续推理 ~2 小时高 CPU
- bge-m3 也持续 embed → mempalace daemon 也忙

→ 推荐**晚上跑**或者**周末**跑，期间不影响日常使用（fcntl 锁会让 mp-mine 串行化）

## Phase 4 实施结果 (2026-05-26)

**状态**: Done — 覆盖率从 1.0% → **100.0%** (5504/5504), 超出 95% 目标

### 实施过程 (vs 原 plan 偏离)

原 plan 假设 `mp-mine --llm-summarize --force` 单刀直入跑全库 ~4.5 hr 即可达成。实际遇到 3 个独立根因 (问题分析铁律走完 5 步发现的):

**根因 1**: `~/Developer/ClaudeCodeSkills/` 被重命名为 `<personal-skill-library>/`. mp-mine 走符号链接定位新路径建了 4572 新 chunks (有 summary), 老路径 4925 chunks 永远 orphan 在 chromadb (因为 store_chunks 用 wing+source_file 做 dedup, 路径不同 = 不同 chunk).

**根因 2**: 22 per-project wings 共 836 chunks 是从 <external-backup-volume> 同步过来的, 源文件从未在本机出现, mp-mine 无法 re-process.

**根因 3**: mp-mine 架构限制 — 无"按内容 hash dedup"机制, 也无"orphan chunk 检测/清理"机制. 源路径变 / 源消失时孤儿永久滞留. (留 Parking Lot)

### 解决方案

**Plan A — 删除 renamed dir 孤儿**:
- 抽查 415 个 common files: 385 (92.8%) MD5 100% 同 (纯 rename), 28 内容 drift, 22 仅老路径(已删文件), 2 chunk-count drift
- 决策: 全删 4925 老 chunks (chromadb 是检索当前 canonical state 用的, 不是版本归档; git 管历史)
- 备份: `~/.mempalace-zh/audit/orphan-chunks-dump-20260526-231404.jsonl` (5.4 MB), + chromadb.before-llm-remine-20260526-2000/
- 验证: chromadb 10382 → 5457, 0 OLD-path chunks 残留

**Plan B — 新增工具 `summarize_in_chromadb.py`**:
- 直接读 chunk text from chromadb, 调 memory_llm_assist.summarize_chunk, 用 col.update 写回 metadata (不依赖源文件)
- 跑 857 chunks 17.5 min (0.87 chunks/sec, mlx-llm warm 模式比预估 2.85s/chunk 快 3x)
- success=832, fallback=25 (其中过滤器误杀 — "无法"字样是合法摘要内容)
- 修补脚本再过滤误杀的 55 chunks (65s 全过)

### Final State

- TOTAL chunks: **5504**, with summary: **5504** = **100.0% coverage**
- 全 23 wings 全 100%
- mp-search 3 query smoke 通过 (部署铁律 / OAuth deploy / memory system) — 全部命中相关结果, 无老路径污染
- mp-health 全栈绿

### 新增/改动文件

- `~/.claude/scripts/check_summary_coverage.py` (新, ~70 LOC) — 每月可跑的覆盖率审计
- `~/.claude/scripts/full-remine.sh` (新) — 全库重 mine 编排, share lock 跟 hourly refresh
- `~/.claude/scripts/full-remine-resume.sh` (新) — 父进程意外死亡时 step 2+3 续跑
- `~/.claude/scripts/summarize_in_chromadb.py` (新) — Plan B 直接 chromadb 内 summarize, 不依赖源文件
- `~/.mempalace-zh/audit/orphan-chunks-dump-20260526-231404.jsonl` (5.4 MB) — 删除前快照

### 教训沉淀

1. **跨机器同步 chromadb 必然产生 orphan chunks** — 源文件不在本机时 mp-mine 无能为力. 需要"in-chromadb"工具补
2. **目录 rename 不能依赖符号链接修复** — store_chunks 的 dedup 按完整路径 hash, 符号链接解析后路径不同就是不同 chunk
3. **--quiet 在 4-hour 任务里把进度藏没了** — 应该改成 progress 每个文件一行, 不是完全 silent
4. **Claude Code harness `run_in_background` 不能跨 harness 重启** — 长任务必须 `subprocess.Popen(start_new_session=True)` 真 detach
5. **过滤器不能用"任意短语黑名单"** — "无法" 在中文里很常见, 不一定是 LLM 失败信号

## Rollback Plan

每个 Phase 独立可回滚：
- Phase 1: 删 `memory_timeline.py` + 还原 search.py + restart MCP server (claude mcp remove + add)
- Phase 2: 改 `hybrid: True` → `False` 默认或删 hybrid branch
- Phase 3: 删 SessionStart 段 from settings.json + 删 hook script
- Phase 4: 不可回滚（数据已写入）— 但 backup-memory.sh 已经每周备份 chromadb，理论上能从备份回滚

## Success Metrics（部署后 1 周观察）

- Phase 1: 平均 mp-search 返回 token < 600 (现 1750) → 65%+ saving
- Phase 2: 测试集召回率提升 ≥ 15%
- Phase 3: SessionStart hook 触发率（看日志），实际有用的注入比例 ≥ 50%（不是噪音）
- Phase 4: `mp-status` 看到 summary 覆盖率 ≥ 95%

## References

- INSPIRED-BY 文档: `~/Developer/<personal-skill-library>/_archive/memory-system-design/INSPIRED-BY-claude-mem.md`
- Claude-mem progressive disclosure 官方: https://docs.claude-mem.ai/progressive-disclosure
- Hybrid Search BM25 + Dense: https://mbrenndoerfer.com/writing/hybrid-search-bm25-dense-retrieval-fusion
- ChromaDB fulltext search: 1.5.7 schema 已有 `embedding_fulltext_search` 等表
- RRF (Reciprocal Rank Fusion): Cormack et al. 2009
