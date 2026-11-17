# Novel-RAG · 项目方案 v0.9

> 以完结小说为蓝本的写作范本检索系统
> 交付形态：**云服务（HTTP API）** 或 **stdio MCP 服务**；对外既可返回**待拼接的结构化素材**，也可返回**拼接完成的提示词**
> **多书聚合是本项目对外提供的最高层抽象接口**
> 当前阶段：离线拆书索引、单书检索、多书聚合、可选重排、上传与实时观察台均已实现；本版补齐多书顶层契约、云服务/MCP 交付形态、上下文拼接契约，并把检索质量评估扩展到多本书

**文档版本：** v0.9
**更新日期：** 2026-11-13
**上一版本（已归档）：** [`archive/novel-rag-prd-v0.8-2026-09-24.md`](archive/novel-rag-prd-v0.8-2026-09-24.md)

---

## 一、项目定位与边界

**Novel-RAG** 是一个独立的写作范本检索服务：把已完结小说拆解为可检索的 Scene 资产，用多路向量检索找到"值得参考的场景"，并把结果以调用方可直接使用的方式交付。

- **离线输入**：`.epub` / `.txt` / `.md` 小说文件。
- **离线输出**：PostgreSQL 保存全量 Scene 归档（Archive Layer）；Qdrant 只保存经 Reference Evaluation 选中、完成深度标注并成功向量化的 Writing Reference Scene（Reference Layer）。
- **在线输入**：自然语言写作需求 + 一个或多个 `book_id`（可选显式 `version`）+ 交付格式与预算参数。
- **在线输出**：结构化查询、逐书四路 Top-N、书内 RRF、跨书聚合结果、可选重排结果，以及**两种消费形态**之一：
  - `fragments`：结构化素材 + 引用信息，由调用方自行拼接；
  - `prompt`：服务端按预算与模板拼接完成的提示词文本。
- **不负责**：小说正文生成、Agent 编排、写作流程控制。上下文拼接只做"把检索素材变成提示词"，不生成新剧情。
- **对外身份**：云服务或本地 stdio MCP 服务。`novel-creator` 只是调用方之一，不引入其数据结构或会话模型。

### 1.1 v0.9 的定位变化

| 议题 | v0.8 | v0.9 |
| --- | --- | --- |
| 多书聚合 | 后续扩展位，本批不实现 | **最高层抽象接口**，单书检索是其内部实现细节 |
| Rerank | 本批不接入请求链路 | 已实现为可选链路，由配置开关控制 |
| 上下文拼接 | TBD，不定义 | 明确定义对外契约（见第六章） |
| 交付形态 | 仅 HTTP | 云服务 HTTP + stdio MCP |
| 质量评估 | 单书 10 条探针 | 多书规模化基线（见第十章） |
| 默认运行库 | 无明确默认，观察数据在独立预览模式 | 默认指向观察库 `novel_rag`，测试库为独立的 `novel_rag_test` |

---

## 二、术语与分层

| 术语 | 含义 |
| --- | --- |
| Archive Layer | PostgreSQL 中的全量 Scene，含 `selected` / `archived` / `discarded` / `evaluation_failed` |
| Reference Layer | Qdrant 中满足 `selected + annotated + indexed` 的 Point |
| Writing Reference Scene | 具备可迁移写作参考价值的 Scene，是检索命中的最小单位 |
| 单书边界 | `book_id + version`，单书检索只读取对应 collection |
| 书单（book set） | 一次多书聚合请求中显式给出的 `book_ids` 集合，是权限与作用域的基本单位 |
| 片段（fragments） | 未拼接的检索素材，含 Scene 标识、限长预览、标注字段与名次 |
| 组装（assembly） | 把已排序的片段按预算与模板转换为提示词文本的过程 |
| 交付形态 | `cloud_http` 或 `stdio_mcp`，两者共享同一套领域契约 |

**双层数据原则不变：** 所有 Scene 都是小说结构资产，只有部分 Scene 是写作范本资产。跨书检索只在 Reference Layer 范围内进行。

---

## 三、总体架构

```text
┌──────────────────────── 离线索引（已实现） ────────────────────────┐
│ 文件导入 → EPUB/TXT/MD 解析 → 章节整理 → LLM 场景切分                │
│ → 场景质量全筛（垃圾/近重复） → 参考价值评估 → selected 深度标注      │
│ → 四路向量化 → PostgreSQL Archive Layer + Qdrant Reference Layer     │
│ → 一致性校验后激活 current_version                                   │
└──────────────────────────────────────────────────────────────────────┘
                                   ↓ 只读
┌──────────────────────── 在线服务（v0.9 交付层） ────────────────────┐
│  云服务 HTTP API            │            stdio MCP Server            │
│  /books /search /search/multi│  tools: search_references / get_scene  │
│  /assemble（新增契约）       │         build_reference_prompt         │
└──────────────────────────────────────────────────────────────────────┘
                                   ↓
┌──────────────────────── 在线检索（已实现） ──────────────────────────┐
│ ① 查询解析与降级 ② 一次性查询向量化 ③ 逐书四路召回                   │
│ ④ 书内无权重 RRF ⑤ 跨书聚合（轮流汇集） ⑥ 可选全局重排              │
│ ⑦ 回表取原文/标注 ⑧ 组装层（fragments 或 prompt）                    │
└──────────────────────────────────────────────────────────────────────┘
```

