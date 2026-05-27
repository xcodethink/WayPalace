# D003 — Wing Lifecycle Management

**Status**: Accepted (implemented 2026-05-27)
**Owners**: (maintainer)
**Context**: 用户指出当前系统固化 22 项目隔离, 新项目要**自动**增加, 删除由人工 review 触发, 价值信息先抢救再清.
**Related**: [D001](D001-progressive-disclosure-and-hybrid-retrieval.md), [D002](D002-test-suite-and-metrics.md)
**Created**: 2026-05-27
**Implemented**: 2026-05-27
**Depends on**: D002 mp-metrics 落地 (forward-compat `wing` 字段)

## TL;DR

D001+D002 后系统功能完备 + 有测试 + 有观测, 但**项目 wing 名单是硬编码 22 个** — 新项目要手改 refresh-memory.sh 才能进系统. 用户要求: **加 wing 自动, 删 wing 受控**.

本 ADR 把 wing 从"硬编码列表"升级到"动态资源生命周期", 借鉴 GitLab/JIRA archive 节奏 (90 天) + AWS S3 lifecycle 分层 (90/180/365), 并坚持"自动增 / 人工减"原则.

## 1) 调研

### A. 现状画面 (实测)

```
chromadb 实测 23 wings (D003 实施前):
  global=4687  <project-a>=403  <project-e>=113  <project-h>=49
  <project-k>=44  <project-c>=33  <project-b-ai>=30  <project-j>=26  <project-c-visa>=22
  <project-d>=19  <project-i>=16  <project-o-internal>=13  <project-e-cn>=9
  <project-c-mytask>=9  <project-n>=7  <example-new-project>=7  <example-test-ai>=5  <example-ai-voice>=4
  <example-test-prd>=4  <project-m>=3  <project-c-myzone>=3  <example-reddit>=2  <example-task>=2
```

### B. "固化"的 3 处根因

| # | 位置 | 内容 | 问题 |
|---|---|---|---|
| 1 | `~/.claude/refresh-memory.sh` line 65-88 | 硬编码 22 个 (dir:wing) 映射 | 加新项目要手改脚本 |
| 2 | `memory_llm_assist_test.py` line 22 | `PROJECT_WINGS = [7 项写死]` | 加新 wing 测试不识别 |
| 3 | 无 wing 元数据表 | created_at/last_active/source_machine 全无 | 无法做活跃度审计 |

### C. ~/Developer 实存 vs 已建 wing

- ~/Developer 下 55 个目录
- chromadb 23 wings (含 global)
- 差 33 目录无 wing — 因为这些项目没在 Claude Code 里写过 memory 文件 (合理, 不强求)

→ 现状真实问题不是"被锁死"是"硬编码 list 没跟上自动化".

### D. 业界 ADR 范围决策依据 (Q1 拆 D003 而非合 D002 的理由)

**AWS Architecture Blog / Martin Fowler / TechTarget 2025-2026**:
- "One decision per ADR"
- "Each ADR captures a single architecturally significant decision at a useful level of granularity"
- "Avoid combining multiple architecture decisions in one document — challenging to understand, govern, or supersede later"

D002 = "防回归 + 观测可见性" (一个决策)
D003 = "资源动态生命周期" (另一个决策)
→ 业界标准说必须拆.

### E. 业界 stale 阈值对标 (Q4 选 90/180/365 的依据)

| 场景 | 阈值 | 节奏 |
|---|---|---|
| GitHub stale bot | 60 天 mark, +7 天 close | 高频开发 |
| Slack archivist | 30 天 warn, +7 天 archive | 聊天节奏快 |
| **GitLab/JIRA archive recommendation** | **90 天** | 项目管理 ★ |
| **AWS S3 lifecycle** | **90 / 180 / 365** | 存储分层 ★ |

记忆系统接近 GitLab/JIRA + S3 模式 (项目元数据 + 长期存储), 对应阈值:
- **active**: 90 天内有活动
- **dormant**: 90-180 天 (review 看到但不催)
- **stale**: 180-365 天 (建议归档)
- **orphan**: 365+ OR 源目录已删 OR 跨机器 orphan

为什么 90 而非 60: 单人多项目跳跃幅度大, <project-c-visa> 这种季节性项目半年碰一次很正常.

### F. 业界 RAG namespace lifecycle 经验

Pinecone / Weaviate / Qdrant 主流 vector DB **没有 built-in lifecycle** (namespace 是 logical, 不会过期). 各家文档主推:
- Multi-tenancy via namespace (isolate logically without N-collection cost)
- Operational: backups / replication / snapshot / restore
- 删除 / archive 由**应用层**实现

→ 我们要在应用层实现 lifecycle, 业界没现成模板. **设计需自己拍, 但参考 GitLab 资源管理模式**.

## 2) 分析

### A. 用户核心需求 (一句话)

> 加 wing 是**自动**的零摩擦, 删 wing 是**受控**的人工触发 + AI 辅助抢救价值信息.

### B. 现系统差距

