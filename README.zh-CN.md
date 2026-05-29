<div align="center">

# WayPalace

**给 AI 编程助手的本地长期记忆**

中文检索优化 · 零外发遥测设计

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#状态)
[![CI](https://github.com/xcodethink/WayPalace/actions/workflows/test.yml/badge.svg)](https://github.com/xcodethink/WayPalace/actions/workflows/test.yml)

**[English](README.md) · [中文](README.zh-CN.md)**

</div>

---

## 这些痛点你一定遇到过

每次跟 AI 编程助手开新对话, 都是从零开始:

- 周一早上花 20 分钟跟它介绍项目结构, 这周第三次了.
- 4 个月前修过的 Cloud Run / OAuth / Cloudflare 问题, 这次又踩一遍.
- 担心粘贴项目 A 的密钥到项目 B 的对话里 — 更怕它"顺手"帮你泄露.
- 你认真记的 memory 笔记... 真要找时永远想不起来在哪个文件.

现有方案各有问题:

- **mem0 / Letta** 默认上云, 你的代码 / 项目结构 / 密钥要交给第三方.
- **向量数据库** (Pinecone / Weaviate / Qdrant) 是存储, 不是记忆管理 — 索引由你自己组织.
- **手写笔记** 没人检索 — 写了不用等于没写.
- **claude-mem** 思路好, 但英文优先, 没有项目命名空间生命周期管理.

## WayPalace 提供的能力

一个面向 AI 编程助手的**本地优先记忆层**.

### 你写下的, 它自动记住

当 Claude Code 写了 memory 文件 (`feedback_*.md` 决策、`project_*.md` 笔记 等), WayPalace 通过 PostToolUse hook 在约 20 秒内自动入库. 不需要调 `client.add()`, 不需要做分类规划.

> 今天你写下学到的经验. 下个月不同项目踩同样的坑, WayPalace 在你问 AI 之前就把上下文捞回来了.

### 项目密钥, 物理隔离不串

项目 A 的 GCP project ID / API key / 域名, 不能进项目 B 的记忆. PreToolUse 阶段的 cross-project guard 物理拦截 — 不是软过滤, 是真不让写.

> 不是软过滤 — 是真拦截. 设计的泄露测试场景 100% 命中.

### 中文检索优化 (英文也兼顾)

检索管道是 bge-m3 dense + bge-m3 sparse + RRF fusion + bge-reranker. 12 个中文查询的 golden set 上, recall@10 是 **92%**, 跟 mem0 在英文 LOCOMO 上的表现持平.

> bge-m3 是目前唯一对中英文都是一线水平的主流嵌入模型. WayPalace 围绕这个事实搭建.

### 100% 本地

你的记忆数据存在你硬盘上. 无账号, 无注册, 无配额. `mp-metrics-summary` 让你清楚看到系统里发生的每件事, 全部基于本地 JSONL 文件.

> 隐私是契约本身, 不是个能切换的开关.

### 自治运行

6 个 launchd daemon、4 个每小时跑的对账器、30 个 pytest case, 让系统 7×24 自治运行, 不需要人工介入.

> 装一次. 然后忘了它的存在. 直到你查询.

## 工作原理

```
   ┌──────────────────────────────────────────────────┐
   │ Claude Code · Cursor · Codex (你的 AI 工具)       │
   └──┬─────────────────────────────────┬─────────────┘
      │ MCP / CLI                       │ Hooks (可选)
      ▼                                 ▼
   ┌─────────────┐                ┌────────────────────┐
   │ mp-* CLI    │                │ auto-mine hook     │
   │  search     │                │ auto-surface hook  │
   │  mine       │                │ session-start hook │
   └──────┬──────┘                └──────────┬─────────┘
          │ Unix socket                       │ Detached spawn
          ▼                                   ▼
   ┌──────────────────────────────────────────────────┐
   │ memory daemon (常驻预热, launchd 托管)            │
   │   bge-m3 dense + sparse + RRF + bge-reranker     │
   │   时效加权 · cross-project 过滤                   │
   └──┬──────────────────┬──────────────────┬─────────┘
      ▼                  ▼                  ▼
   ┌──────────┐    ┌──────────────┐    ┌────────────────┐
   │ChromaDB  │↔   │Sparse store  │↔   │Namespace meta  │
   │HNSW dense│    │SQLite + bge  │    │SQLite, 4 档    │
   └──────────┘    └──────────────┘    └────────────────┘
```

三层架构:

1. **入库.** PostToolUse hook、每小时批量任务、手动 `mp-mine` 收口到同一个 `store_chunks` 路径.
2. **检索.** Query → daemon socket → dense + (可选 sparse + RRF) → bge-reranker → 时效加权 → 返回.
3. **生命周期.** Namespace 自动归类成 `active` / `dormant` / `stale` / `orphan`, 含资产存在性 override 判定.

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 和 [docs/decisions/](docs/decisions/) 设计决策记录.

## 实际场景

### 场景 1 — 用 4 个月前的经验避免今天的坑

**没装 WayPalace:**

```
你: gcloud run deploy --port=8080 --healthcheck-path=/healthz
[部署完, 健康检查返回 404]
你: ...等等, 这个我以前是不是遇到过?
[翻 Stack Overflow + grep 老项目 30 分钟]
你: 哦对. Cloud Run 在 GFE 层拦截 /healthz 直接返回 404.
```

**装了 WayPalace** — PreToolUse hook 匹配到 4 个月前的记忆 (相似度 0.78):

```
你: gcloud run deploy --port=8080 --healthcheck-path=/healthz

WayPalace 从你的历史里找到:
  "Cloud Run 在 GFE 层拦截 /healthz 直接返回 404, 不转给容器.
   用 /api/health 之类的路径代替.
   [解决于 2026-01-18, 项目 ProjectA]"

你: 哦对. --healthcheck-path=/api/health
```

### 场景 2 — 周一早上的上下文恢复

你手上 5 个以上项目. 每个周一早上花 20 分钟跟 Claude Code 介绍周五在做什么.

**没装 WayPalace:**

```
你: 帮我看下 ProjectA 的 OAuth 流程.
AI: 好, 你们用的是哪个 auth 库?
你: <重新解释 5 分钟>
AI: 好, 默认配置是...
你: 不对, 我们用了 override. 让我找下文档...
```

**装了 WayPalace** — SessionStart hook 检测到 cwd 在项目目录, 自动注入新鲜上下文:

```
[~/Developer/ProjectA 下开新对话]
[SessionStart 检测到 current-task.md + 最近 conversation-log + HANDOFF.md]

你: 帮我看下 ProjectA 的 OAuth 流程.
AI: 从你的 current task 和最近决策看到, 你们用 NextAuth.js + Cloudflare
    Workers callback override. 上周你记了 NEXTAUTH_URL 必须跟部署域名一致,
    否则 callback 会静默失败. 你现在卡在哪一步?
你: 对. 我现在要加 refresh-token 流程...
```

### 场景 3 — 跨项目知识迁移

某个经验在项目 A 学到, 但其实项目 B / C / D 都用得上.

WayPalace 的 `global` namespace 装跨项目通用的经验 (部署铁律、debug 模式、语言坑). 项目专属 namespace 装项目特定的 (本项目密钥、本项目惯用法).

查询时:

- 在项目 A 目录里查, WayPalace 检索 `projectA + global` — 项目特定加跨项目通用.
- 显式跨项目查 (`mp-search-all`), 全量检索, 但提示你跨 namespace 的风险.

入库时自动分类的 LLM 决定新经验进哪个 namespace. 大约 1% 的情况会分错, 可以用 `mp-wing-archive` 归档, 在正确 namespace 写一份回去.

## WayPalace 跟其他方案对比

| 能力 | WayPalace | mem0 | Letta | claude-mem |
|---|---|---|---|---|
| 本地优先 / 零外发遥测 | 是, by design | 可选 (默认上云) | 自部署 | 是 |
| 中文检索优化 | 是 (bge-m3 + RRF + reranker) | 英文优先 | 英文优先 | 英文优先 |
| 多信号 namespace 生命周期 | 是 (4 档 + 资产存在性 override) | 否 | 否 | 否 |
| 跨项目密钥防泄露 | 是 (物理 hook + sensitive dict) | 否 (只做路由) | 否 | 否 |
| 写文件自动入库 | 是 (PostToolUse hook) | 否 (手动 `add()`) | LLM 自反思 | 否 |
| Progressive disclosure (3 档) | 是 | 否 | 否 | 是 (参考来源) |
| Hybrid 检索 (dense + sparse + RRF) | 是 | 是 | — | — |
| 公开 ADR 设计决策 | 是 (D001-D004) | — | — | — |

WayPalace 不冲着 mem0 / Letta 在它们擅长的场景去 (云端 SaaS、agent 自反思编辑). 它合适的位置是: 你想要**本地优先、中文友好、设置一次忘掉**的 AI 编程辅助记忆.

## 开始用

按你的硬件选档.

### Tier 0 — 任何机器都能跑 (无 LLM)

快速检索, 不要自动分类 / 摘要. Python 3.11+ · 4 GB RAM 即可.

```bash
git clone https://github.com/xcodethink/WayPalace.git
cd WayPalace
bash install.sh
```

### Tier 1 — 小型本地 LLM

想要自动分类 + 摘要, 但机器一般. 约 8 GB RAM.

```bash
bash install.sh --tier=small
```

### Tier 2 — 全本地最强 (Mac 64 GB 或以上推荐)

完整体验: Apple MLX 跑 Qwen3.6-35B 做细致分类和摘要. 推荐 Apple Silicon.

```bash
bash install.sh --tier=mlx
```

### Tier 3 — 自带 API

用 OpenAI / Anthropic / Groq / 任何 OpenAI-compatible 端点.

```bash
bash install.sh --tier=external
```

### 首次 mine 和 search

```bash
source $HOME/.waypalace/venv/bin/activate
mp-mine /path/to/your/notes/directory --namespace global
mp-search "你的查询"
```

### 可选: Claude Code 集成

三个 hook (`auto-mine`、`auto-surface`、`session-start`) 让 WayPalace 真正好用. 见 [docs/INSTALL.md § Claude Code hooks](docs/INSTALL.md).

## 性能指标 (摘要)

Apple Silicon · 约 8000 chunks · 23 个 namespace (方法论和完整数据见 [docs/BENCHMARKS.md](docs/BENCHMARKS.md)):

| 指标 | WayPalace | 行业参考 |
|---|---|---|
| Recall@10 (中文 golden set) | **92%** | mem0 LOCOMO 67-92% (英文) |
| Precision@1 | 50% | top-3 reach 显著更高 |
| 单 chunk token 节省 (`index` vs `full`) | **约 12.5×** | claude-mem 宣称 11-18× |
| Search p50 latency (warm daemon) | **467 ms** | mem0 LOCOMO 710 ms / mem0g 1090 ms |
| Hybrid 检索 top-1 差异化 | 8 个 case 中 3 个 | dense-only baseline |
| 跨项目防泄露 | **100%** 拦截 | 业界无对标 feature |
| pytest | **30/30** in 约 31s | — |

这些数据来自一台机器一份数据集. 本地复现: `python -m pytest tests/` 和 `python waypalace/hybrid_benchmark.py`.

## WayPalace **不是**什么

> [!IMPORTANT]
> 以下都是有意的非目标或已知限制. 清晰设定期望本身就是契约的一部分.

- **不是云端 SaaS.** 想要托管记忆加 web dashboard, 用 mem0 或 Letta — 它们更专业.
- **不是多租户.** 单机单用户设计. 团队场景不适合.
- **不是生产级 SLA agent 用.** Alpha 质量. API 可能改.
- **没在 Linux 测过.** Daemon 代码是 portable 的, 但 launchd 是 macOS only. systemd 模板已有但未实测. 欢迎 PR.
- **不是替代项目文档.** WayPalace 是文档的补充, 不是替代.
- **没有 web UI.** 只有 CLI 和 MCP.

## 文档

- [docs/INSTALL.md](docs/INSTALL.md) — 安装、launchd daemon、Claude Code hooks
- [docs/USAGE.md](docs/USAGE.md) — CLI 参考 (`mp-search`、`mp-mine`、`mp-wings-review`、...)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 系统架构
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — 完整方法论和结果
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — 怎么贡献
- [docs/decisions/](docs/decisions/) — 架构决策记录 (D001-D004)
- [ROADMAP.md](ROADMAP.md) — 计划做什么, 明确**不**做什么

> [!NOTE]
> 文档详情目前为英文版本. 中文翻译已加入 [ROADMAP.md](ROADMAP.md).

## 社区

- [Issues](https://github.com/xcodethink/WayPalace/issues) — bug、功能请求、提问
- [Discussions](https://github.com/xcodethink/WayPalace/discussions) — 设计讨论、show-and-tell
- 欢迎 Pull Request — 见 [CONTRIBUTING.md](docs/CONTRIBUTING.md)

## 状态

**Alpha (v0.1.0).** 推荐给以下早期采用者:

- 想试试本地优先的 agent 记忆
- 用 Claude Code、Cursor、或类似 AI 编程工具
- 用 macOS (Linux 计划中), 16 GB 或以上 RAM
- 习惯命令行工作流

## 致谢

- [ChromaDB](https://www.trychroma.com/) — 向量数据库
- [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) — 让中文检索达到一线水平的嵌入模型
- [BAAI/bge-reranker](https://huggingface.co/BAAI/bge-reranker-v2-m3) — cross-encoder reranker
- [Qwen](https://github.com/QwenLM/Qwen3) 团队 — 分类用 LLM
- [MLX](https://github.com/ml-explore/mlx) 团队 — Apple Silicon 推理
- [claude-mem](https://github.com/thedotmack/claude-mem) — progressive disclosure 模式的灵感来源
- Anthropic Claude Code 团队 — hooks API

## License

[MIT](LICENSE)