**两条链路仍分开运行**：离线索引是批处理，在线服务只读消费已激活版本。在线链路不修改 books、chapters、scenes、token_map 或 Qdrant Point。

---

## 四、交付形态

### 4.1 运行形态与网络暴露（当前）

- **当前不租赁服务器。** 运行形态是**本机部署 + Cloudflare Tunnel（或同类内网穿透）按需暴露**：应用、PostgreSQL、Qdrant、Ollama 全部留在本机，公网侧只做转发。
- **云服务器 / 托管云服务是以后可能的实现**，本版只保证领域契约与 HTTP 接口不与之冲突，不做服务器侧改造；迁移时预计只需要增加持久化部署脚本与鉴权网关。
- 应用默认监听 `127.0.0.1`；使用隧道时必须先启用访问控制（Cloudflare Access、API Key 或反向代理鉴权），否则任何拿到域名的人都能读取全部书籍。
- 隧道只暴露应用 HTTP 端口，不暴露 PostgreSQL（5432）、Qdrant（6333/6334）、Ollama（11434）。
- 限流与配额按调用方设置：并发检索数、单次 `book_ids` 数量、返回条数；超限返回结构化错误。

### 4.2 HTTP API

- Base path `/api/v1`；`/health` 独立于版本前缀。
- 现有接口（已实现）：书籍登记/上传/索引、版本与 Scene 观察、单书检索、多书检索（含 NDJSON 流式）、PG/Qdrant 组件直查。
- 新增契约（本版定义，随 M1 实现）：`POST /api/v1/assemble`、`GET /api/v1/book-sets`（可选）。
- 稳定性：所有检索接口无副作用；失败返回结构化错误，不返回伪造的空成功结果。

### 4.3 stdio MCP（已实现第一版）

启动：

```bash
uv run python -m app.mcp_server      # 或 uv run novel-rag-mcp
```

MCP 是交付重点，核心设计是：**调用方不需要会拼检索查询**。调用方只说"想写什么"，
服务端负责查询规划、多书检索与提示词组装。

已实现工具：

| tool | 作用 | 状态 |
| --- | --- | --- |
| `search_writing_references` | **主入口**：writing_goal + 细纲/约束 → 查询规划 → 多书检索 → 拼装 prompt 或 fragments | 已实现 |
| `plan_writing_query` | 只预览由写作目标生成的检索查询，不检索 | 已实现 |
| `build_reference_prompt` | 进阶：直接给检索 query，返回拼装结果 | 已实现 |
| `get_scene` | 按 book_id/version/scene_id 回查原文与标注 | 已实现 |
| `list_books` | 列出书籍与当前版本（返回 `{count, books}`） | 已实现 |
| `get_service_status` | 检查 PostgreSQL / Qdrant / Ollama / LLM 配置与 ready 书籍数 | 已实现 |
| `get_usage_guide` | 返回"如何向本服务提问"的完整说明 | 已实现 |

同时暴露：

- MCP prompt：`writing_reference_brief(writing_goal)`，给调用方一段现成的调用指导；
- MCP resource：`novel-rag://usage-guide`，与 `get_usage_guide` 同内容；
- 配置文件级 skill：[`skills/novel-rag-writing/SKILL.md`](../../skills/novel-rag-writing/SKILL.md)。
- 项目以 hatchling 打包，`uv sync` 会安装 `novel-rag-mcp` 入口；第三方客户端的 `mcpServers`
  配置示例见 README「第三方 AI 接入」。
- 预期错误统一转为 MCP `is_error=true` 的可读消息（`ToolError`），不会只暴露 SDK 的通用失败。

不变量：

- 以 stdio 启动，随调用方进程生命周期，不额外监听端口。
- 与 HTTP 共享同一检索/组装领域层与响应结构，不直连数据库以外的新存储。
- 默认只读：不触发索引、不修改 Scene。
- 默认不重排；`use_rerank=true` 才进入分钟级重排。
- 超长 Scene 一律有界采样，不把整本书塞进 tool 结果。

### 4.4 调用方 A 如何发起请求（组装 skill）

背景：写作 Agent 通常只知道由细纲推出的"想写什么样的段落"，并不知道如何拆标签或构造检索 query。为此提供三层入口：

1. **最简（推荐）**：只传 `writing_goal` + `outline_context` + `constraints`，调用
   `search_writing_references`。工具内部用 `plan_writing_query` 生成查询，
   再用多书聚合与组装层返回 `prompt.text`。
2. **预览/调试**：先用 `plan_writing_query` 看服务端会用什么查询，不消耗检索；
   确认后再调用主入口。
3. **进阶**：已有结构化 query 的调用方用 `build_reference_prompt`，或直接用
   HTTP `/api/v1/assemble`。

组装 skill 的职责是告诉 A：

- 什么时候调用、参数怎么填（目标、细纲上下文、约束）；
- 三个层级的入口怎么选；
- 返回值怎么用（`prompt.text` 直接进写作模型；`references` 用于回查与引用）；
- 失败/空结果怎么处理（把目标写具体，而不是换标签）；
- 不要做什么（不要自己拼标签、不要要求整本书、不要默认开重排）。