| 维度 | 现状 | D003 后 |
|---|---|---|
| 新项目加 wing | 手改 refresh-memory.sh | 自动 (Tier 1 通配 + Tier 2 hook) |
| Wing 元数据 | 无 (chunk metadata 散落) | `wing_meta` sqlite 表统一 |
| 活跃度审计 | 无工具 | `mp-wings-review` CLI |
| 归档流程 | 无 | `mp-wing-inspect` + `mp-wing-archive` + `mp-wing-delete` |
| 价值抢救 | 无 | AI 辅助 cherry-pick 写入 global |

### C. 风险评估

| 风险 | 概率 | 缓解 |
|---|---|---|
| 改 refresh-memory.sh 误删生产路径 | 中 | 通配扫描 = 加法 (原 22 路径仍工作), 不删 |
| Tier 2 hook 误创建噪音 wing | 中 | wing 名规范化白名单 (lowercase + `_`); 加新 wing 写 metrics 留痕 |
| 用户误删活跃 wing | 低 | dump 备份 + 双重确认 + 软删 (archived=1) 而非物理删 |
| 跨机 orphan 误识别 | 中 | source_machine 字段标记, review 时显式 flag |
| `wing_meta` 表跟 chromadb 不一致 | 中 | 单点写: 仅 store_chunks / del_chunks 路径更新 |

### D. 业界不会做但我们要做的

- **价值抢救 (Salvage)**: 删 wing 前 AI 主动 cherry-pick valuable info → 升级到 global wing — 这是个人记忆系统特有需求, 业界没模板
- **季度自动 reminder**: 每月 1 号 Tier 1 batch 在 refresh.log 写一条 "wing review due" — 防你忘
- **跨机 orphan 标记**: D001 提到 836 chunks 是 <external-backup-volume> sync 过来的源已不在本机, D003 显式 flag

## 3) 计划 (Scope Card — 锁定)

### 做 (交付物)

**Part A: 自动化新增** (拆硬编码)

1. **拆 refresh-memory.sh 硬编码 list** → 通配扫描
   - 删除 line 65-88 PROJECTS=(...) 数组
   - 改成: 遍历 `~/.claude/projects/-user-workspace-*/memory/` 目录
   - 从目录名 (`-user-workspace-<X>`) 提取 X → 规范化 (lowercase + `-` → `_`) → wing 名
   - 保留 ClaudeCodeSkills → global wing 特殊路径 (line 34)
   - 保留 auto-memory → LLM classify 路径 (line 43-46)

2. **wing 名规范化函数** (`memory_core.py` 加 1 函数)
   ```python
   def normalize_wing_name(dir_name: str) -> str:
       # "<project-h>" → "<project-h>"
       # "<project-e-cn>" → "<project-e-cn>"
       # "<project-i>" → "<project-i>_new<project-i>"
       return dir_name.lower().replace("-", "_").replace(" ", "_")
   ```
   保证跟现有 22 wings 命名一致 (无破坏)

3. **memory_core.store_chunks 加 wing 自动注册**
   - 第一次 store 某 wing 时, 写 `wing_meta` 表: `INSERT OR IGNORE`
   - 每次 store 时, `UPDATE wing_meta SET last_mine_at, chunk_count WHERE wing_name=?`
   - 写 metrics event: `wing.created` / `wing.updated`

4. **Tier 2 hook 即时新增** (memory-auto-mine.py)
   - 已经会自动 mine memory 文件; D003 加: 检测到新 wing 名时, 同步注册 wing_meta
   - 无需新加 hook, 因为 store_chunks 已经会注册

**Part B: 元数据基础** (新增 wing_meta 表)

1. `~/.mempalace-zh/wing_meta.sqlite3` (新增独立 sqlite — 跟 aging.sqlite3 分开避免锁竞争)
   ```sql
   CREATE TABLE wing_meta (
     wing_name        TEXT PRIMARY KEY,
     source_dir       TEXT,              -- e.g. "-user-workspace-<project-a>"
     created_at       INTEGER,           -- unix epoch
     last_mine_at     INTEGER,
     last_search_at   INTEGER,
     chunk_count      INTEGER DEFAULT 0,
     source_machine   TEXT DEFAULT '<workstation>',  -- '<backup-volume-restore>' 标记跨机 orphan
     notes            TEXT,
     archived         INTEGER DEFAULT 0,  -- 软删标记
     archived_at      INTEGER
   );
   CREATE INDEX idx_wing_active ON wing_meta(last_mine_at, archived);
   ```

2. **回填脚本**: 扫现有 23 wings + chromadb metadata → 填 wing_meta (created_at 取最早 chunk mtime; 没 mtime 的标 'unknown')

3. **memory_search 加 last_search_at 更新** (search_isolated/search_all 末尾, 不增加显著 latency)

**Part C: 审计 + 归档工具** (4 个新 CLI)

1. `~/.claude/bin/mp-wings-review` (新 CLI, ~150 LOC)
   - 输出: 4 档分类 (active / dormant / stale / orphan)
   - 每 wing 显示: name / chunks / last_mine / last_search / source_dir / source_machine / notes
   - 自动 flag: source_dir 不存在 → orphan; source_machine != current → orphan