调用示例（MCP tool arguments）：

```json
{
  "writing_goal": "写一段雨夜追杀的紧张场景，冷峻短句，结尾留一个反转",
  "outline_context": "主角刚发现盟友背叛，追兵封锁街道",
  "constraints": {"情绪": "紧张", "句式": "短句", "视角": "第三人称限知"},
  "output": "prompt",
  "budget_chars": 6000,
  "max_scenes": 6
}
```

服务端返回：`query_plan`（实际使用的查询）、`search`（逐书状态/耗时/降级）、
`prompt`（文本 + 预算）、`references`（引用表）、`dropped`（预算/上限舍弃项）。

### 4.5 两种输出格式

| 取值 | 含义 | 定位 |
| --- | --- | --- |
| `prompt` | 服务端按预算与模板拼接完成的提示词文本 | **当前实现目标**：让"拼接完成的提示词"正式可用 |
| `fragments` | 只返回结构化素材与引用信息，由调用方拼接 | 中间产物与高级调用方 |
| `both` | 同时返回两者，便于审计与调试 | 调试与前端展示 |

无论哪种格式，都必须返回可追溯的引用表（`book_id`、`version`、`scene_id`、`scene_index_in_book`、`rank`）与预算使用报告。

---

## 五、多书聚合（最高层抽象接口）

**多书聚合是 Novel-RAG 对外的主接口。** 调用方只声明"我要在哪些书里找什么"，服务端负责查询解析、逐书召回、书内融合、跨书汇集与可选重排。单书检索接口保留，但定位为聚合器的内部构件与调试入口。

### 5.1 接口

```http
POST /api/v1/search/multi
POST /api/v1/search/multi/stream
```

请求：

```json
{
  "query": "找一段通过对话逐步揭露真相、结尾有反转的场景",
  "book_ids": [10, 43, 44, 86],
  "versions": {"10": 3, "86": 3},
  "route_top_n": 20,
  "per_book_limit": 5,
  "global_limit": 20,
  "output": {"format": "fragments"}
}
```

约束：

- `book_ids` 必填、去重、数量上限由 `MULTI_SEARCH_MAX_BOOKS` 控制。
- `versions` 可选，只允许指定 `book_ids` 内且为正整数的版本；解析规则与单书一致，缺省使用 `books.current_version`，并在响应中回显实际版本。
- `route_top_n`、`per_book_limit`、`global_limit` 均受服务端上下限约束。
- 不接受任何业务权重参数（无 `book_weight` / `route_weight` / `tag_weight`）。
- 当前已实现的 `/search/multi` 只返回 `fragments`；`output` 字段与 `prompt` 返回随组装层（M1）落地，在此之前调用方按 fragments 自行拼接。

### 5.2 处理语义

```text
1. 校验书单与版本，固定本次请求的 (book_id, version) 集合
2. 查询解析一次；使用 **latency-first** 参数（`reasoning_effort=minimal`、`max_tokens=400`、
   温度 0、超时 45 秒、不重试、`response_format=json_object`）；失败或超时立即退化为
   `raw_intent`，并把 fallback 结果缓存 6 小时，避免同一慢查询反复等待；成功结果照常持久缓存。
3. 只执行一轮查询向量化：text / meta / summary 三路查询向量 + 稀疏查询
4. 对每本书并发执行四路独立召回，各自保留 rank 与原始 score
5. 每本书内做无权重 RRF，得到书内候选
6. 跨书聚合：按书内 RRF 名次轮流汇集（round_robin_book_rrf），不直接比较跨书原始分数
7. 可选全局重排：对候选池前 N 条用 Rerank 模型评分，失败时保留聚合顺序
8. 回表补全预览与标注；按 output.format 返回 fragments / prompt
```

### 5.3 排序与确定性

- 书内：标准无权重 RRF，`RRF_K` 全局统一；同分按命中路由数降序、最佳单路 rank 升序、`scene_id` 升序。
- 跨书：**不比分数**，按各书书内名次轮流取候选，保证书单内的书籍覆盖与结果确定性。
- 重排：模型分数只用于重排候选池内排序，不改变候选池构成；`score_exact=false` 的边界分数保留原顺序。
- 相同输入、相同索引版本、相同配置下结果必须可复现。

### 5.4 降级与失败

| 情况 | 行为 |
| --- | --- |
| 单本书检索失败 | 其余书继续，失败书进入 `degraded_books`，并记录尝试次数与错误 |
| 单路召回失败 | 书内其余路由继续融合，失败路进入 `degraded_routes` |
| 查询解析失败 | 全链路退化为 `raw_intent` |
| 稀疏词项全部未知 | 该路返回 `skipped / no_known_tokens`，不发送空向量 |
| 重排失败 | `rerank.status=failed`，最终结果退回聚合顺序 |
| 所有书都失败 | 整个请求失败（HTTP 503 / MCP error），不返回空成功 |
| 候选为空 | 返回空数组与原因，不伪造结果 |

### 5.5 响应

```json
{
  "raw_intent": "……",
  "parsed_query": {"fallback": false, "cache_hit": true, "summary_query": "……"},
  "book_ids": [10, 43, 44, 86],
  "books": {
    "10": {
      "status": "ok",
      "version": 3,
      "attempts": 1,
      "routes": {"text_dense": {"items": [], "latency_ms": 0}},
      "rrf": {"k": 60, "items": []},
      "degraded_routes": []
    }
  },
  "degraded_books": [],
  "aggregation": {"strategy": "round_robin_book_rrf", "items": []},
  "rerank": {"status": "disabled"},
  "final": {"source": "aggregation", "items": []},
  "latency_ms": 0
}
```

当 `output.format=prompt` 时额外返回：

```json
{
  "prompt": {"template_version": "assembly-v1", "text": "……", "char_count": 0, "budget": {}},
  "references": [{"book_id": 10, "version": 3, "scene_id": 1, "rank": 1}]
}
```

### 5.6 可观测性

聚合链路记录结构化日志：`book_count`、`candidate_count`、`degraded_book_count`、`rerank_status`、`rerank_latency_ms`、逐书 `candidate_count`、`attempts`、`latency_ms`、`degraded_routes`。日志不输出 Scene 全文。

---

## 六、上下文拼接与提示词组装契约

### 后续要求：调用方预算与输入超限

MCP 与 HTTP `/assemble` 的 `budget_chars` 均为可选参数，省略时使用服务端 `ASSEMBLY_BUDGET_CHARS`；该值是字符预算，不是模型 token 上下文窗口。当前预算控制会缩减或移除参考素材，但任务、大纲、约束和模板本身超过预算时，仍可能超出该值。

后续需设计并实现输入超限策略：优先保留写作任务与明确约束，对可缩减的大纲按可追溯规则裁剪并报告；必要输入已超限时返回明确的预算不足结果。若支持 token 限制，需要明确目标模型与计数方式，不得将字符数直接当 token 数。本轮仅登记需求，不新增截断逻辑，也不要求调用方必须传入上下文长度。

本章是 v0.9 新增的**对外契约**，已随 M1 落地（`app/services/assembler.py`、`app/prompts/assembly/v1.py`、`POST /api/v1/assemble`、MCP `search_writing_references`）。目标：让调用方不必理解 Scene 切分与四路检索细节，也能拿到可用的写作参考上下文。

### 6.1 组装输入

```json
{
  "task": "写一段雨夜追杀的紧张场景，冷峻短句",
  "book_ids": [10, 43, 44, 86],
  "budget_chars": 6000,
  "max_scenes": 8,
  "per_scene_chars": 1200,
  "include_fields": ["summary", "style_summary", "usage_hint", "excerpt"],
  "book_quota": 2,
  "template_version": "assembly-v1"
}
```

### 6.2 组装规则

1. **只使用检索结果**：组装层不重新检索、不调用 LLM、不生成新内容。
2. **去重**：按 `scene_id` 与完整原文哈希去重；近重复片段只保留名次最高的一条。
3. **有界采样**：单条 Scene 超出 `per_scene_chars` 时按开头/中间/结尾有界采样，并标记 `truncated=true`。
4. **预算内贪心填充**：按最终名次从高到低填充，直到接近 `budget_chars`；超预算时丢弃最低名次条目，不静默截断到不可预期位置。
5. **书籍配额**：可选 `book_quota`，避免单一书籍占满上下文；配额不足时按名次回补。
6. **可追溯**：输出必须带 `references` 引用表，标明每条片段来自哪本书、哪个版本、哪个 Scene、原始名次与截断状态。
7. **预算报告**：输出 `budget`，包含预算上限、实际用量、收录条数、因预算被丢弃条数与丢弃原因。
8. **模板版本化**：模板存放于 `app/prompts/assembly/v1.py`（计划新增），修改模板必须新建版本号。

### 6.3 组装输出

```json
{
  "format": "prompt",
  "prompt": {
    "template_version": "assembly-v1",
    "text": "【写作任务】……\n【参考场景 1｜马之途 v3 场景 4】……",
    "char_count": 5820,
    "budget": {"limit": 6000, "used": 5820, "included": 7, "dropped": 1, "dropped_reason": "budget"}
  },
  "references": [
    {"book_id": 10, "version": 3, "scene_id": 4, "scene_index_in_book": 4,
     "rank": 1, "truncated": false, "source": "text_dense+summary_dense"}
  ]
}
```

### 6.4 明确不做的事

- 不生成正文，不改写 Scene 原文。
- 不做多轮对话、不维护会话状态。
- 不在组装层重新排序检索结果（排序只属于检索与重排）。
- 不把不同版本的向量坐标或分数混用。

---

## 七、数据模型

v0.9 不新增必需表。沿用 v0.8 已实现的表结构：`books`、`chapters`、`scenes`、`tag_vocab`、`token_map`、`reference_evaluation_cache`、`annotation_cache`、`query_parsing_cache`、`embedding_cache`、`index_jobs`，以及场景切分/质量全筛缓存表。

后续可能需要的新增（不在本版阻塞范围内）：

| 表 | 用途 | 触发条件 |
| --- | --- | --- |
| `api_keys` / `tenants` | 云服务鉴权与租户隔离 | 启用公网或多调用方 |
| `book_sets` | 命名书单与权限绑定 | MCP/云服务需要稳定书单 |
| `assembly_templates` | 组装模板版本管理 | 模板需要在线更新时 |