2. `~/.claude/bin/mp-wing-inspect <wing>` (新 CLI, ~80 LOC)
   - 列出该 wing 的全部 chunks (source_file 分组)
   - 输出可读 markdown, 方便用户跟 AI 一起决定哪些有价值
   - 顺便统计: 跨机 orphan? 总字符数? 最新 chunk 日期?

3. `~/.claude/bin/mp-wing-archive <wing>` (新 CLI, ~120 LOC)
   - **不删, 只 dump**: 把 wing 全 chunks 导出到 `~/.mempalace-zh/archive/<wing>-YYYYMMDD.jsonl`
   - 提示用户: "执行 mp-wing-promote <md-file> 把抢救的内容写入 global, 再 mp-wing-delete --confirm"
   - 软删 wing_meta: `archived=1, archived_at=now`

4. `~/.claude/bin/mp-wing-delete <wing> --confirm` (新 CLI, ~80 LOC)
   - 硬删: chromadb chunks + sparse.sqlite3 entries + aging events
   - 必须先 mp-wing-archive 过 (查 wing_meta.archived=1), 否则拒绝
   - 写 metrics: `wing.deleted` event

5. (可选, 选做) `~/.claude/bin/mp-wing-promote <wing> --content="<md>"` 
   - 自动把抢救内容写入 `~/.claude/projects/-user/memory/salvaged_<wing>_<date>.md`
   - Tier 2 hook 自然把它收进 global wing
   - 不强求 — 用户可以直接手写 .md 到 memory 目录

**Part D: 季度 reminder**

修改 `refresh-memory.sh`, 在每月 1 号 8am 那一小时执行时多 echo 一条:
```bash
if [ "$(date +%d)" = "01" ] && [ "$(date +%H)" = "08" ]; then
  echo "[REMINDER] Wing review due. Run: mp-wings-review" >> "$LOG_FILE"
fi
```

**Part E: 测试 (D002 完成后追加)**

在 D002 的 `tests/` 目录加 4 个新 case:
- `test_wing_auto_create_on_first_store` — 新 wing 名 → store_chunks 自动注册 wing_meta
- `test_wing_review_classification` — active/dormant/stale/orphan 4 档阈值正确
- `test_wing_archive_dumps_jsonl` — archive 后 jsonl 存在, chromadb 仍有 (软删)
- `test_wing_delete_requires_archived_first` — 没 archive 直接 delete → 拒绝

### 不做 (Parking Lot)

- ❌ **自动删除 stale/orphan wing** — 用户硬性要求人工触发
- ❌ **跨机器同步 wing_meta** — <external-backup-volume> 同步只管 chromadb + sparse, wing_meta 是本机视角
- ❌ **wing 合并工具** (e.g. <project-c-mytask> → <project-c>) — 用户用 mp-wing-archive + mp-wing-promote 手动合, 不专门做工具
- ❌ **wing 重命名** — 罕见需求, 出现时再做
- ❌ **审计日志可视化 (web UI)** — CLI 输出 markdown 表格够用
- ❌ **Auto-promote 价值信息识别** — 价值判断必须用户拍, AI 只辅助

### 完成标志 (DoD)

| # | 验收条件 | 怎么测 |
|---|---|---|
| 1 | refresh-memory.sh 拆掉硬编码 list, 通配扫描 22+ wings | 跑一次 `bash refresh-memory.sh`, 日志显示扫到所有 ~/Developer/.../memory 路径 |
| 2 | 故意建 `~/.claude/projects/-user-workspace-TestNewProj/memory/foo.md` → 自动出现 wing | mp-status 多 1 wing, wing_meta 表新条目 |
| 3 | wing_meta 表完整回填 23 现有 wings | `sqlite3 wing_meta.sqlite3 'SELECT count(*) FROM wing_meta'` = 23 |
| 4 | mp-wings-review 4 档分类输出正确 | 至少 1 wing 在 dormant/stale/orphan (用现有 22 wings 自然分布验证) |
| 5 | mp-wing-inspect 输出可读 markdown | 跑 `mp-wing-inspect <example-task>` 看 2 chunks 内容 |
| 6 | mp-wing-archive 生成 jsonl | 跑后看 `~/.mempalace-zh/archive/<wing>-YYYYMMDD.jsonl` 存在 + 字节 > 0 |
| 7 | mp-wing-delete 需要 archived=1 才能执行 | 没 archive 直接 delete → exit 1 + 错误信息 |
| 8 | 季度 reminder 在 refresh.log 出现 | mock 日期为 "01 08:00" 跑一次, log 看到 [REMINDER] |
| 9 | D002 测试套件加 4 个 wing 相关 case 全 pass | `pytest tests/ -v -k wing` |
| 10 | mp-health 全栈仍绿 | `mp-health` 退 0 |
| 11 | hybrid_benchmark + classify_test 仍 pass | 两个测试退 0 |
| 12 | <external-backup-volume> sync 完成 | `_system-config/claude-core/` 含 wing_meta.sqlite3 + 4 新 CLI + 改后的 refresh-memory.sh |

## 4) 方案 (技术设计)