---

## 八、离线索引链路

离线链路已在 v0.8 之后补齐以下能力，本版维持契约不变：

- EPUB 按 spine 顺序转换、章节边界保留、转换缓存按 `source_sha256 + converter_version`。
- 全局场景切分 + LLM 边界精判；边界决策持久缓存，长书中断后可复用。
- 场景质量全筛：全书重复证据 + LLM 判定 + 明确规则兜底，剔除垃圾与近重复片段；被剔除 Scene 以 `discarded` 保留在 PostgreSQL，不进入标注与 Qdrant。
- Reference Evaluation 产生 `selected` / `archived` / `evaluation_failed`。
- 仅对 `selected` 做深度标注与四路向量化；一致性校验通过后切换 `current_version`。
- Dashboard 上传（EPUB/TXT/MD、拖拽、重复文件版本确认）与索引进度观察。
- 个人摘抄（非小说素材）入口仍是占位，未实现。

状态机、幂等、缓存与失败语义与 v0.8 一致：`archived` 是正常业务结果，`evaluation_failed` 不得静默转归档，失败版本不激活、旧版本继续可用。

---

## 九、在线检索链路

### 9.1 单书检索（内部构件 + 调试入口）

保留 v0.8 契约：查询解析与 `raw_intent` 降级、四路独立 Top-N、书内无权重 RRF、逐路 rank/score/耗时、`degraded_routes`、`scene_id` 回表。单书接口是聚合器的构件，不作为对外主推接口。

meta 查询的构造在 2026-11-13 修复为 **`raw_intent` 锚点 + canonical 标签**：诊断显示纯标签查询落在高度同质的 meta 向量空间中心，8 条 miss 探针全部无法进入 Top-5；加入需求锚点后该路由基线从 9/28 回升到 24/28（证据见第十章与 `reports/meta-route-diagnosis-2026-11-13.md`）。

查询解析是检索链路里唯一同步调用 LLM 的步骤，按 **latency-first** 设计：默认
`QUERY_LLM_REASONING_EFFORT=minimal`、`QUERY_LLM_MAX_TOKENS=400`、温度 0、
`QUERY_LLM_TIMEOUT_SECONDS=45`、`QUERY_LLM_MAX_RETRIES=0`、`response_format=json_object`；
超时或错误立即回退 `raw_intent`，并把 fallback 写入带 TTL 的缓存（默认 6 小时），同一慢查询
不会反复阻塞。实测同一端点下解析通常 1–3 秒；此前的数分钟等待属于上游模型/网关的瞬时行为，
现在由 45 秒超时兜底。需要更换更快的解析模型时设置 `QUERY_LLM_MODEL` 即可，不影响标注/评估模型。

### 9.2 多书聚合

见第五章。当前实现策略为 `round_robin_book_rrf`；重排为可选链路，通过 `RERANK_ENABLED` 控制，并在每次多书检索前重新读取配置。

### 9.3 流式事件

`/search/multi/stream` 返回 NDJSON，事件包含：`checking_books(_complete)`、`parsing(_complete)`、`embedding(_complete)`、`retrieving`、`route_complete`、`book_complete`、`fusion(_complete)`、`aggregating(_complete)`、`reranking(_complete)`、`result`、`error`。MCP 交付形态用同构的 progress notification 映射这些阶段。

---

## 十、检索质量评估（多书规模化）

### 10.1 评估语料

本版基线覆盖本地观察库中的四本 ready 书籍：

| 书籍 | book_id | 版本 | Scene | 入选参考 | 备注 |
| --- | ---: | ---: | ---: | ---: | --- |
| 马之途 | 10 | v3 | 9 | 8 | 公开快照之一 |
| 备用联系人 | 43 | v2 | 88 | 54 | 公开快照之一 |
| 切勿操之过急 | 44 | v2 | 129 | 89 | 公开快照之一 |
| 百年孤独 | 86 | v3 | 113 | 109 | 公开快照之一（权利声明见 NOTICE） |

### 10.2 方法与指标

- 单书探针：28 条人工写作需求，覆盖四本书的已知目标 Scene，统计各路与 RRF 的 Hit@5。
- 多书聚合探针：4 条跨书需求，统计成功书籍数、候选池规模、最终结果书籍覆盖与耗时。
- 重排探针：1 条查询验证重排链路是否可用及耗时，不作为质量结论。
- 同时记录解析降级率、查询解析缓存命中、降级路由数量与单次耗时分布。

机器可读结果保存在 `data/converted/retrieval-baseline-multi-book.json`，可读报告见 [`reports/retrieval-baseline-multi-book-2026-11-13.md`](../../reports/retrieval-baseline-multi-book-2026-11-13.md)；风格路由的原因分析见 [`reports/meta-route-diagnosis-2026-11-13.md`](../../reports/meta-route-diagnosis-2026-11-13.md)。

### 10.3 结论摘要

2026-11-13 基线结果（完整表格见报告）：

| 指标 | 结果 |
| --- | --- |
| 单书 RRF Hit@5 | 28/28 |
| 原文语义路由 Hit@5 | 27/28 |
| 剧情摘要路由 Hit@5 | 28/28 |
| 原文关键词路由 Hit@5 | 22/28 |
| 风格与技法路由 Hit@5（meta 向量重建后） | 25/28 |
| 风格与技法路由 Hit@5（查询锚点修复后、重建前） | 24/28 |
| 风格与技法路由 Hit@5（初始） | 9/28 |
| 查询解析降级 | 0 条 |
| 存在降级路由的探针 | 0 条 |
| 多书聚合成功 | 4/4，覆盖 4/4 本书，平均约 4.2 s |
| 重排探针 | 可用，5 条候选约 144 s |

结论：多书链路可用、可复现、可解释，稳定性符合预期。meta 路由先从查询锚点修复的 9/28 提升到 24/28，再经风格签名与向量重建提升到 25/28，带动 RRF 达到 28/28；但 meta 仍低于剧情摘要（28/28），标签词表收敛与更大规模评估仍在 M4。重排模型耗时两分钟以上，不能作为同步交互链路的默认项。

meta 向量重建的方法、重建前后书内余弦与复现命令见 [`reports/meta-vector-rebuild-2026-11-16.md`](../../reports/meta-vector-rebuild-2026-11-16.md)。

### 10.4 已知限制

- 探针目标 Scene 由人工按剧情位置指定，属于闭环可用性检查，存在构造偏差。
- 四路原始 score 不可跨向量空间比较，报告只统计名次与命中。
- 跨书重排收益尚未建立对照实验，缺少标注数据；重排单次耗时约 144 s。
- 四本书已包含在公开快照中，但语料仍只有四本；数十本规模的召回质量与延迟尚未验证。
- 每本只有 8–109 个可检索 Scene，Hit@5 存在天花板效应，25/28 不能外推。
- meta 向量重建后书内余弦仍在 0.68–0.73，标签词表 Jaccard 仍为 0.29；词表收敛待做。
- 多书聚合的"书籍覆盖 4/4"由轮流汇集策略保证，不能当作相关性质量指标。

---

## 十一、对外 API 清单

| 能力 | 方法与路径 | 状态 |
| --- | --- | --- |
| health | `GET /health` | 已实现 |
| 书籍登记 / 上传 | `POST /api/v1/books`、`POST /api/v1/books/upload` | 已实现 |
| 启动索引 / 任务 | `POST /api/v1/books/{book_id}/index`、`GET /api/v1/books/{book_id}/jobs` | 已实现 |
| 书籍 / 章节直查 | `GET /api/v1/books`、`GET /api/v1/books/{book_id}`、`GET /api/v1/books/{book_id}/chapters/{chapter_index}` | 已实现 |
| 版本与观察 | `GET /api/v1/books/{book_id}/versions`、`.../overview`、`.../scenes`、`.../scenes/{scene_id}`、`.../projection`、`.../compare`、`.../scenes/{scene_id}/similar` | 已实现 |
| Scene 直查 | `GET /api/v1/scenes/{scene_id}` | 已实现 |
| 单书检索 | `POST /api/v1/books/{book_id}/search`、`POST /api/v1/books/{book_id}/search/stream` | 已实现（内部构件） |
| **多书聚合（主接口）** | `POST /api/v1/search/multi`、`POST /api/v1/search/multi/stream` | 已实现 |
| 写作参考组装 | `POST /api/v1/assemble` | 已实现 |
| Qdrant 直查 | `POST /api/v1/qdrant/collections/{collection}/points/search`、`GET .../points/{point_id}` | 已实现 |
| MCP tools | `search_writing_references`、`plan_writing_query`、`build_reference_prompt`、`get_scene`、`list_books`、`get_usage_guide` | 已实现（stdio） |

请求/响应细节以运行中的 `/openapi.json` 与 `/docs` 为准。

---

## 十二、配置与环境

```bash
APP_VERSION=0.9.0

# 观察库（已含四本解析完成的书；第三方接入无需重新索引）
POSTGRES_DB=novel_rag
POSTGRES_TEST_DB=novel_rag_test

# 查询解析（latency-first）
QUERY_LLM_MODEL=
QUERY_LLM_TEMPERATURE=0
QUERY_LLM_MAX_TOKENS=400
QUERY_LLM_REASONING_EFFORT=minimal
QUERY_LLM_TIMEOUT_SECONDS=45
QUERY_LLM_MAX_RETRIES=0
QUERY_LLM_JSON_RESPONSE_FORMAT=true
QUERY_FALLBACK_CACHE_SECONDS=21600

# 多书聚合
MULTI_SEARCH_MAX_BOOKS=20
MULTI_SEARCH_CONCURRENCY=4
MULTI_SEARCH_ATTEMPTS=2
MULTI_SEARCH_PER_BOOK_LIMIT=20
MULTI_SEARCH_GLOBAL_LIMIT=50

# 可选重排（每次多书检索前重新读取）
RERANK_ENABLED=false
RERANK_TOP_N=10
RERANK_CONCURRENCY=1

# 组装（计划实现）
ASSEMBLY_BUDGET_CHARS=6000
ASSEMBLY_MAX_SCENES=8
ASSEMBLY_PER_SCENE_CHARS=1200
ASSEMBLY_TEMPLATE_VERSION=assembly-v1
```