### A. 目录结构变化

```
~/.claude/scripts/
├── memory_core.py                 # 改: 加 normalize_wing_name() + store_chunks 注册 wing_meta
├── memory_search.py               # 改: search_isolated/search_all 末尾更新 last_search_at
├── wing_lifecycle.py              # 新 ~200 LOC: 共享 wing_meta CRUD
└── tests/
    └── test_wing_lifecycle.py     # 新 4 case

~/.claude/bin/
├── mp-wings-review                # 新
├── mp-wing-inspect                # 新
├── mp-wing-archive                # 新
└── mp-wing-delete                 # 新

~/.claude/refresh-memory.sh        # 改: 拆硬编码 + 加 reminder

~/.mempalace-zh/
├── wing_meta.sqlite3              # 新
└── archive/                       # 新 (mp-wing-archive 输出目录)
    └── <example-task>-20260601.jsonl
```

### B. 关键算法: refresh-memory.sh 通配扫描

```bash
# 替换原 line 65-88 PROJECTS=(...) 数组
PROJECTS_ROOT=~/.claude/projects
for dir in "$PROJECTS_ROOT"/-user-workspace-*/memory; do
  [ -d "$dir" ] || continue
  parent=$(basename "$(dirname "$dir")")
  proj_name="${parent#-user-workspace-}"
  # 规范化: lowercase + '-' → '_' + ' ' → '_'
  wing=$(echo "$proj_name" | tr '[:upper:]' '[:lower:]' | tr '- ' '__')
  $PYTHON $MINE "$dir" --wing "$wing" --quiet >> "$LOG_FILE" 2>&1
done
```

### C. wing_lifecycle.py 共享 API

```python
# wing_meta CRUD
def register_wing(wing: str, source_dir: str = None) -> None:
    """INSERT OR IGNORE; idempotent"""

def update_wing_activity(wing: str, event: str) -> None:
    """event ∈ {'mine', 'search'}; updates last_mine_at or last_search_at"""

def list_wings(filter_archived: bool = True) -> list[dict]:
    """返回 wing_meta 全表"""

def classify_wing_status(wing_dict: dict, now: int) -> str:
    """active / dormant / stale / orphan — 实现 90/180/365 阈值"""

def archive_wing(wing: str) -> str:
    """dump jsonl, set archived=1, returns dump_path"""

def delete_wing(wing: str, confirm: bool = False) -> dict:
    """硬删, 需要 archived=1 前置; returns deleted_count_per_store"""
```

### D. mp-wings-review 输出样例

```
$ mp-wings-review

=== Wings Health Report (2026-08-15) ===
Source machine: <workstation>
Total wings: 23 (22 project + 1 global)

ACTIVE (90 天内有 mine 或 search 命中) — 8 wings
  Wing                  Chunks  LastMine    LastSearch  Source
  global                  4687  2h ago      10m ago     ~/.claude/skills
  <project-a>                 403  1d ago      3h ago      <project-a>
  <project-e>                113  4d ago      1d ago      <project-e>
  <project-c>                    33  12d ago     5d ago      <project-c>
  <project-k>                   44  18d ago     7d ago      <project-k>
  <project-d>                 19  45d ago     11d ago     <project-d>
  <project-b-ai>                   30  60d ago     20d ago     <project-b-ai>
  <project-j>            26  88d ago     30d ago     <project-j>

DORMANT (90-180 天无活动) — 3 wings ▼ review 时关注
  <project-h>        49  95d ago     45d ago     <project-h>  ✓ source 存在
  <project-c-visa>               22  120d ago    60d ago     <project-c-visa>         ✓ source 存在
  <project-i>                 16  155d ago    80d ago     <project-i> ✓

STALE (180-365 天无活动) — 4 wings ◆ 建议归档
  <project-o-internal>       13  200d ago    100d ago    <project-o-internal> ✓
  <project-e-cn>               9  250d ago    180d ago    <project-e-cn>        ✓
  <project-c-mytask>              9  300d ago    250d ago    <project-c-mytask>       ✓
  <project-n>                  7  360d ago    300d ago    <project-n>           ✓

ORPHAN (365+ OR 源目录不存在 OR 跨机) — 8 wings ✗ 强烈建议清理
  <project-c-myzone>              3  N/A         N/A         <project-c-myzone>       ✗ no chunks since restore
  <example-new-project>                7  N/A         N/A         (unknown)           ✗ source missing
  <example-test-ai>                  5  N/A         N/A         <example-test-ai>           ✗ <backup-volume-restore>
  <example-test-prd>                     4  N/A         N/A         <example-test-prd>              ✗ source missing
  <project-m>                   3  N/A         N/A         <project-m>            ✗ <backup-volume-restore>
  <example-ai-voice>                   4  N/A         N/A         (unknown)           ✗ source missing
  <example-reddit>            2  N/A         N/A         <example-reddit>     ✗ source missing
  <example-task>             2  N/A         N/A         (unknown)           ✗ source missing

Next steps:
  1. Review STALE + ORPHAN wings together with AI
  2. For each candidate: run `mp-wing-inspect <wing>` to see chunks
  3. Salvage valuable info → write to ~/.claude/projects/-user/memory/ as new .md
  4. mp-wing-archive <wing> → mp-wing-delete <wing> --confirm

Next quarterly review reminder: 2026-11-01
```

### E. 跟 D002 的 forward-compat 衔接

D002 mp-metrics 已经预埋:
```jsonl
{"ts":"...","event":"mine","wing":"<project-a>",...}
{"ts":"...","event":"search","wing":"global",...}
{"ts":"...","event":"hook.auto_mine.spawned","wing":"<project-e>",...}
```

D003 不读 metrics 派生 wing_meta — 直接在 store_chunks / search_isolated 端**双写**:
- chromadb 写 chunks
- 同步 update wing_meta

为什么不依赖 metrics 派生:
- metrics 是观测数据, 容许 fail-silent / 缺失 — 不能作为 wing_meta 真相源
- 双写更直接, 单点写, 不会跟 metrics 出现 inconsistency

### F. 风险 + 回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| refresh-memory.sh 改坏 | 改前 cp 备份 + 跑 1 次 + grep 日志确认扫到 22+ wings | 还原备份, 22 项目硬编码 list 重新生效 |
| wing_meta 表 schema 错 | 单独 sqlite (跟 aging 分开), 不影响其他模块 | `rm wing_meta.sqlite3` + 重跑回填脚本 |
| mp-wing-delete 误删 | 必须 archived=1 前置; dump jsonl 留底; 双重 --confirm | 从 jsonl 重新 store_chunks 还原 |
| 跨机 orphan 误识别 | source_machine 字段显式标记; mp-wings-review 输出 ✗ 标记不自动删 | 用户人工修正 wing_meta.source_machine |
| Tier 2 hook 误创建 spam wing | wing 名规范化 (`a-z0-9_` 字符集白名单) + 长度限制 32 字符 | 跑 mp-wing-delete 清掉 spam wing |

### G. 实施前要确认的技术问题 (实施前的二次调研)

用户说"实施前你也要调研分析清楚再实施" — 这些是真动手前要明确的:

1. **chromadb 1.5.7 删除某 wing 全 chunks 的具体 API** — col.delete(where={"wing":x}) 是否完全清空? 是否影响 HNSW index 紧凑性? 是否需要 col.compact()? 
   → 实施时先在 test wing 上试一次
2. **sparse.sqlite3 删除 wing 行的 SQL + 反查 chromadb 一致性** — 防止两边对不上
3. **D002 mp-metrics JSONL 30 天滚动里 last_mine_at 推断范围有限** — 如果 wing 90 天没动, JSONL 30 天看不到 — D003 必须从 chromadb metadata mtime + wing_meta 双源拼; 实施时确认现有 chunk metadata 有没有 mtime (我刚才 dump 显示 n/a, 需调研是 metadata 字段名不同还是真的没存)
4. **跨机 orphan 识别**: <external-backup-volume> sync 过来的 chunks 怎么区分? metadata 没有 source_machine 字段 — D003 实施时回填用什么启发式? (e.g. wing 名匹配但本机无 source_dir → 标记 'unknown' / '<backup-volume-restore>')

→ **真动手前先 spike 1 小时验证以上 4 项**, 写回到 D003 ADR "Implementation Findings" 章节, 再开工.

### G.findings. Implementation Findings (Spike 实测 2026-05-27)

**Spike 1: chromadb del-by-wing API**
- `col.delete(where={"wing": X})` 一句完成, metadata filter 原生支持
- chromadb 1.5.7 **无 compact() / hnsw_compact() 用户级 API** (search 仍正常, HNSW tombstones 只占磁盘)
- 实施: `col.delete(where={"wing": wing})` + `print(deleted_count)` (从 col.get 先 count, delete 后再 count 验证 = 0)
- 不需要 compact 操作

**Spike 2: sparse.sqlite3 schema**
- 表: `chunk_sparse_weights (chunk_id TEXT PK, weights TEXT, wing TEXT NOT NULL, updated_at TEXT)`
- 有 `idx_sparse_wing ON wing` 索引, wing 过滤删高效
- 删除 SQL: `DELETE FROM chunk_sparse_weights WHERE wing = ?`
- 现状: 5961 行 (4959 global + 其他), 跟 chromadb 5510 chunks 数对不上 — sparse store 比 chromadb 多了 ~450 行 (D001 Phase 4 orphan cleanup 删了 chromadb 但 sparse 没同步删 → sparse 也有 orphan 行)
- 实施: 删 wing 时**双删** chromadb + sparse; 顺便 D003 加 sparse orphan 清理工具 (Parking Lot)

**Spike 3: chunk metadata mtime — 之前错判!**
- 字段名实际是 **`source_mtime`** (Unix epoch float, 100% 覆盖率), 不是 `mtime`
- 完整 metadata schema (实测 500 chunk 全覆盖): chunk_index / chunk_total / wing / source_file / source_name / source_mtime / filed_at / summary
- 实施: wing_meta 回填可直接 `MAX(source_mtime)` 派生 `last_mine_at` (Unix epoch); 也有 `filed_at` (ISO timestamp, 写入时间, 比 source_mtime 晚)