单书四路、模型输入上限（`MAX_LLM_INPUT_CHARS` / `MAX_EMBED_INPUT_CHARS`）、标签词表版本等沿用既有配置。

`POSTGRES_DB` 的默认值面向本仓库的本地观察环境；生产或 CI 应另建数据库并在迁移后导入或重新索引书籍，避免测试与演示数据互相污染。

---

## 十三、部署、安全与长任务可靠性

### 13.1 运行与网络暴露

- 本地开发：`uv sync` + 迁移 + `uvicorn app.main:app`。
- 容器：`compose.yaml` 启动 PostgreSQL、Qdrant、Ollama、应用与迁移；默认仅绑 `127.0.0.1`。
- **公网访问（当前方案）**：本机 + Cloudflare Tunnel（或同类内网穿透）。隧道只转发应用 HTTP，且必须先启用访问控制（Cloudflare Access、API Key 或反向代理鉴权）；PostgreSQL/Qdrant/Ollama 端口不暴露。
- **云服务器 / 托管云服务：以后可能的实现**，当前不租赁服务器、不做服务器侧改造；领域契约与网络形态解耦，迁移时预计只需补持久化部署与网关鉴权。
- MCP：stdio 启动，随调用方进程生命周期，不额外监听端口。
- 密钥只放环境变量，不落日志；公开仓库不提交 `.env`。

### 13.2 长任务可靠性（索引，不引入额外中间件）

**结论：不需要 Redis / Celery / RabbitMQ。** 本服务是单机、低并发的索引场景（一次索引一本书，
阶段之间本来就有缓存与幂等表），可靠性缺口是"崩溃后能否续跑"，而不是"吞吐或跨机调度"。
持久状态已经全在 PostgreSQL，用**数据库任务队列 + 租约 + 断点续跑**即可闭环，不新增端口、
账号或运维对象。

现状问题：`start_index` 使用 FastAPI `BackgroundTasks` 在 API 进程内执行，长书索引在模型超时、
进程重启或人工中断时会丢失任务状态。

设计（M3，in-process 已实现）：

- **队列即表**：扩展现有 `index_jobs`，增加 `job_type`（index/stage）、`status`（queued/leased/
  running/completed/failed/retrying/canceled）、`attempts`、`max_attempts`、`next_retry_at`、
  `lease_expires_at`、`heartbeat_at`、`worker_id`、`target_version`、`checkpoint JSONB`。
  `start_index` 在 worker 开启时只入队并返回 `job_id`，不再直接跑任务。
- **领取任务**：worker 用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取；`job_type='index'` 上的
  部分唯一索引保证每本书最多一个活跃索引请求；租约到期才允许接管。
- **断点续跑**：沿用现有幂等资产——EPUB/TXT 转换缓存、场景切分缓存、质量全筛缓存、标注缓存、
  Embedding 缓存、Scene 状态机；`checkpoint.completed_stages` 记录已完成阶段，重启后若 `split`
  已完成则直接加载已存 Scene，避免重复切分与覆盖下游结果。
- **重试与退避**：timeout、连接错误、429、5xx 按 `min(30 × 2^n, 900)` 秒退避，达到
  `max_attempts` 标记 failed 并保留旧版本可用；文件缺失、格式错误等永久错误不重试。
- **看门狗**：worker 每 30 秒心跳续租；进程崩溃后租约过期，下一次启动或轮询自动回收为
  `retrying`，不出现永久 running。
- **运行形态（当前选择）**：`INDEX_WORKER_ENABLED=true` 时，FastAPI lifespan 启动一个
  in-process 守护线程；API 重启后线程随进程消失，但租约过期 + checkpoint 会在下次启动接管续跑。
  独立 worker 进程（`python -m app.worker`）保留为后续可选项，不是当前必需。
- **低延迟**：默认 5 秒轮询；需要时可加 PostgreSQL `LISTEN/NOTIFY`，仍不引入中间件。
- **可观测与人工操作**：阶段、尝试次数、最后错误、下次重试时间、租约状态写库并在 Dashboard 展示；
  取消接口在阶段边界生效（repository 已支持，HTTP 入口待补）。
- **失败隔离**：单个 Scene 失败不阻塞整书；失败达到阈值时新版本不激活，旧版本继续可用。

验收：worker 单测覆盖成功/续跑/瞬时重试/永久失败/空队列/租约回收；集成测试覆盖
`enqueue → claim → 租约过期接管 → complete`、退避与最大重试；微型小说端到端测试由 worker
从入队跑到 ready，checkpoint 记录到 `sync`。

**取舍**：数据库队列不提供跨主机自动扩缩容、优先级路由和现成的任务监控 UI。当前单机规模用不上；
`FOR UPDATE SKIP LOCKED` + 租约的语义足够，且省掉一整套中间件的部署与凭证管理。

### 13.3 重排的定位与优化

实测重排 5 条候选耗时约 138.6 s（Qwen3-Reranker-4B，本机 Ollama）。结论：

- **重排天生高耗时，不作为实时写作工作流的一部分**；它是离线/半离线精排、批量评估与人工审核辅助。
- 默认链路使用聚合顺序；调用方可显式请求重排，或读取后台预计算的重排结果。
- 优化方向：按 `(model, prompt_version, query, document hash)` 缓存候选分数；候选先截断到 Top-N；并发受 Ollama 容量约束；超时快速失败并回退聚合顺序；对高频查询后台预计算。
- 指标：缓存命中率、单候选耗时、超时率、重排前后名次变化；重排收益需与不重排基线对照后才能决定默认值。

### 13.4 数据边界与公开快照

- 公开仓库分发**四本书快照**（马之途、备用联系人、切勿操之过急、百年孤独）及其衍生标注与向量；不包含原始 EPUB/TXT。
- 作品数据不适用代码的 MIT 许可；权利人如有异议，按 `NOTICE.md` 的 takedown 流程处理，可用导出脚本重新生成快照替换。
- 检索与观察接口一律只读，不借由看板或 MCP 删除、重建或切换索引版本。

---

## 十四、阶段路线与验收

| 阶段 | 范围 | 状态 |
| --- | --- | --- |
| A | PRD 定稿、单书边界与四路契约 | 已完成 |
| B | 实时索引观察台 | 已完成 |
| C | 单书 Retrieval Lab | 已完成 |
| D | 单书人工基线 | 已完成（归档报告） |
| E | 场景质量全筛、上传与版本化重传 | 已完成 |
| F | 多书聚合顶层接口 + 重排开关 | 已完成 |
| G | 多书规模化基线 + meta 路由诊断 | 已完成 |
| **H（M1）** | **组装层 `/assemble` 与 `build_reference_prompt`：让"拼接完成的提示词"正式可用** | 已完成 |
| **I（M2）** | **stdio MCP：以写作目标为主入口，附组装 skill 与使用说明** | 已完成第一版 |
| J（M3） | 长任务可靠性：in-process worker + PG 队列/租约/重试/checkpoint | in-process 已实现；独立进程与取消 UI 待做 |
| K（M4） | meta 标注与词表整改、路由形态评估，并回归多书基线 | 查询锚点 + 向量重建已完成；词表收敛待做 |
| L（M5） | 四书公开快照 | 本版完成导出 |
| M | 云服务器 / 托管云形态 | 未来，暂不启动（当前用本机 + Cloudflare Tunnel） |
| N | 个人摘抄库、写作质量标注 | 未来，不着急 |
| O | CI 与独立测试库（见 §13.2 之外的工程保障） | 按需，后续 |

**M1 验收门槛**：`/assemble` 在给定预算内返回提示词或 fragments，包含完整引用表与预算报告；超预算时丢弃最低名次并给出原因，不静默截断；有单元测试覆盖去重、配额、截断与空结果。

**已达成项**：单元测试与集成测试通过；任意 ready `book_id + version` 可实时观察；多书聚合支持部分失败降级与确定性排序；检索前后 Scene 状态与 Qdrant Point 数不变。

---

## 十五、风险与决策

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| 跨书原始分数不可比 | 直接相加产生误导排序 | 跨书只按书内名次轮流汇集，不比分数 |
| 查询解析随机或失败 | 多书检索输入不稳 | 版本化 Prompt、结构校验、缓存、失败统一退化 `raw_intent` |
| 版本在请求中漂移 | 混用两个版本 | 请求开始即固定 `(book_id, version)` 并在响应回显 |
| 重排模型不稳定或超时 | 检索延迟上升或失败 | 重排可选、缓存分数、失败退回聚合顺序、显式返回状态与原因 |
| 重排无法实时 | 阻塞写作工作流 | 默认不重排；离线/后台精排；超时快速回退 |
| 长书索引中断 | 任务丢失、整天重跑 | 独立 worker + 分阶段 checkpoint + 重试看门狗 + 手动续跑 |
| meta 向量空间同质 | 风格路召回差 | 查询已加需求锚点；标注/词表整改与路由形态评估列入 M4 |
| 上下文超预算 | 调用方提示词溢出 | 有界采样 + 预算内贪心 + 丢弃报告，禁止静默截断 |
| MCP 与 HTTP 语义漂移 | 两套行为不一致 | 共享领域层与响应结构，契约测试覆盖两条入口 |
| 隧道暴露无鉴权 | 公网数据泄露 | 默认仅本机；隧道前启用 Cloudflare Access / API Key |
| 作品权利异议 | 公开快照需下架 | `NOTICE.md` 说明与 takedown 流程；导出脚本可重新生成快照 |
| 观察库与测试库同名 | 测试污染演示数据 | 已分离为 `novel_rag`（观察）与 `novel_rag_test`（测试）；CI 可另建 |
| 未来云化改造成本 | 迁移返工 | 契约与网络形态解耦；当前只依赖本机 + 隧道 |

---

## 十六、版本历史

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v0.9 | 2026-11-16 | 多书聚合升为最高层接口；交付形态改为本机 + Cloudflare Tunnel，云服务列为未来可能实现；MCP 与组装层（prompt 可用）落地；meta 风格签名与向量重建；**索引长任务可靠性改为 PostgreSQL 队列 + in-process worker（租约/重试/checkpoint，无中间件）**；四书公开快照 |
| v0.8 | 2026-09-24 | 单书检索、观察台、Reference Evaluation 定稿（[归档](archive/novel-rag-prd-v0.8-2026-09-24.md)） |