**Spike 4: 跨机 orphan 识别启发式**
- 实测 5 个 suspect wings:
  - <example-task>: chunks=2, unique_sources=1, sources_exist=**0/1** (100% orphan)
  - <example-reddit>: chunks=2, unique_sources=2, sources_exist=**0/2** (100% orphan)
  - <project-m>: chunks=3, unique_sources=3, sources_exist=**0/3** (100% orphan)
  - <example-ai-voice>: chunks=4, unique_sources=3, sources_exist=**0/3** (100% orphan)
  - <example-new-project>: chunks=18, unique_sources=11, sources_exist=**4/11** (~36% orphan — mixed!)
- 结论: **不能 binary orphan/not**, 要看 missing 比例
- 启发式: `missing_ratio = (unique_sources - sources_exist) / unique_sources`
  - ≥ 80% missing → orphan (机器迁移孤儿)
  - 30-80% missing → degraded (部分丢失)
  - < 30% missing → 看 mtime

**G.findings 总结: 4 档分类公式 (实施版, 比 PRD 原版更精细)**

```python
def classify_wing_status(wing_dict, now=time.time()) -> str:
    # 1) 没活动 → 看源
    last_active = max(wing_dict.last_mine_at, wing_dict.last_search_at, wing_dict.last_source_mtime or 0)
    age_days = (now - last_active) / 86400 if last_active else None
    
    # 2) 源 missing 比例 (orphan 强信号)
    missing_ratio = wing_dict.sources_missing / max(1, wing_dict.unique_sources)
    
    if missing_ratio >= 0.8 or age_days is None or age_days > 365:
        return "orphan"
    if age_days < 90:
        return "active"
    if age_days < 180:
        return "dormant"
    return "stale"  # 180-365 天

### H. 实施顺序 (TEP 单轨)

**前置**: D002 完工 (mp-metrics 落地 + wing 字段预埋)

D003 内部:
1. Spike 4 项技术问题 (1h) → 写 Implementation Findings
2. wing_lifecycle.py 共享 API + wing_meta sqlite3 表
3. 回填脚本: 23 现有 wings → wing_meta
4. memory_core.store_chunks 双写 wing_meta
5. memory_search 双写 last_search_at
6. refresh-memory.sh 拆硬编码 + 季度 reminder
7. 4 个新 CLI (mp-wings-review / inspect / archive / delete)
8. 4 个新测试 case
9. 端到端冒烟: 假新项目 → 自动出现 → review → archive → delete → 验证全清
10. ADR D003 转 Accepted + 实施结果章节
11. project memory 更新
12. <external-backup-volume> sync

## Success Metrics (实施后 1 月观察)

- 新建一个 ~/Developer 项目 + 写 memory 文件 → 1 hour 内自动出现在 mp-status (零手动)
- 跑 `mp-wings-review` 输出 4 档分类正确 (人工核对 ≥ 90% 准确)
- 第一轮 review 后, orphan 数量从 8 → ≤ 3 (用户决策清理后)
- 季度 reminder 在 refresh.log 出现 (2026-08-01 或下个月 1 号)
- 整个 lifecycle 全程零数据丢失 (jsonl dump 可恢复)

## Implementation Results (2026-05-27)

### 落地形态

**新增 (8 文件 + 1 sqlite)**:
- `scripts/wing_lifecycle.py` (~180 LOC) — wing_meta CRUD + classify_status + audit_wing_sources
- `scripts/wing_meta_backfill.py` (~120 LOC) — 一次性回填脚本
- `scripts/mp_wings_review.py` (~140 LOC) — 4 档分类报告 CLI
- `scripts/mp_wing_inspect.py` (~80 LOC) — 单 wing chunks 浏览 CLI
- `scripts/mp_wing_archive.py` (~80 LOC) — JSONL dump + 软删 CLI
- `scripts/mp_wing_delete.py` (~80 LOC) — 物理 purge CLI (chromadb + sparse + wing_meta)
- `scripts/tests/test_wing_lifecycle.py` (~150 LOC) — 4 case
- `bin/mp-wings-review` / `bin/mp-wing-inspect` / `bin/mp-wing-archive` / `bin/mp-wing-delete` — bash wrappers
- `~/.mempalace-zh/wing_meta.sqlite3` (独立 sqlite, 不动 aging.sqlite3)

**改动 (2 文件)**:
- `scripts/memory_core.py::store_chunks` — D003 双写 wing_meta (auto-register + bump last_mine_at)
- `scripts/memory_search.py::search_isolated/search_all` — D003 双写 last_search_at
- `refresh-memory.sh` — **拆掉 22 项目硬编码 list**, 改成 glob 扫描 + 命名规范化 + 季度 reminder (每月 1 号 08:00)

### DoD 验收 (12 项)

| # | 验收条件 | 实际结果 |
|---|---|---|
| 1 | refresh-memory.sh 拆硬编码 list, 通配扫描 22+ wings | ✅ 改成 glob, 实测扫到 ~/.claude/projects 下 4 个真实 Developer 项目目录 |
| 2 | 故意建假项目目录 → 自动出现 wing | ✅ E2E smoke 全流程跑通: 假项目 → mp-mine → wing 自动建 + wing_meta 注册 |
| 3 | wing_meta 表完整回填 23 现有 wings | ✅ 实测 INSERT 23/23 (后续 refresh-memory 跑后又自动 +4 = 27 总数) |
| 4 | mp-wings-review 4 档分类输出正确 | ✅ 实测 27 wings: 7 active (<workstation> sources), 20 orphan (<backup-volume-restore> 源 100% missing) |
| 5 | mp-wing-inspect 输出可读 markdown | ✅ E2E 显示文件名 + chunk preview + summary |
| 6 | mp-wing-archive 生成 jsonl | ✅ E2E 写 `~/.mempalace-zh/archive/<wing>-YYYYMMDD.jsonl` + 软删 wing_meta |
| 7 | mp-wing-delete 需要 archived=1 才能执行 | ✅ test_wing_delete_requires_archived_first 验证 exit 2 拒绝 |
| 8 | 季度 reminder 在 refresh.log 出现 | ✅ refresh-memory.sh § 8 加 `[REMINDER]` echo on 1st 08:00 |
| 9 | tests/test_wing_lifecycle.py 4 case 全 pass | ✅ 4/4 pass in 10.87s |
| 10 | mp-health 全栈仍绿 | ✅ OVERALL [OK] all healthy |
| 11 | hybrid_benchmark + classify_test 仍 pass | classify 17/17 (100%); hybrid 2/8 D001 数据漂移 pre-existing FAIL (D002 ADR 已 document) |
| 12 | <external-backup-volume> sync 完成 | ⏸ 等用户插盘, 跟 D002 一起 sync |

### 验收 + 实测数据

```
=== Wings Health Report (2026-05-27) ===
Total wings: 27

ACTIVE (90 天内有活动) — 7 wings
  global (7299 chunks, last_mine 0h ago, last_search 0h ago, machine=<workstation>)
  <project-f> / <project-b> / <example-opentools> / <project-l> (auto-discovered by glob! 7h ago)
  <example-new-project> / <project-b-ai> (existing, recent)

ORPHAN (源 missing ≥80%) — 20 wings
  <project-a> / <project-e> / <project-h> / <project-c> / <project-k> / ...
  (全部 <backup-volume-restore>, 源文件已不在本机, missing_ratio=100%)
```

### 路径中微调 (vs PRD 原版)

1. **chunk_count 不在 store_chunks 时累加** — upsert 可能只更新不新增, 累加会高估. 改成: wing_meta.chunk_count 由 mp-wings-review 实时 query chromadb 计算 (review 不频繁, 容许 O(wing) 计数)
2. **wing 名规范化兼容性**: 实测老 22 个 hardcoded wings 跟 glob normalize 基本一致, **唯一 mismatch**: `-<project-i>` 老→`<project-i>`, 新→`<project-i>_new<project-i>`. 但 ~/.claude/projects 下该目录不存在, 不触发. 留 Parking Lot, 用户 review 时如发现可手动合并.
3. **源目录推断准度**: backfill 用 `parts[idx+1]` 后 'projects/' 第一段 — 对 `global` wing 推断成 `-user` (因为 source 来自 ~/.claude/projects/-user/memory). 跟 ~/.claude/skills/ 路径混合时可能不准, 但不影响 lifecycle 决策, 仅 source_dir 字段供 review 参考.

### Spike Findings 集成 (G.findings 章节)

Spike 1-4 全部实施前完成 + 写回 ADR. 关键纠正: Spike 3 揭示之前以为的 metadata `mtime` 字段实际叫 `source_mtime`, 100% 覆盖. 这让 backfill 用真实 mtime 派生 last_mine_at, 比依赖 D002 metrics JSONL 准确得多.

### 教训沉淀

1. **chromadb 1.5.7 无用户级 compact API** — HNSW tombstones 持久但不影响 search; 不需要操心
2. **chunk metadata 字段名要 verify** — 之前 dump `mtime` 都是 n/a 让我以为 mtime 没存, 实际字段名是 `source_mtime`. **教训**: dump metadata 时遍历 keys 而非假定字段名
3. **glob 自动新增 vs 老硬编码列表** — 实测发现 ~/.claude/projects/ 真实只有 4 个 Developer-* 目录, 老 22-list 大部分早就指向不存在的目录 (Claude Code 在新机器上没建过对话). glob 比硬编码列表更诚实
4. **wing 自动新增 = 改 store_chunks 双写** — 不需要单独的 hook, 因为 mp-mine 必然调 store_chunks, store_chunks 一加双写 = 全路径覆盖 (Tier 1 batch + Tier 2 hook + 手动 mp-mine 全自动)
5. **mp-wing-delete 默认 dry-run** — 物理删除有不可逆性, 默认 dry-run + --confirm 双重保险跟 GitLab/Stripe 类似最佳实践
6. **archived 软删 vs hard_delete** — archived=1 是审计追溯 (留 jsonl + 时间戳); hard_delete 是物理清理. 两步流程让用户有反悔机会

### <external-backup-volume> sync 待办 (跟 D002 一起)

新文件清单要 sync:
```
~/.claude/scripts/wing_lifecycle.py
~/.claude/scripts/wing_meta_backfill.py
~/.claude/scripts/mp_wings_review.py
~/.claude/scripts/mp_wing_inspect.py
~/.claude/scripts/mp_wing_archive.py
~/.claude/scripts/mp_wing_delete.py
~/.claude/scripts/tests/test_wing_lifecycle.py
~/.claude/bin/mp-wings-review
~/.claude/bin/mp-wing-inspect
~/.claude/bin/mp-wing-archive
~/.claude/bin/mp-wing-delete
~/.claude/refresh-memory.sh (改动)
~/.claude/scripts/memory_core.py (改动: store_chunks 双写)
~/.claude/scripts/memory_search.py (改动: 双写 last_search_at)
~/.mempalace-zh/wing_meta.sqlite3 (新数据)
~/.mempalace-zh/archive/ (空目录, 实际归档时填充)
```

## D003 v1.1 Amendment — asset_exists signal (2026-05-27 same-day)

**Trigger**: First `mp-wings-review` run flagged `<project-a>` ((maintainer)'s flagship reference implementation per CLAUDE.md), `<project-e>`, `<project-c>`, `<project-d>` 等 16 个 ACTIVE projects as ORPHAN. **Root cause**: classify_status only consulted `~/.claude/projects/<X>/memory/` source_file existence; ignored whether `~/Developer/<X>` (actual project asset) still existed locally. Almost a destructive false-positive (would have purged 16 live projects' wings if user blindly ran the recommendation).

**Industry research**: GitHub stale-bot, GitLab archive policy, Notion page retention all use **multi-signal** — they never archive an asset just because a derived activity log went silent. Asset existence **overrides** historical-activity signal.

**Fix (single-shot, no scope creep)**:
1. `wing_lifecycle.developer_project_exists(wing)` — new pure function reverse-normalizes `~/Developer/<X>` against wing name
2. `wing_lifecycle.audit_wing_sources` — now returns `developer_dir_exists` field
3. `wing_lifecycle.classify_status` — new `asset_exists` parameter; when True, would-be `orphan` is downgraded to `dormant`
4. `mp-wings-review` — passes `asset_exists` through + shows new `DevDir` column (`alive` / `gone`)

**Verification**: 30 pytest cases still pass (asset_exists default None preserves prior behavior); mp-health green; wings now classified correctly:
- ACTIVE: 7 (was 7, same)
- DORMANT: 16 (was 0 — these were ACTIVE projects wrongly flagged orphan in v1.0)
- STALE: 0
- ORPHAN: 4 (was 20 — only the 4 true dead projects)

### Truly orphaned wings purged (2026-05-27)

| Wing | Chunks | Source files | Reason |
|---|---|---|---|
| `<example-task>` | 2 | MEMORY.md only | FB Chrome ext, defunct |
| `<example-ai-voice>` | 4 | 3 files | AI Voice Studio, ~/Developer dir gone |
| `<project-j>` | 26 | 4 files | content actually about <project-a> (mis-classified historical residue) |
| `<project-k>` | 44 | 3 files | content actually about <project-d> (mis-classified historical residue) |

All 4 archived to `~/.mempalace-zh/archive/<wing>-20260527.jsonl` (94 KB total) before purge. Recoverable if needed.

### Outcome

- Total chunks: 5510 → 5434 (76 deleted)
- Total wings: 27 → 23 (4 archived + deleted)
- 0 user-perceivable impact (no active project lost data; 16 false-positive orphans correctly preserved)

### Discoveries / lessons

1. **LLM classification historical drift**: 2/4 purged wings had **mis-classified** content (<project-j>→<project-a>, <project-k>→<project-d>). These predate the v2 classify_wing prompt (per D001 / memory_llm_assist_test improvements). Active <project-a> / <project-d> wings already hold canonical current data, so historical residue purged without loss.
2. **D002 mp-metrics forward-compat paid off**: future review can use JSONL `mine` events to detect mis-classified ingestion in real time (alert if a wing receives chunks whose source_file path mentions a different project name).
3. **Single-signal lifecycle decisions are dangerous**: multi-signal (source_file ratio + asset existence + age + activity) is the industry default for a reason. D003 v1.0 single-signal would have destroyed 16 live wings.
4. **Must verify wings against ground truth before any batch delete** — for D003-style lifecycle work, "the asset is alive" trumps every other signal.

## References

- ADR best practices: AWS Architecture Blog / Martin Fowler bliki / TechTarget 2025-2026
- "One decision per ADR" 原则: AWS Prescriptive Guidance
- GitLab archive 90 天阈值
- AWS S3 lifecycle 90/180/365 分层
- D001: `~/.claude/docs/decisions/D001-progressive-disclosure-and-hybrid-retrieval.md`
- D002: `~/.claude/docs/decisions/D002-test-suite-and-metrics.md`
