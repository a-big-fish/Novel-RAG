# novel-rag · 项目方案 v0.8

> 以完结小说为蓝本的写作范本检索系统
> 独立服务，通过 HTTP API 提供小说知识库拆解、索引观察与自然语言范本检索
> 当前阶段：上半链路已完成 Archive Layer + Reference Layer；本批先完善并随后实现实时索引观察台与单书检索 MVP

------

## 一、项目定位

**novel-rag** 是一个独立的 RAG 服务，输入一本已完结的小说，输出一个可检索的写作范本库。

- **本轮输入**：`E:\novels\tool` 下的 EPUB，或已经转换完成的 TXT；EPUB 在正式索引前必须先转换为 TXT
- **本轮输出**：PostgreSQL 保留全量 Final Scene 归档，Qdrant 只保留经 Reference Evaluation 选中的 Writing Reference Scene
- **在线输入**：写作需求（自然语言）+ 本批选定的 `book_id`，可选显式 `version`
- **在线输出**：结构化查询、四路独立 Top-N、无权重 RRF 融合结果、Scene 原文与元数据
- **不负责**：小说生成、Agent 编排、写作流程控制、自动上下文拼接、正文生成
- **已完成范围**：源文件登记、EPUB 转 TXT、Scene Resolution、Reference Evaluation、selected Scene 深度标注、向量化、双层存储和组件级直查 API
- **本批目标范围**：实时只读索引观察台、自然语言查询解析、单书四路召回、四路结果对比、无权重 RRF 融合
- **后续扩展范围**：由高层聚合器屏蔽不同 `book_id` / collection，支持多书查询；不改变单书检索契约

与外部调用方的关系：

```text
任意外部调用方 / 调试看板
        │
        │ HTTP API
        ↓
   novel-rag  ← 本项目
        │
        ├── PostgreSQL
        ├── Qdrant
        └── Ollama (本批使用 BGE-M3；Reranker 延后)
```

> 解耦原则：`novel-rag` 不引用 `novel-creator` 的数据结构、会话、Agent 或写作流程。外部系统只依赖版本化 HTTP 契约。用户会话可在未来作为拆书模块能力独立增加，但不进入本批。

### 1.1 本批问题、方案与成功标准

**问题：** 上半链路已经能生成 Archive Layer 和 Reference Layer，但静态 Demo 依赖固定 `BOOK_ID` 与导出文件，也无法用自然语言验证四种索引表示是否真正召回了有参考价值的 Scene。

**方案：** 将既有拆书可视化封装为按 `book_id + version` 实时读取的通用只读看板，并提供单书自然语言查询解析、四路独立召回、无权重 RRF 与完整检索轨迹。

**主要使用者：** novel-rag 开发者、索引规则调试者、拆书结果审核者。

**用户故事：**

- 作为索引调试者，我希望选择一本书和索引版本查看拆书结果，以判断 Scene、准入与标注是否合理。
- 作为检索调试者，我希望输入自然语言写作需求并并列查看四路 Top-N，以判断每种表示的实际贡献。
- 作为检索调试者，我希望查看无权重 RRF 的排名来源，以理解融合结果而不是只看到一个黑箱分数。
- 作为未来高层调用方，我希望单书接口以稳定的 `book_id + version` 为边界，以便在不改变底层实现的情况下并行聚合多本书。

**本批成功标准：**

- 任意 `ready` 书籍均可仅凭 `book_id + version` 打开实时看板，不要求重新导出 `data.js`。
- 每次自然语言检索都返回结构化查询、四路各自 Top-N 和一组确定性的无权重 RRF 结果。
- 每条融合结果都可追溯到各路的 rank、原始 score、`scene_id`、`book_id` 和 `version`。
- 查询解析失败时 100% 退化为 `raw_intent` 检索，不导致整个请求失败。
- 本批所有观察与检索操作均不修改 books、chapters、scenes 或 Qdrant Point。

**本批非目标：**

- 多书高层聚合的实现及跨书分数归一化。
- 任何人工权重、书籍权重、标签加权或负向惩罚策略。
- Rerank、自动上下文拼接、上下文预算控制、正文生成、写作工作流。
- 用户会话、查询历史持久化与用户画像。

**双层数据原则：**

- **Archive Layer（PostgreSQL）**：保存全部 Final Scene，包括 `selected`、`archived` 及处理失败的 Scene。`archived` 是正常业务结果，不是无效文本，不得删除。
- **Reference Layer（Qdrant）**：一个 Point 只表示一个经过 Reference Evaluation 选中、完成 Deep Annotation 并成功向量化的 Writing Reference Scene。

> 一句话原则：所有 Scene 都是小说结构资产，但只有部分 Scene 是写作范本资产。

------

## 二、技术选型

| 模块             | 选型                                                        | 状态 |
| :--------------- | :---------------------------------------------------------- | :--- |
| 项目名           | novel-rag                                                   | 已定 |
| 语言             | Python 3.11                                                 | 已定 |
| Web              | FastAPI                                                     | 已定 |
| 关系库           | PostgreSQL 17                                               | 已定 |
| 向量库           | Qdrant 1.19                                                 | 已定 |
| 全文检索         | Qdrant 稀疏向量 + jieba 分词 + 停用词/重要单字 + 词频控制   | 已定 |
| 嵌入模型         | BGE-M3（Ollama `/api/embed`）                               | 已定 |
| 重排模型         | Qwen3-Reranker-4B 调研保留；本批不接入请求链路               | 延后 |
| LLM              | API（base_url + APIKEY，`.env` 配置）                       | 已定 |
| LLM 适配          | OpenAI-compatible API 或 `codex exec`，通过环境变量切换       | 已实现 |
| 数据库访问        | SQLAlchemy Core（不使用 ORM）                                | 已实现 |
| 包管理            | uv                                                           | 已实现 |
| EPUB 转换        | Python `ebooklib` + `BeautifulSoup` + `lxml`                | 已定 |
| 分词             | jieba + 停用词表 + 重要单字白名单                            | 已定 |
| 向量类型         | text-dense + meta-dense + summary-dense + text-sparse       | 已定 |
| 标签系统         | 受控 canonical tags + 展示用 display tags，映射到 `tag_vocab` | 已定 |
| 长文本处理       | 开头 / 中间 / 结尾截断采样，MVP 不拆 passage                 | 已定 |
| 跨书检索         | 本批单书；未来由高层并行调用单书接口并聚合，底层不感知         | 已定 |
| 同步机制         | 版本化 collection + 校验后切换，失败可重跑                   | 已定 |
| 文件存储         | 原始 EPUB 保留原位；转换缓存写入 `data/converted/`           | 已定 |
| 测试框架         | pytest；组件单测优先，联调仅使用小型 fixture                 | 已定 |
| 可观测性         | 日志（stdout，结构化 JSON）                                  | 已定 |
| Prompt 管理      | `prompts/` 文件夹内文件维护                                  | 已定 |
| 范本准入         | 独立轻量 Reference Evaluation，selected / archived             | 已定 |
| 数据分层         | PostgreSQL 保存全量 Scene；Qdrant 只存 selected Scene      | 已定 |
| 对外 API         | FastAPI；索引 API、实时观察 API、单书自然语言检索 API          | 已定 |
| 看板数据模式     | 按 `book_id + version` 实时只读，不依赖静态导出文件            | 已定 |
| 融合策略         | 四路结果并列展示 + 无权重 RRF；不设计人工权重                  | 已定 |
| 当前实现边界     | 上半链路已完成；本批先定稿、随后实现观察台与单书检索 MVP       | 已定 |

------

## 三、总体架构

系统分为**上半链路**和**下半链路**，两者**分开运行**，通过 PostgreSQL + Qdrant 连接。

**上半链路已经完成首轮实现。** 本批以其稳定产物为只读输入，增加实时观察与单书检索；两条链路仍独立运行。

### 上半链路（离线索引，已完成首轮实现）

```text
┌─────────────────────────────────────────────────────────────┐
│               输入：完结小说 .epub / .txt                     │
│              原始 EPUB 保存在 E:\novels\tool                  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 0  源文件准备与 EPUB 转换                              │
│    .txt → 直接清洗                                            │
│    .epub → 按 spine 顺序提取 XHTML → 清洗 HTML → UTF-8 TXT    │
│    转换结果按 source_sha256 + converter_version 缓存           │
│    允许全量输出 TXT 并缓存；不修改原始 EPUB                    │
│    禁止把整本书或未切分全文直接送入 LLM / Embedding             │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 1  拆书                                                │
│    章切分 → 全局切段落 → 规则标记候选边界 → LLM 精判          │
│    → 跨章合并 → 幂等写入 chapters + scenes                   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 2  Reference Evaluation                                  │
│    Final Scene → 轻量 LLM 评估 → selected / archived       │
│    全部 Scene 保留在 PostgreSQL；失败为 evaluation_failed  │
└───────────────────────────────────────────────────────────┘
                              ↓ selected only
┌───────────────────────────────────────────────────────────┐
│  Step 3  Deep Annotation                                        │
│    selected 场景原文 → LLM → 结构化元数据                     │
│    summary / style_summary / usage_hint                      │
│    scene_type / technique / style_tags / emotion_tags        │
│    key_images / narrative_func / meta_json                   │
│    标签映射：display tags → canonical tags                   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 4  向量化（selected + annotated only）                 │
│    text-dense    ← BGE-M3(原文采样文本)                      │
│    meta-dense    ← BGE-M3(style/meta_text)                   │
│    summary-dense ← BGE-M3(summary)                           │
│    text-sparse   ← jieba 分词 + 词频                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 5  存储                                                │
│    PostgreSQL：全量 Scene 归档 + 评估/标注元数据 + 缓存     │
│    Qdrant：仅 selected Scene，4 个向量 + payload              │
│    校验一致后切换 books.current_version                       │
└─────────────────────────────────────────────────────────────┘
```

**线性执行，无实时同步要求。** 每步完成后进入下一步，失败可重跑。所有写入保持幂等。

**三阶段 LLM 职责边界：** Scene Judge 回答“这里是否切分场景”；Reference Evaluator 回答“该 Final Scene 是否值得作为写作范本”；Deep Annotation 仅对 selected Scene 回答“好在哪里、如何被检索”。

**持续约束：**

- 允许 EPUB 全量转换为 TXT，但禁止把全量文本作为单次模型输入。
- 不修改 `E:\novels\tool` 下的原始 EPUB。
- 在线观察与检索只读，不反写上半链路数据。

### 下半链路（在线检索，本批目标）

```text
用户写作需求（自然语言）+ 选定 book_id / version
  ↓
① 查询解析（LLM → 结构化查询）
  ↓
② 构造 4 个查询向量
  ↓
③ 对选定 collection 执行四路独立 Top-N 召回
  ↓
④ 并列保留四路结果与原始 rank / score
  ↓
⑤ 无权重 RRF 融合
  ↓
⑥ 回 PostgreSQL 取 Scene 原文与元数据
  ↓
⑦ 返回结构化查询、四路 Top-N、RRF Top-N 与检索轨迹
```

**两条链路分开，上半链路是离线批处理，下半链路是在线只读请求。** 当前检索器只接收一个选定 `book_id`；未来多书聚合器并行调用同一单书契约，底层检索器不承担跨书权重或归一化。
------

## 四、数据模型

### 4.1 PostgreSQL 表清单

| 表                 | 用途                         | MVP  |
| :----------------- | :--------------------------- | :--- |
| `books`            | 书籍元信息、索引进度         | 必需 |
| `chapters`         | 章节原文、段落范围           | 必需 |
| `scenes`           | 场景（核心）                 | 必需 |
| `tag_vocab`        | 受控标签词表与展示映射       | 必需 |
| `token_map`        | 稀疏向量 token 映射          | 必需 |
| `reference_evaluation_cache` | Reference Evaluation 独立缓存 | 必需 |
| `annotation_cache` | LLM 标注缓存                 | 必需 |
| `embedding_cache`  | Embedding 缓存               | 必需 |
| `index_jobs`       | 索引进度追踪                 | 可选 |

**无外键。** 引用完整性由应用层保证。

### 4.2 books

| 字段                 | 类型        | 说明                                   |
| :------------------- | :---------- | :------------------------------------- |
| `id`                 | BIGINT PK   | —                                      |
| `title`              | TEXT        | 书名                                   |
| `author`             | TEXT        | 作者                                   |
| `source_path`        | TEXT        | 原始文件路径；EPUB 不做复制或修改       |
| `source_format`      | TEXT        | epub / txt                             |
| `source_sha256`      | TEXT UNIQUE | 原始文件内容哈希，用于去重和转换缓存    |
| `converted_path`     | TEXT        | 转换后的 TXT 路径；TXT 输入可为空       |
| `converter_version`  | TEXT        | EPUB 转换器版本；TXT 输入可为空         |
| `status`             | TEXT        | 状态机（见下）                         |
| `current_version`    | INT         | 当前索引版本                           |
| `total_chapters`     | INT         | 章节数                                 |
| `total_scenes`       | INT         | 场景数                                 |
| `selected_scenes`    | INT         | 当前版本 selected Scene 数              |
| `archived_scenes`    | INT         | 当前版本 archived Scene 数              |
| `evaluation_failed_scenes` | INT    | 当前版本评估失败 Scene 数             |
| `error_message`      | TEXT        | 失败原因                               |
| `created_at`         | TIMESTAMPTZ | —                                      |
| `updated_at`         | TIMESTAMPTZ | —                                      |

**status 状态机：**

```text
pending → converting → splitting → evaluating → annotating → indexing → ready
                                                        ↓
                                                     failed
```

| 状态         | 含义           |
| :----------- | :------------- |
| `pending`    | 已登记，未启动索引 |
| `converting` | EPUB 转 TXT 中 |
| `splitting`  | 拆书中         |
| `evaluating` | Writing Reference 准入评估中 |
| `annotating` | LLM 标注中     |
| `indexing`   | 向量化写入中   |
| `ready`      | 可用           |
| `failed`     | 某阶段失败     |

### 4.3 chapters

| 字段                    | 类型      | 说明         |
| :---------------------- | :-------- | :----------- |
| `id`                    | BIGINT PK | —            |
| `book_id`               | BIGINT    | 索引，无外键 |
| `chapter_index`         | INT       | 第几章       |
| `title`                 | TEXT      | 章节标题     |
| `raw_text`              | TEXT      | 章节原文     |
| `char_count`            | INT       | 字数         |
| `start_paragraph_index` | INT       | 全局段落起始 |
| `end_paragraph_index`   | INT       | 全局段落结束 |

`UNIQUE(book_id, chapter_index)`，`INDEX(book_id)`

### 4.4 scenes

| 字段                      | 类型        | 说明                                  |
| :------------------------ | :---------- | :------------------------------------ |
| `id`                      | BIGINT PK   | —                                     |
| `book_id`                 | BIGINT      | 索引，无外键                          |
| `scene_index_in_book`     | INT         | 全书场景序号                          |
| `chapter_start_index`     | INT         | 起始章节                              |
| `chapter_end_index`       | INT         | 结束章节                              |
| `text`                    | TEXT        | 场景原文                              |
| `char_count`              | INT         | 字数                                  |
| `split_reason`            | TEXT        | 切分原因                              |
| `is_cross_chapter`        | BOOLEAN     | 是否跨章                              |
| `summary`                 | TEXT        | 剧情摘要                              |
| `style_summary`           | TEXT        | 风格摘要：为什么这段可作写作范本      |
| `usage_hint`              | TEXT        | 使用提示：适合参考什么，避免什么      |
| `scene_type`              | TEXT[]      | 受控场景类型 canonical tags           |
| `scene_type_display`      | TEXT[]      | LLM 原始/展示场景类型                |
| `technique`               | TEXT[]      | 受控写作技法 canonical tags           |
| `technique_display`       | TEXT[]      | LLM 原始/展示技法                    |
| `style_tags`              | TEXT[]      | 受控文风标签 canonical tags           |
| `style_tags_display`      | TEXT[]      | LLM 原始/展示文风                    |
| `emotion_tags`            | TEXT[]      | 受控情绪标签 canonical tags           |
| `emotion_tags_display`    | TEXT[]      | LLM 原始/展示情绪                    |
| `key_images`              | TEXT[]      | 受控/归一化意象 canonical tags        |
| `key_images_display`      | TEXT[]      | LLM 原始/展示意象                    |
| `narrative_func`          | TEXT        | 叙事功能，严格受控                    |
| `reference_status`        | TEXT        | unevaluated / evaluating / selected / archived / evaluation_failed |
| `reference_score`         | REAL        | Writing Reference Value，0.0–5.0，不单独决定准入 |
| `reference_reason`        | TEXT        | selected / archived 的可审计理由          |
| `reference_prompt_version`| TEXT        | Reference Evaluation Prompt 版本            |
| `reference_rule_version`  | TEXT        | 准入规则版本                         |
| `reference_meta_json`     | JSONB       | 评估维度与原始结构化输出             |
| `meta_json`               | JSONB       | LLM 原始输出                          |
| `annotate_status`         | TEXT        | pending / running / annotated / failed_retryable / failed_permanent |
| `index_status`            | TEXT        | pending / indexed / failed            |
| `error_message`           | TEXT        | 最近一次失败原因                      |
| `version`                 | INT         | 版本号                                |
| `is_active`               | BOOLEAN     | 是否当前可用版本                      |
| `created_at`              | TIMESTAMPTZ | —                                     |
| `updated_at`              | TIMESTAMPTZ | —                                     |

`UNIQUE(book_id, scene_index_in_book, version)`，`INDEX(book_id)`，GIN 索引覆盖受控标签数组。

**Reference 状态原则：**

- `archived` 是正常业务结果：保留原文、章节位置、跨章信息、切分信息与评估结果；不写 `error_message`，不进入 Deep Annotation / Embedding / Qdrant。
- `evaluation_failed` 是处理失败，不得静默转换为 `archived`，也不得进入后续阶段。
- `selected` 只表示获得 Deep Annotation 资格；只有 `selected + annotated + indexed` 的 Scene 才与 Qdrant Point 对应。
- 不使用 `bad_scene`、`useless_scene`、`discarded_scene` 等命名。

**标签原则：**

- 检索、过滤、统计、向量 meta 使用 canonical tags。
- 展示、审计、调试使用 display tags。
- LLM 输出先落到 display tags，再通过 `tag_vocab` 映射到 canonical tags。
- 无法映射的标签只进入 display 和 `meta_json`，不进入受控检索字段。

### 4.5 tag_vocab

| 字段            | 类型        | 说明                         |
| :-------------- | :---------- | :--------------------------- |
|                 |             |                                                  |
| `namespace`     | TEXT        | scene_type / technique / style / emotion / image |
| `canonical_key` | TEXT        | 受控标签 key                 |
| `display_name`  | TEXT        | 展示名或 LLM 常见表达         |
| `status`        | TEXT        | active / deprecated          |
| `created_at`    | TIMESTAMPTZ | —                            |
| `updated_at`    | TIMESTAMPTZ | —                            |

`PRIMARY KEY(namespace, canonical_key)`，`UNIQUE(namespace, display_name, status)`。

`tag_vocab` 用于把 LLM 的自由表达映射到稳定标签。例如：

| namespace | canonical_key | display_name |
| :-------- | :------------ | :----------- |
| style     | short_sentence | 短句 |
| style     | short_sentence | 冷硬短句 |
| style     | restrained     | 克制 |
| style     | restrained     | 情绪克制 |
| image     | rain           | 雨 |
| image     | neon           | 霓虹 |
| image     | neon           | 霓虹倒影 |

### 4.6 token_map

| 字段       | 类型   | 说明       |
| :--------- | :----- | :--------- |
| `book_id`  | BIGINT | 联合主键   |
| `token_id` | INT    | 联合主键   |
| `token`    | TEXT   | 词         |
| `doc_freq` | INT    | 出现场景数 |

`PRIMARY KEY(book_id, token_id)`，`UNIQUE(book_id, token)`

### 4.7 reference_evaluation_cache

Reference Evaluation 是独立 LLM 任务，不与 Deep Annotation 共用缓存。

| 字段               | 类型        | 说明 |
| :----------------- | :---------- | :--- |
| `id`               | BIGINT PK   | — |
| `input_hash`       | TEXT UNIQUE | sha256(model + prompt_version + rule_version + final_input) |
| `model`            | TEXT        | 评估模型 |
| `prompt_version`   | TEXT        | Reference Prompt 版本 |
| `rule_version`     | TEXT        | Reference Rule 版本 |
| `input_text`       | TEXT        | 已有界采样的单个 Final Scene |
| `output_json`      | JSONB       | 经 Pydantic 校验的评估输出 |
| `created_at`       | TIMESTAMPTZ | — |

Reference Prompt 或 Rule 改版只使本缓存自然失效，不影响 Deep Annotation Cache；反之亦然。

### 4.8 annotation_cache

| 字段               | 类型        | 说明                                          |
| :----------------- | :---------- | :-------------------------------------------- |
| `id`               | BIGINT PK   | —                                             |
| `input_hash`       | TEXT UNIQUE | sha256(model + prompt_version + tag_vocab_version + text) |
| `model`            | TEXT        | —                                             |
| `prompt_version`   | TEXT        | —                                             |
| `tag_vocab_version`| TEXT        | 词表版本                                      |
| `input_text`       | TEXT        | —                                             |
| `output_json`      | JSONB       | —                                             |
| `created_at`       | TIMESTAMPTZ | —                                             |

### 4.9 embedding_cache

| 字段         | 类型        | 说明                 |
| :----------- | :---------- | :------------------- |
| `id`         | BIGINT PK   | —                    |
| `input_hash` | TEXT UNIQUE | sha256(model + text) |
| `model`      | TEXT        | —                    |
| `input_text` | TEXT        | —                    |
| `vector`     | REAL[]      | —                    |
| `dim`        | INT         | —                    |
| `created_at` | TIMESTAMPTZ | —                    |

### 4.10 EPUB 转换缓存（文件系统，不建表）

MVP 不新增数据库表，转换结果按内容哈希落盘：

```text
data/converted/{source_sha256}.{converter_version}.txt
```

规则：

- 同一 `source_sha256 + converter_version` 命中缓存时直接复用。
- 转换器升级时修改 `converter_version`，自然产生新缓存，不覆盖旧缓存。
- 使用“写临时文件 → 原子重命名”避免中断后留下半成品。
- 原始 EPUB 始终保留在 `E:\novels\tool`，转换器只读、不改写。
- `books.converted_path` 只记录当前可用缓存路径。

### 4.11 index_jobs（可选）

| 字段            | 类型        | 说明                            |
| :-------------- | :---------- | :------------------------------ |
| `id`            | BIGINT PK   | —                               |
| `book_id`       | BIGINT      | —                               |
| `stage`         | TEXT        | convert / split / evaluate / annotate / embed / sync |
| `status`        | TEXT        | running / completed / failed    |
| `total_items`   | INT         | —                               |
| `done_items`    | INT         | —                               |
| `error_message` | TEXT        | —                               |
| `started_at`    | TIMESTAMPTZ | —                               |
| `finished_at`   | TIMESTAMPTZ | —                               |

### 4.12 Qdrant Collection

**MVP 每本书一个 version collection：** `scenes_book_{book_id}_v{version}`

```text
vectors:
  text-dense     (1024, cosine)    ← BGE-M3(原文采样文本)
  meta-dense     (1024, cosine)    ← BGE-M3(style/meta_text)
  summary-dense  (1024, cosine)    ← BGE-M3(summary)
  text-sparse    (IDF modifier)    ← jieba 分词 + 词频

payload:
  scene_id,
  book_id,
  version,
  scene_index_in_book,
  chapter_start_index,
  chapter_end_index,
  summary,
  style_summary,
  usage_hint,
  scene_type,
  technique,
  style_tags,
  emotion_tags,
  key_images,
  scene_type_display,
  technique_display,
  style_tags_display,
  emotion_tags_display,
  key_images_display,
  narrative_func,
  char_count
```

**创建时机：** 切分完成后创建对应 version collection；重跑同一 version 时重建，防止原 selected、新 archived 的 Point 残留。

**不存原文。** 检索命中后用 `scene_id` 回 PostgreSQL 取。

**准入约束：** Qdrant Point 必须满足 `reference_status = selected AND annotate_status = annotated AND index_status = indexed`。`archived`、`unevaluated`、`evaluation_failed` 不得进入 collection。

**Collection 策略决策：**

- MVP 采用“每本书一个 collection”，隔离简单，重索引和清理成本低。
- 当 book 数超过 20，或跨书全局检索复杂度明显增加时，迁移到单 collection + `book_id` filter 方案。
------

## 五、上半链路详细流程（已完成首轮实现）

### Step 0：源文件准备与 EPUB 转换

**输入：** `books.source_path` 指向的 `.epub` 或 `.txt`。

**统一接口：**

```python
def convert_epub_to_txt(epub_path: Path, output_path: Path) -> Path:
    """将 EPUB 按阅读顺序转换为 UTF-8 TXT，成功返回 output_path。"""
```

**转换要求：**

1. 按 EPUB `spine` 顺序读取正文，不按 ZIP 内文件名或修改时间排序。
2. 优先使用 EPUB 导航目录中的章节标题；缺失时回退到正文中的 `<h1>`～`<h6>` 或 `<title>`。
3. 删除 `script`、`style`、注释和纯导航页；HTML 实体解码为普通字符。
4. 统一换行符为 `\n`，移除 BOM、全角空格和无效控制字符。
5. 段落之间至少保留一个空行，保证后续章节与场景切分稳定。
6. 输出必须为 UTF-8，且不改变章节顺序和标题文本。
7. 解析失败、正文为空或编码异常时抛出可识别的 `EpubConversionError`，并写入 `books.error_message`。

**幂等与缓存：**

```text
source_sha256 = sha256(原始 EPUB 字节)
cache_key     = source_sha256 + converter_version
cache_path    = data/converted/{source_sha256}.{converter_version}.txt
```

同一缓存键已存在且文件非空时直接复用，不重复转换。

**全量转换与模型输入边界：**

- 允许将整本 EPUB 全量转换为 TXT，并保留在 `data/converted/` 或测试临时目录。
- 全量 TXT 只作为本地中间产物，用于章节切分、场景切分和截断样本生成。
- 禁止把整本 TXT、`chapters.raw_text` 或未切分的全文直接拼进 LLM Prompt。
- 禁止把整本 TXT 作为单次 Embedding 输入。
- LLM 标注和 Embedding 只能处理单个 chapter / scene，或在场景超长时处理有界 `sample_text`。

### Step 1：拆书

```text
输入：book.txt / converted/{source_sha256}.{converter_version}.txt + book_id

1. 清洗格式噪声（BOM、全角空格、空行统一）
2. 正则切章 → chapters 列表（记录章节标题位置）
3. 全文按空行切段落 → 全局 paragraphs 列表
4. 规则扫描，标记候选边界：
   - 时间词开头
   - 地点切换词
   - 章节标题之后
   - 分隔符
5. LLM 精判候选边界：
   - 普通边界：判断是否切场景
   - 章节边界：判断是否跨章合并（默认切，明确同一场景才合并）
6. 按最终边界切场景
7. 幂等 upsert chapters + scenes
8. 创建当前 version 的 Qdrant collection
9. 更新 books.status = 'annotating'
```

**产出：** chapters 表 N 行，scenes 表 M 行，`reference_status = 'unevaluated'`、`annotate_status = 'pending'`、`index_status = 'pending'`。所有 Final Scene 先进入 PostgreSQL Archive Layer。

**幂等要求：** 同一 `book_id + version + scene_index_in_book` 重复执行应覆盖同一条 scene，不产生重复行。

### Step 2：Reference Evaluation

Reference Evaluator 在 Final Scene 和 Deep Annotation 之间运行，只判断该 Scene 是否具有明确、独立、可迁移的 Writing Reference Value，不复制深度标注工作，也不把 selected / archived 等同于文学质量好坏。

**输入：** 单个 Final Scene；跨 Chapter Scene 作为一个整体输入，不在本阶段再次拆分。超长 Scene 复用 head / middle / tail 有界采样。

**判断维度：** `prose_quality`、`technique_value`、`scene_completeness`、`context_independence`、`distinctiveness`、`reference_value`，每项 0.0–5.0。核心结论仍是 `selected / archived`，不由总分机械代替。

**结构化输出：**

```json
{
  "reference_status": "selected",
  "reference_score": 4.6,
  "reference_reason": "通过短句、动作停顿和信息延迟提升冲突，具有明确可迁移的对白节奏价值。",
  "dimensions": {
    "prose_quality": 4.0,
    "technique_value": 4.8,
    "scene_completeness": 4.5,
    "context_independence": 4.0,
    "distinctiveness": 4.2,
    "reference_value": 4.8
  }
}
```

**状态流程：**

```text
unevaluated → evaluating → selected
                          └→ archived
                          └→ evaluation_failed
```

- `archived`：正常完成；保留 PostgreSQL，设 `annotate_status/index_status = not_applicable`，不写错误，不触发重试。
- `selected`：保持 `annotate_status/index_status = pending`，进入后续阶段。
- `evaluation_failed`：记录错误，不得当作 archived，不得进入 Deep Annotation / Embedding。现有容错语义保持不变：本版本有评估失败时整书本次新版本不激活，旧版本继续可用。

**缓存键：** `sha256(model + reference_prompt_version + reference_rule_version + final_input)`，使用独立 `reference_evaluation_cache`。

**可观测性：** `evaluate` 作为独立 index job；完成日志记录 `book_id`、`version`、`total_scenes`、`selected`、`archived`、`failed`、`selection_rate`、`latency_ms`、`model`、`reference_rule_version`，不输出原文。

### Step 3：Deep Annotation（selected only）

**标注字段：**

| 字段              | 控制方式       | 说明                                   |
| :---------------- | :------------- | :------------------------------------- |
| `summary`         | 自由           | 剧情摘要，50–150 字                    |
| `style_summary`   | 自由           | 风格摘要，30–120 字，说明写作参考价值  |
| `usage_hint`      | 自由           | 使用提示，20–80 字                     |
| `scene_type`      | canonical 受控 | 1–3 个，映射到 `tag_vocab`             |
| `technique`       | canonical 受控 | 1–4 个，映射到 `tag_vocab`             |
| `style_tags`      | canonical 受控 | 3–6 个，映射到 `tag_vocab`             |
| `emotion_tags`    | canonical 受控 | 2–4 个，映射到 `tag_vocab`             |
| `key_images`      | canonical 受控 | 1–5 个，尽量映射到 `tag_vocab`         |
| `narrative_func`  | 严格受控       | 从词表选 1 个                          |
| `meta_json`       | 存档           | LLM 原始输出                           |

**标签二级分离：**

- LLM 原始表达进入 `*_display` 字段，例如“冷硬短句”“情绪克制”。
- 系统根据 `tag_vocab` 映射为 canonical tags，例如 `short_sentence`、`restrained`。
- 无法映射的标签不进入受控字段，只保留在 display 和 `meta_json`。
- `narrative_func` 仍然严格受控。

**模型输入红线：**

- 每次 LLM 调用只允许携带一个 scene，不允许携带全书上下文。
- 超长 scene 必须使用与 Step 3 相同的有界采样策略，先缩到 `MAX_LLM_INPUT_CHARS` 以内。
- 禁止直接使用 `chapters.raw_text`、完整 TXT 或整本书拼接文本作为 Prompt。
- 有界采样后仍超过 `MAX_LLM_INPUT_CHARS` 时抛出 `ModelInputTooLargeError`，不得静默截断到不可预测的位置。

**流程：**

```text
1. 查 `reference_status = 'selected' AND annotate_status = 'pending'` 的场景
2. 分批，并发 10
3. 每场景：
   a. 构造有界 final_input；超限场景使用开头 / 中间 / 结尾采样
   b. 算 input_hash = sha256(model + prompt_version + tag_vocab_version + final_input)
   c. 查 annotation_cache，命中则直接用
   d. 未命中 → 调 LLM API → Pydantic 校验 → tag_vocab 映射
   e. 成功 → 幂等 upsert scenes + 写缓存
   f. 失败 → 记录 error_message，标记 failed_permanent
4. 更新 books.status = 'indexing'
```

**失败策略：**

- LLM 传输层对 timeout / connection_error / HTTP 408 / 429 / 5xx 做有限指数退避重试；默认额外重试 2 次。
- 非可重试 4xx、JSON 结构错误和 Pydantic 校验错误不自动重试。
- 重试耗尽后才由当前阶段记录 `error_message` 和失败状态；重新启动索引时可依靠已成功缓存只补跑失败 Scene。
- `archived` 是 Reference Evaluation 的正常结果，不进入任何失败重试。

### Step 4：向量化（selected + annotated only）

**长文本采样策略：**

MVP 不拆 passage。若场景原文超过采样阈值，则取开头、中间、结尾三段拼接为 `sample_text`。

```python
HEAD_CHARS = 1200
MIDDLE_CHARS = 1200
TAIL_CHARS = 1200
LIMIT = HEAD_CHARS + MIDDLE_CHARS + TAIL_CHARS

if len(text) <= LIMIT:
    sample_text = text
else:
    head = text[:HEAD_CHARS]
    middle_start = (len(text) - MIDDLE_CHARS) // 2
    middle = text[middle_start : middle_start + MIDDLE_CHARS]
    tail = text[-TAIL_CHARS:]
    sample_text = f"{head}\n...\n{middle}\n...\n{tail}"
```

`text-dense` 和 `text-sparse` 均使用 `sample_text`。完整章节和场景原文保留在 PostgreSQL，但禁止把整本书或未切分的全文直接送入 BGE-M3。

`sample_text` 的总长度不得超过 `MAX_EMBED_INPUT_CHARS`；若配置冲突，以较小值为准。

**向量构造：**

| 向量             | 输入                         | 模型         | 维度 |
| :--------------- | :--------------------------- | :----------- | :--- |
| `text-dense`     | `sample_text`                | BGE-M3       | 1024 |
| `meta-dense`     | `meta_text`                  | BGE-M3       | 1024 |
| `summary-dense`  | `summary`                    | BGE-M3       | 1024 |
| `text-sparse`    | `sample_text` 分词           | jieba + 词频 | 稀疏 |

**BGE-M3 调用：**

```text
POST http://172.16.43.125:11434/api/embed
{
  "model": "bge-m3",
  "input": ["文本1", "文本2", "文本3"]
}
→ {"embeddings": [[...], [...], [...]]}
```

支持批量，一次调用可编码多个文本。

**`meta_text` 拼接模板：**

`meta_text` 只保存风格与技法信息，不混入剧情摘要。剧情摘要单独进入 `summary-dense`。

```text
风格摘要：{style_summary}
使用提示：{usage_hint}
场景类型：{scene_type}
写作技法：{technique}
文风：{style_tags}
情绪：{emotion_tags}
意象：{key_images}
叙事功能：{narrative_func}
```

**稀疏向量构造：**

```python
import math
from collections import Counter

STOPWORDS = set()
IMPORTANT_SINGLE_CHARS = set("雨夜血刀雾雪火冷热痛死逃刀枪门窗灯火")

tokens = jieba.lcut(sample_text)
tokens = [
    t for t in tokens
    if t not in STOPWORDS
    and (len(t) > 1 or t in IMPORTANT_SINGLE_CHARS)
]

counter = Counter(tokens)
indices = [token_to_id[t] for t in counter if t in token_to_id]
values = [1.0 + math.log(count) for t, count in counter.items() if t in token_to_id]
```

**缓存：** `embedding_cache` 按 `input_hash = sha256(model + text)`。

**并发：** Ollama 调用并发 3。

### Step 5：存储

**PostgreSQL 写入：**

```sql
INSERT INTO scenes (
    id, book_id, scene_index_in_book, chapter_start_index, chapter_end_index,
    text, char_count, split_reason, is_cross_chapter,
    summary, style_summary, usage_hint,
    scene_type, scene_type_display,
    technique, technique_display,
    style_tags, style_tags_display,
    emotion_tags, emotion_tags_display,
    key_images, key_images_display,
    narrative_func, meta_json,
    annotate_status, index_status, error_message,
    version, is_active, created_at, updated_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9,
    $10, $11, $12,
    $13, $14, $15, $16, $17, $18, $19, $20, $21, $22,
    $23, $24, $25, $26, $27, $28, $29, now(), now()
)
ON CONFLICT (book_id, scene_index_in_book, version)
DO UPDATE SET
    text = EXCLUDED.text,
    summary = EXCLUDED.summary,
    style_summary = EXCLUDED.style_summary,
    usage_hint = EXCLUDED.usage_hint,
    scene_type = EXCLUDED.scene_type,
    scene_type_display = EXCLUDED.scene_type_display,
    technique = EXCLUDED.technique,
    technique_display = EXCLUDED.technique_display,
    style_tags = EXCLUDED.style_tags,
    style_tags_display = EXCLUDED.style_tags_display,
    emotion_tags = EXCLUDED.emotion_tags,
    emotion_tags_display = EXCLUDED.emotion_tags_display,
    key_images = EXCLUDED.key_images,
    key_images_display = EXCLUDED.key_images_display,
    narrative_func = EXCLUDED.narrative_func,
    meta_json = EXCLUDED.meta_json,
    annotate_status = EXCLUDED.annotate_status,
    index_status = EXCLUDED.index_status,
    error_message = EXCLUDED.error_message,
    updated_at = now();
```

**Qdrant 写入：**

```python
qdrant.upsert(
    collection_name=f"scenes_book_{book_id}_v{version}",
    points=[PointStruct(
        id=scene.id,
        vector={
            "text-dense": text_vec,
            "meta-dense": meta_vec,
            "summary-dense": summary_vec,
            "text-sparse": sparse_vec
        },
        payload={
            "scene_id": scene.id,
            "book_id": book_id,
            "version": version,
            "scene_index_in_book": scene.scene_index_in_book,
            "chapter_start_index": scene.chapter_start_index,
            "chapter_end_index": scene.chapter_end_index,
            "summary": scene.summary,
            "style_summary": scene.style_summary,
            "usage_hint": scene.usage_hint,
            "scene_type": scene.scene_type,
            "technique": scene.technique,
            "style_tags": scene.style_tags,
            "emotion_tags": scene.emotion_tags,
            "key_images": scene.key_images,
            "scene_type_display": scene.scene_type_display,
            "technique_display": scene.technique_display,
            "style_tags_display": scene.style_tags_display,
            "emotion_tags_display": scene.emotion_tags_display,
            "key_images_display": scene.key_images_display,
            "narrative_func": scene.narrative_func,
            "char_count": scene.char_count
        }
    )]
)
```

**一致性切换流程：**

```text
1. 使用新 version 准备 scenes，is_active = false
2. 创建新 Qdrant collection：scenes_book_{book_id}_v{version}
3. 幂等写入 PostgreSQL + Qdrant
4. 校验 PostgreSQL 中 `selected + annotated + indexed` 的 Scene 数与 Qdrant points 数一致；不再与全量 Scene 数比较
5. 开启事务：
   - 旧 version scenes.is_active = false
   - 新 version scenes.is_active = true
   - books.current_version = version
   - books.status = 'ready'
6. 校验或切换失败时，保留旧 version 可用，新 collection 可删除后重跑
```
------

## 六、下半链路详细流程（本批实现基线）

> 本章是本批后续开发的直接验收依据。范围止于单书查询解析、四路召回、无权重 RRF、原文回表与可解释展示；不包含 Rerank、跨书聚合实现或上下文组装。

### 6.1 查询解析

**输入：** 自然语言写作意图

**输出：**

```json
{
  "raw_intent": "写一段雨夜追杀的紧张场景，冷峻短句",
  "summary_query": "雨夜中的追杀与逃亡，持续制造紧张感",
  "scene_type": ["chase", "rain_night"],
  "technique": ["environmental_description"],
  "style_tags": ["cold", "short_sentence", "restrained"],
  "emotion_tags": ["tension"],
  "key_images": ["rain", "neon"]
}
```

**规则：**

- 空字段不推断，不参与检索。
- `raw_intent` 永远保留，作为 text-dense / sparse 的查询输入及所有降级路径的事实来源。
- `summary_query` 只改写剧情与叙事需求，不加入未在原意中出现的人物、事件或结局；为空时回退 `raw_intent`。
- 结构化字段必须映射到 `tag_vocab` 的 canonical tags。
- 解析失败 → 退化为纯 `raw_intent` 检索。
- 候选词表与标注共用一套 `tag_vocab`。
- 本批解析结果不产生 hard/soft/negative 权重字段，也不驱动人工加权。
- 解析失败或结构化字段为空时，meta-dense 与 summary-dense 的查询文本也回退为 `raw_intent`，保证四路仍可独立运行并展示。
- 查询解析成功后写入 PostgreSQL `query_parsing_cache`，缓存键包含模型、Prompt 版本、词表版本和规范化后的原始查询；缓存命中时不调用 LLM。解析失败不缓存。

### 6.2 查询向量构造

| 向量                 | 输入                         | 模型   |
| :------------------- | :--------------------------- | :----- |
| `query-dense`        | `raw_intent`                 | BGE-M3 |
| `query-meta-dense`   | 结构化查询拼成的 style/meta_text | BGE-M3 |
| `query-summary-dense`| `summary_query`，为空时使用 `raw_intent` | BGE-M3 |
| `query-sparse`       | `raw_intent` 分词            | jieba  |

`query-meta-dense` 只拼风格、技法、情绪、意象等信息，不混入无关剧情。

### 6.3 多路召回

**检索作用域：** 本批一次请求只解析一个 `book_id + version`，只访问该版本对应的 `scenes_book_{book_id}_v{version}` collection。若省略 version，服务端解析为 `books.current_version`，并在响应中返回实际 version。

**四路必须分别执行并保留结果：**

| 路由名 | Qdrant 向量 | 查询输入 | 默认 Top-N |
| :----- | :---------- | :------- | :--------- |
| `text_dense` | `text-dense` | `raw_intent` | 20 |
| `meta_dense` | `meta-dense` | 结构化风格、技法、情绪、意象文本 | 20 |
| `summary_dense` | `summary-dense` | `summary_query`，为空时使用 `raw_intent` | 20 |
| `text_sparse` | `text-sparse` | `raw_intent` 经现有 jieba/token_map 管道 | 20 |

约束：

- 每一路返回自己的 rank、Qdrant 原始 score 和 Scene 标识，不把不同向量空间的 score 直接相加或比较。
- 四路即使没有命中也必须出现在响应中，结果数组为空并携带耗时与错误状态。
- 稀疏查询全部词项都不在目标书的 `token_map` 时，不向 Qdrant 发送空向量；该路返回 `status=skipped`、`reason=no_known_tokens` 和空结果。有效稀疏向量检索后零命中为 `status=ok`，服务异常为 `status=failed`。
- 只召回 `reference_status = selected` 且属于请求 version 的 Point。
- 单路失败时保留其他路结果，并使用成功路由继续产生 RRF；失败路由必须进入 `degraded_routes`。四路全部失败时整个请求失败，不返回伪造的空成功结果。

### 6.4 无权重 RRF 融合

本批采用标准 Reciprocal Rank Fusion，不引入任何路由权重、书籍权重或标签加权：

```text
rrf_score(scene) = Σ 1 / (RRF_K + rank_route(scene))
```

- `RRF_K` 默认 60，可配置，但所有路由使用同一个值。
- 同一 Scene 在同一路由只计一次。
- 未命中的路由不贡献分数。
- 同分时依次按：命中路由数降序、最佳单路 rank 升序、`scene_id` 升序，保证结果确定性。
- 融合结果必须返回 `route_ranks` 和逐路 `rrf_contributions`，便于看板解释排序来源。
- 本批不在 RRF 后调用 Reranker。

### 6.5 PostgreSQL 回表与响应

RRF 只处理 Qdrant 中的候选标识。融合结果返回 `scene_id + book_id + version`、有界预览及必要元数据，不返回完整原文。需要阅读全文时按这三个键单独查询 PostgreSQL；Qdrant payload 不作为完整原文的唯一事实来源。本批不引入更小粒度的片段 ID。

响应必须包含：

```json
{
  "book_id": 1,
  "version": 3,
  "raw_intent": "写一段雨夜追杀的紧张场景，冷峻短句",
  "parsed_query": {},
  "routes": {
    "text_dense": {"items": [], "latency_ms": 0},
    "meta_dense": {"items": [], "latency_ms": 0},
    "summary_dense": {"items": [], "latency_ms": 0},
    "text_sparse": {"items": [], "latency_ms": 0}
  },
  "rrf": {
    "k": 60,
    "items": [
      {
        "scene_id": 42,
        "rrf_score": 0.0476,
        "route_ranks": {"text_dense": 1, "summary_dense": 2},
        "rrf_contributions": {
          "text_dense": 0.01639,
          "summary_dense": 0.01613
        },
        "text_preview": "……",
        "summary": "……",
        "style_summary": "……",
        "usage_hint": "……"
      }
    ]
  },
  "degraded_routes": [],
  "latency_ms": 0
}
```

本批不做自动上下文拼接。列表预览仅为界面展示限长；调用方可按 `scene_id` 从 Scene 详情接口获取完整原文。

### 6.6 实时索引观察台

既有静态 Demo 的视觉与信息架构继续保留，但数据源改为实时只读 API。

**稳定输入键：**

- `book_id`：必需；标识书籍，不依赖标题或文件路径。
- `version`：建议显式传入；省略时读取 `books.current_version`，响应必须回显解析后的版本。
- `scene_id`：用于场景详情与检索结果联动。

**观察能力：**

- 书籍和版本概览、章节/Scene 导航。
- selected / archived / evaluation_failed 漏斗及筛选。
- Reference Evaluation 分数、理由和 Deep Annotation 字段。
- 标签分布、Scene 长度分布、索引阶段状态和缓存/失败统计。
- 向量二维投影、相似 Scene、版本差异。
- PostgreSQL selected-indexed 数与 Qdrant Point 数一致性。
- 自然语言查询页：结构化解析、四路 Top-N 并列列、RRF 列及同一 Scene 联动高亮。

**低耦合约束：**

- 看板不直接连接 PostgreSQL、Qdrant 或读取服务端文件，只调用版本化 HTTP API。
- 看板不触发索引、不编辑标签、不修改 Scene；本批所有交互均为只读观察或无副作用查询。
- API 返回领域 DTO，不向前端暴露 SQLAlchemy Row、Qdrant SDK 对象或 collection 内部实现。
- 原 `data.js` 导出仅保留为离线快照/演示能力，不作为实时看板的运行依赖。
- 向量二维坐标是特定 `book_id + version + vector_name` 下的投影，不得把不同版本的坐标轴直接当作可比较指标。

### 6.7 后续扩展位（本批不实现）

- **多书检索：** 高层聚合器接收多个 `book_id`，并行调用稳定的单书检索服务，再统一展示或融合。底层单书检索器不感知其他 collection。
- **权重策略：** 暂不设计；不预埋 book/style/tag 等业务权重字段。
- **Rerank：** 保留现有模型调研结论，但本批不接入请求链路。
- **上下文预算控制：** 状态为 TBD；不作为本批接口、实现或验收内容。
- **会话：** 可在未来作为拆书模块能力增加，不与当前无状态检索 API 绑定。
------

## 七、关键决策汇总

| 决策点                 | 结论                                                         |
| :--------------------- | :----------------------------------------------------------- |
| 本轮输入格式           | `.epub` / `.txt`；EPUB 必须先转换为 UTF-8 TXT                |
| EPUB 转换实现          | `ebooklib` + `BeautifulSoup(lxml)`，按 spine 顺序输出         |
| EPUB 转换缓存          | `source_sha256 + converter_version`，缓存于 `data/converted/` |
| add_new_book 语义      | 只登记，不解析；返回 `book_id`，状态为 `pending`              |
| 索引启动               | 独立 `POST /books/{book_id}/index`，显式启动上半链路          |
| 场景切分               | 全局切场景，章节只作标签                                     |
| 跨章场景               | LLM 判断，默认切，明确同一场景才合并                         |
| 超长子段               | MVP 不拆 passage，采用开头 / 中间 / 结尾截断采样             |
| 角色/地点/时间字段     | 不设独立字段                                                 |
| 标注字段               | summary / style_summary / usage_hint + 受控标签 + meta_json  |
| 数据分层               | PostgreSQL Archive Layer 保存全量 Scene；Qdrant Reference Layer 只存 selected |
| 范本准入               | Final Scene 先经独立 Reference Evaluation，再决定 selected / archived |
| archived 语义          | 正常小说结构资产，不是失败，不删除、不标注、不建向量 |
| 标签受控程度           | canonical tags 受控，display tags 展示；映射到 `tag_vocab`    |
| 标注失败               | 当前直接记录；后续规划指数退避重试                           |
| 向量类型               | text-dense + meta-dense + summary-dense + text-sparse        |
| 摘要向量               | 摘要单独做 `summary-dense`，不混入 `meta-dense`              |
| 稀疏向量               | jieba + 停用词/重要单字 + 子线性词频 + Qdrant IDF            |
| Qdrant 组织            | MVP 每本书一个 version collection                            |
| Collection 迁移        | book 数超过 20 或全局检索复杂时，迁移单 collection + filter   |
| 单书检索边界           | 本批按 `book_id + version` 检索一个 collection               |
| 跨书检索               | 未来由高层并行聚合单书结果；本批不实现                       |
| 权重策略               | 本批不设计、不预埋业务权重                                   |
| 四路结果               | text/meta/summary dense + sparse 各自保留 Top-N              |
| 融合排序               | 标准无权重 RRF；返回逐路 rank 与贡献                         |
| 同步机制               | 版本化 collection + 校验后切换，失败可重跑                   |
| 外键                   | 不用                                                         |
| 上下半链路             | 分开运行；在线链路只读消费已激活索引                         |
| 组件级直查             | PG 按主键查询；Qdrant 接收原始向量直查，不含业务检索          |
| 自然语言检索           | 本批实现选定单书的结构化解析、四路召回与 RRF                 |
| 实时观察台             | 按 `book_id + version` 调用只读 API，不依赖静态导出          |
| Rerank                  | 本批不实现                                                   |
| 上下文预算控制         | TBD；本批忽略                                                 |
| EPUB 全量转换          | 允许；完整 TXT 可作为 `data/converted/` 本地缓存或测试中间产物 |
| 全量文本进入模型       | 禁止；LLM / Embedding 只能接收分章、分场景或有界采样后的输入  |
| 测试数据               | 模型调用测试只允许 4000 字微型小说或开头/中间/结尾截断书     |
| 开发方式               | 小组件先行；单测通过后再组装大模块                           |
| 可观测性               | 日志（结构化 JSON）                                          |
| Prompt 管理            | `prompts/` 文件夹                                            |
| 对外 API               | health、add_new_book、start_index、PG/Qdrant 组件级直查       |
| MinIO / RustFS         | 不使用，已移除                                               |
| 风格量化特征           | 本轮不实现                                                   |
| 多样性控制             | 本轮不实现，后续再评估                                       |
| 检索质量评估           | 本批建立人工查询集并展示各路差异；先产基线，不设权重          |

------

## 八、重新索引流程

触发条件：改了切分规则、Reference Evaluation Prompt / Rule、Deep Annotation Prompt、向量模型、`tag_vocab` 等。

```text
1. new_version = books.current_version + 1
2. 准备新 version 的 scenes，is_active = false
3. 重新运行 Reference Evaluation；只对新 selected Scene 运行 Deep Annotation / Embedding
4. 创建或重建新 Qdrant collection：scenes_book_{book_id}_v{new_version}
5. 幂等写入 PostgreSQL Archive Layer，并只把 selected Scene 写入 Qdrant
6. 校验 PostgreSQL `selected + annotated + indexed` 数与 Qdrant points 数一致
7. 开启事务：
   - 旧 version scenes.is_active = false
   - 新 version scenes.is_active = true
   - books.current_version = new_version
   - books.status = 'ready'
8. 旧 collection 保留一段时间后手动清理
```

**失败处理：**

- 新 version 未切换成功前，旧 version 始终可用。
- 新 version 失败时，可以删除新 collection 并重跑。
- 所有 upsert 保持幂等，避免重复执行产生脏数据。

**归档约束：** 不自动删除 archived Scene。旧 version collection 的清理与 PostgreSQL Archive Layer 的数据保留是两个独立决策。

------

## 九、Prompt 管理

**目录结构：**

```text
app/prompts/
  reference_evaluation/
    v1.py           # 轻量准入评估，与深度标注解耦
  annotation/
    v1.py           # PROMPT_VERSION = "v1.0", SYSTEM_PROMPT = "..."
  query_parsing/
    v1.py           # 自然语言写作需求 → 结构化查询
```

**规则：**

- 改 prompt 新建 `v2.py`，不改 `v1.py`。
- Reference Evaluation 缓存键包含 `model + prompt_version + rule_version + final_input`，不与 Deep Annotation 共用缓存。
- Deep Annotation 缓存键包含 `model + prompt_version + tag_vocab_version + final_input`。
- 标注 prompt 必须要求输出 canonical/display 分层字段，或输出原始标签由后端映射。
- 本批新增版本化 `query_parsing/` Prompt；解析失败必须退化为 `raw_intent`。
- Rerank Prompt 本批不进入请求链路；其后续版本独立于查询解析 Prompt。

------

## 十、可观测性

**通过日志维护，结构化 JSON 格式，输出到 stdout。**

关键事件日志字段：

| 事件         | 字段                                                         |
| :----------- | :----------------------------------------------------------- |
| EPUB 转换    | book_id, source_sha256, converter_version, cache_hit, latency_ms |
| 索引启动     | book_id, version, total_scenes                               |
| 切分完成     | book_id, version, chapters, scenes                           |
| Reference Evaluation 完成 | book_id, version, total_scenes, selected, archived, failed, selection_rate, latency_ms, model, reference_rule_version |
| 标注完成     | book_id, version, success, failed, tag_unmapped             |
| 向量化完成   | book_id, version, indexed, failed                            |
| 一致性校验   | book_id, version, selected_indexed_count, qdrant_count, total_scenes, ok |
| 索引完成     | book_id, version, ready                                      |
| 组件直查     | storage, collection, operation, latency_ms                   |
| 看板读取     | book_id, version, resource, row_count, latency_ms             |
| 查询解析     | book_id, version, model, prompt_version, fallback, latency_ms |
| 单路召回     | book_id, version, route, top_n, hit_count, latency_ms, error  |
| RRF 融合     | book_id, version, rrf_k, candidate_count, output_count, degraded_routes, latency_ms |
| LLM 调用     | purpose, model, prompt_tokens, completion_tokens, latency_ms |
| 错误         | stage, error_type, message                                   |

**MVP 阶段不做指标打点，不做 Prometheus 集成。** 日志即可满足调试和排查需求。

------

## 十一、对外 API（现有能力 + 本批新增）

Base path 为 `/api/v1`；`/health` 不放在版本前缀下，便于外部探活。

### 11.1 API 边界

API 分为现有上半链路、低层组件直查、本批实时观察和单书自然语言检索：

- `add_new_book` 只登记书籍元信息，不立即转换、拆书或调用 LLM。
- EPUB 转换、拆书、标注、向量化由显式索引接口启动。
- PostgreSQL 直查只按 `book_id`、`chapter_index`、`scene_id` 等主键/索引字段查询。
- Qdrant 组件直查继续只接收调用方提供的原始向量，不承担业务检索。
- 实时观察 API 以 `book_id + version` 为稳定边界，只读返回领域 DTO。
- 单书自然语言检索 API 负责查询解析、四路召回和无权重 RRF，不做 Rerank、跨书聚合或上下文组装。

### 11.2 接口清单

| 能力                  | 方法与路径                                                     | 本轮状态 | 说明 |
| :-------------------- | :------------------------------------------------------------- | :------- | :--- |
| health                | `GET /health`                                                   | 实现     | 服务与依赖健康检查 |
| add_new_book          | `POST /api/v1/books`                                            | 实现     | 注册 `.epub` / `.txt`，返回 `book_id` |
| start_index           | `POST /api/v1/books/{book_id}/index`                            | 实现     | 显式启动上半链路索引任务 |
| get_book              | `GET /api/v1/books/{book_id}`                                   | 实现     | PostgreSQL 查询书籍状态和索引进度 |
| get_chapter           | `GET /api/v1/books/{book_id}/chapters/{chapter_index}`          | 实现     | PostgreSQL 直查章节 |
| get_scene             | `GET /api/v1/scenes/{scene_id}`                                 | 实现     | PostgreSQL 直查场景及元数据 |
| qdrant_direct_search  | `POST /api/v1/qdrant/collections/{collection_name}/points/search` | 实现   | 使用原始向量直查 Qdrant |
| qdrant_get_point      | `GET /api/v1/qdrant/collections/{collection_name}/points/{point_id}` | 实现 | 按 point id 直查 Qdrant |
| list_books            | `GET /api/v1/books`                                            | 本批新增 | 看板选择书籍，可按状态分页 |
| list_book_versions    | `GET /api/v1/books/{book_id}/versions`                         | 本批新增 | 返回可观察版本及 current_version |
| get_index_overview    | `GET /api/v1/books/{book_id}/versions/{version}/overview`      | 本批新增 | 漏斗、统计、一致性、管线状态 |
| list_version_scenes   | `GET /api/v1/books/{book_id}/versions/{version}/scenes`        | 本批新增 | 分页、按章节和 reference_status 筛选 |
| get_vector_projection | `GET /api/v1/books/{book_id}/versions/{version}/projection`    | 本批新增 | 指定向量的二维投影与近邻展示数据 |
| search_book           | `POST /api/v1/books/{book_id}/search`                          | 本批新增 | 自然语言解析、四路 Top-N、无权重 RRF |

### 11.3 add_new_book

```http
POST /api/v1/books
Content-Type: application/json

{
  "source_path": "E:/novels/tool/备用联系人.epub",
  "title": "备用联系人",
  "author": ""
}
```

处理规则：

1. 校验路径存在，且必须位于 `ALLOWED_SOURCE_ROOTS` 白名单内。
2. 根据后缀和文件签名识别 `epub` / `txt`，不信任客户端声明的格式。
3. 计算 `source_sha256`；相同哈希已登记时直接返回已有 `book_id`。
4. 只写入 `books`，状态固定为 `pending`。
5. 不转换、不解析、不创建 collection、不调用 LLM 或 Ollama。

成功返回：

```json
{
  "book_id": 1,
  "status": "pending",
  "source_format": "epub",
  "index_started": false
}
```

### 11.4 start_index

```http
POST /api/v1/books/1/index
```

处理规则：

- `pending` / `failed` / `ready`：创建新 version 索引任务并返回 `202 Accepted`；`ready` 表示显式重索引，旧 version 在新 version 激活前保持可用。
- EPUB：`converting → splitting → evaluating → annotating → indexing → ready`。
- TXT：跳过 `converting`，直接从 `splitting` 开始。
- 已处于运行态时返回当前状态，不重复启动同一任务。

### 11.5 PostgreSQL / Qdrant 组件级直查

**PostgreSQL 直查：**

- `GET /api/v1/books/{book_id}`
- `GET /api/v1/books/{book_id}/chapters/{chapter_index}`
- `GET /api/v1/scenes/{scene_id}`

只执行仓储层查询和序列化，不进行标签推理、跨表模糊匹配或业务排序。

**Qdrant 直查：**

```http
POST /api/v1/qdrant/collections/scenes_book_1_v1/points/search
Content-Type: application/json

{
  "vector_name": "text-dense",
  "vector": [0.01, 0.02, 0.03],
  "limit": 10,
  "with_payload": true,
  "filter": {}
}
```

约束：

- 只允许访问符合 `scenes_book_*` 命名规则的 collection。
- `vector` 必须由调用方提供，本服务不把查询文本转成向量。
- 不合并多路结果、不做 RRF、不调用 reranker、不组装上下文。
- 返回 Qdrant 原始命中结果，作为组件验证和调试接口。

### 11.6 实时观察 API

实时观察接口统一遵守：

- `book_id` 和 `version` 必须先在 PostgreSQL 验证存在。
- 默认只允许观察已经生成 Scene 的版本；未完成版本可返回阶段状态，但不得伪装为可检索版本。
- 列表接口必须分页，不一次返回整库全文。
- Scene 详情按需获取；概览接口不携带完整原文。
- 投影接口接收 `vector_name=text-dense|meta-dense|summary-dense`，返回二维坐标，不返回全部 1024 维向量。
- 所有响应回显 `book_id`、`version` 和生成时间，避免前端混用版本。

### 11.7 单书自然语言检索 API

```http
POST /api/v1/books/1/search
Content-Type: application/json

{
  "query": "写一段雨夜追杀的紧张场景，冷峻短句",
  "version": 3,
  "route_top_n": 20,
  "rrf_top_n": 20
}
```

处理规则：

1. 验证 book/version 与 `scenes_book_{book_id}_v{version}` 一致；省略 version 时解析 current_version。
2. 结构化解析写作需求；失败时保留 `raw_intent` 并启用降级查询。
3. 构造 text-dense、meta-dense、summary-dense、text-sparse 四类查询表示。
4. 四路分别召回 Top-N，并保留原始 rank、score 和耗时。
5. 使用同一 `RRF_K` 做无权重融合，按确定性规则处理同分。
6. 对融合候选回 PostgreSQL 获取 Scene 原文和元数据。
7. 同时返回结构化查询、四路原始结果、RRF 结果及 degraded_routes。

约束：

- `query` 去除首尾空白后不能为空，并设置明确长度上限。
- `route_top_n`、`rrf_top_n` 必须受服务端上下限约束，不能由客户端请求无界结果。
- 请求可写独立的 `query_parsing_cache` 持久缓存；不得修改 books、chapters、scenes、token_map 或 Qdrant Point，不记录用户会话。
- 不接受 `book_weight`、`route_weight`、`tag_weight` 等权重参数。
- 本批不接受多个 `book_id`；未来多书端点由高层聚合器另行定义。

### 11.8 health

`GET /health` 检查：

- 应用进程是否可响应。
- PostgreSQL 是否能执行 `SELECT 1`。
- Qdrant 是否可连接及版本是否可读。
- Ollama 是否可连接；只检查服务与模型列表，不实际执行推理。
- LLM 是否已配置；只检查配置完整性，不产生调用费用。

状态语义：

| 状态        | HTTP | 含义 |
| :---------- | :--- | :--- |
| `ok`        | 200  | 所有关键依赖正常 |
| `degraded`  | 200  | 服务可启动，但 Ollama / LLM 等非关键依赖不可用 |
| `unhealthy` | 503  | PostgreSQL 或 Qdrant 等核心依赖不可用 |

响应示例：

```json
{
  "status": "ok",
  "version": "0.5.0",
  "dependencies": {
    "postgres": {"status": "ok", "latency_ms": 2},
    "qdrant": {"status": "ok", "latency_ms": 3},
    "ollama": {"status": "ok", "latency_ms": 5},
    "llm": {"status": "configured"}
  }
}
```

------

## 十二、组件优先开发与测试策略

### 12.1 开发顺序

禁止从大模块 `indexer.py` 直接开工。按以下顺序逐层开发和验收：

| 顺序 | 组件                  | 主要职责                                  | 完成门槛 |
| :--- | :-------------------- | :---------------------------------------- | :------- |
| 1    | `utils/epub.py`       | EPUB → TXT、章节顺序、缓存键               | 单测通过 |
| 2    | `utils/text.py`       | BOM/空白清洗、章节切分、段落切分           | 单测通过 |
| 3    | `db/qdrant.py`        | collection、upsert、point 直查、删除       | 单测 + 组件集成测试通过 |
| 4    | `db/postgres.py`      | books/chapters/scenes 仓储方法             | 单测 + 测试库集成测试通过 |
| 5    | `clients/ollama_client.py` | Embedding 调用、超时、批量与重试边界 | Mock HTTP 单测通过 |
| 6    | `clients/llm_client.py`    | 标注 API 调用、JSON 校验、缓存键     | Mock HTTP 单测通过 |
| 7    | `services/tagger.py` + `services/sparse.py` | 标签映射、分词、token_map | 单测通过 |
| 8    | `services/splitter.py` + `reference_evaluator.py` + `annotator.py` + `embedder.py` | Scene Resolution、范本准入、深度标注、向量化 | 各自组件测试通过 |
| 9    | `services/indexer.py` | 串联上半链路、状态机、幂等与失败恢复 | 小型 fixture 集成测试通过 |
| 10   | `api/`                | health、书籍登记、索引启动、组件直查 API | API 契约测试通过 |
| 11   | `services/query_parser.py` | 写作需求结构化、canonical tag 映射、raw_intent 降级 | 成功/失败/缓存/越界输入单测通过 |
| 12   | `services/retriever.py` | 单书四路独立召回、部分失败降级、回表 | 四路 Fake + Qdrant 集成测试通过 |
| 13   | `services/fusion.py` | 标准无权重 RRF、贡献解释、确定性同分 | 排名与边界单测通过 |
| 14   | 观察/检索 API         | book/version 观察 DTO、单书 search 契约 | API 契约与只读性测试通过 |
| 15   | 通用数据看板          | 实时观察、四路对比、RRF 联动展示 | 对任意 ready book/version 验收通过 |

每个组件先定义输入输出、异常类型和幂等语义，再写实现和测试。上一行未通过，不进入下一行的大模块组装。

### 12.2 测试数据硬约束

**允许全量 EPUB → TXT 转换；禁止把无界全量文本作为单次模型输入。**

规则如下：

- 全量 EPUB 可以转换为 TXT，并保留在正式转换缓存或测试临时目录。
- 全量 TXT 可用于本地章节切分、场景切分、截取 fixture。
- 真实调用 LLM 或 Embedding 时，禁止把整本书作为一个 Prompt；每次仍必须以 Final Scene 为边界，并受 `MAX_LLM_INPUT_CHARS` / `MAX_EMBED_INPUT_CHARS` 限制。
- 模型调用测试允许以下三类样本：

1. **合成微型小说**
   - 由测试代码确定性生成，不依赖网络。
   - 总量约 4000 字，4～6 章，包含中文字符、标点、空行和章节标题。
   - 用于 EPUB 转换、章节切分、标注、向量化和 API 测试。

2. **开头 / 中间 / 结尾截断书**
   - 从一本已转换 TXT 中取开头 1～2 章、中间 1～2 章、结尾 1～2 章。
   - 拼接成一本新的小型 TXT 后，才允许作为模型调用测试输入。
   - 为构造样本而进行的全量转换不再要求删除完整 TXT。
   - 推荐把“构造截断 fixture”做成独立脚本，并写入 `pytest` 的 `tmp_path`；仓库只提交脚本或极小的已截断样本。

3. **人工指定的真实短篇范本**
   - 仅用于手工触发的真实链路验收，不进入默认 CI。
   - 本轮固定使用 `tests/马之途.txt`，按完整短篇执行章节解析、Scene Resolution、Reference Evaluation、selected-only 深度标注、Embedding 和 Qdrant 写入。
   - 即使完整短篇参与测试，模型调用仍逐 Scene、有界执行，不把整本原文拼进单次 Prompt。
   - 结果只写入 `novel-rag-test-2` 和对应测试 collection，并保留供人工核验；不得删除原 `novel-rag-test`。

这里的“开头 / 中间 / 结尾截断”与 Step 3 的模型输入采样是两个不同功能，不能互相替代：

- 测试截断：构造小型测试书。
- Step 3 采样：对单个超长场景构造 embedding 输入。
- 全量 TXT：只作为本地转换产物或截断来源，不直接进入模型 Prompt。

### 12.3 测试分层

| 层级       | 默认执行 | 依赖                   | 要求 |
| :--------- | :------- | :--------------------- | :--- |
| 单元测试   | 是       | 无网络、无真实服务     | Mock HTTP / 使用内存 Qdrant / 仓储 fake |
| 组件集成   | 否       | 测试 PG、测试 Qdrant   | 使用独立测试库和测试 collection |
| 上半链路联调 | 否     | 测试 PG + Qdrant + 可选 Ollama | 常规使用微型/截断书；手工验收可使用指定真实短篇 |
| 下半链路单元 | 是     | Fake LLM / Fake Embedding / Fake Qdrant | 查询降级、四路结果、RRF 确定性 |
| 下半链路集成 | 否     | 测试 PG + Qdrant + Ollama + 可选 LLM | 选定 book_id 的真实查询，不改索引 |
| 看板验收     | 否     | 运行中的 API 与浏览器   | 实时读取，不依赖 data.js 导出 |

默认执行：

```bash
pytest -m "not integration"
```

组件完成的最低标准：

- 有明确类型标注和异常类型。
- 有成功、失败、重复执行、缓存命中的单测。
- 重跑不会产生重复 chapters、scenes、point 或 collection。
- Reference Evaluation 必须覆盖 archived、selected、evaluation_failed、跨章 Scene 整体评估、Qdrant 只保留 selected、重索引 selected → archived 不残留 Point。
- 查询解析必须覆盖正常结构化、空字段、未知标签、LLM 失败退化为 raw_intent。
- 四路召回必须分别断言 route、rank、原始 score、空结果和单路失败降级。
- RRF 必须覆盖重复 Scene、缺失路由、同分稳定排序，且不得出现任何业务权重。
- 检索集成测试必须断言所有命中属于请求的 `book_id + version` 和 selected Reference Layer。
- 对《马之途》建立不少于 10 条人工写作需求的基线查询集；本批记录各路与 RRF 的人工相关性观察，不以调权作为验收手段。
- 检索前后 PostgreSQL Scene 计数、状态与 Qdrant Point 数必须完全不变。
- 本轮集成测试使用独立 PostgreSQL 库 `novel-rag-test-2`；不删除、不重命名、不清空原 `novel-rag-test`。
- 日志为结构化 JSON，且不泄漏 API Key。
- 本批允许新增 query parser、retriever、fusion 和只读看板；不得新增 assembler、正文生成或用户会话实现。

------

## 十三、目录结构

```text
novel-rag/
  app/
    main.py
    config.py
    api/
      __init__.py
      dependencies.py
      routes/
        health.py
        books.py
        direct_query.py     # PG / Qdrant 组件级直查，不属于下半链路
        observe.py          # book/version 实时只读观察 DTO
        search.py           # 单书自然语言检索 API
    utils/
      __init__.py
      epub.py               # EPUB → TXT、章节顺序、缓存
      text.py               # 文本清洗、章节/段落切分
    services/
      splitter.py           # 拆书
      reference_evaluator.py # 轻量 Writing Reference 准入评估
      annotator.py          # selected Scene 深度标注
      tagger.py             # tag_vocab 映射
      embedder.py           # BGE-M3 调用
      sparse.py             # jieba + token_map
      indexer.py            # 索引编排
      query_parser.py       # 自然语言写作需求结构化与降级
      retriever.py          # 单书四路独立召回与 PG 回表
      fusion.py             # 无权重 RRF 与贡献解释
    db/
      postgres.py
      qdrant.py
      models.py
    clients/
      llm_client.py         # LLM API 调用
      ollama_client.py      # Ollama Embedding 调用（BGE-M3）
    prompts/
      reference_evaluation/
        v1.py
      annotation/
        v1.py
      query_parsing/
        v1.py
    data/
      tag_vocab/
        v1.json
  dashboard/                # 实时只读索引观察台 + Retrieval Lab
  data/
    books/
    converted/              # {source_sha256}.{converter_version}.txt
  migrations/
    001_init.sql
    002_tag_vocab_aliases.sql
    003_reference_evaluation.sql
  tests/
    unit/
      utils/
        test_epub.py
        test_text.py
      db/
        test_qdrant_adapter.py
        test_postgres_repository.py
      clients/
        test_ollama_client.py
        test_llm_client.py
    integration/
      test_postgres_repository.py
      test_indexer_micro_novel.py
      test_epub_conversion.py
      test_qdrant_adapter.py
    fixtures/
      build_synthetic_micro_novel.py
      build_synthetic_epub.py
      build_truncated_book.py
    conftest.py
  .env.example
  docker-compose.yml
  pyproject.toml
  README.md
```

------

## 十四、环境变量约定

```bash
# 服务与数据目录
APP_VERSION=0.8.0
API_PREFIX=/api/v1
BOOK_SOURCE_DIR=E:/novels/tool
ALLOWED_SOURCE_ROOTS=E:/novels/tool
DATA_BOOKS_DIR=data/books
DATA_CONVERTED_DIR=data/converted
EPUB_CONVERTER_VERSION=epub-v1

# PostgreSQL
POSTGRES_HOST=172.16.43.125
POSTGRES_PORT=5432
POSTGRES_DB=novel_rag
POSTGRES_USER=rag
POSTGRES_PASSWORD=
POSTGRES_TEST_DB=novel-rag-test-2   # 本轮独立集成测试库，不触碰原测试库

# Qdrant
QDRANT_URL=http://172.16.43.125:6333

# Ollama
OLLAMA_URL=http://172.16.43.125:11434
OLLAMA_EMBED_MODEL=bge-m3
OLLAMA_RERANK_MODEL=awenleven/Qwen3-Reranker-4B:Q4_K_M

# LLM adapter: openai_compatible | codex_exec
LLM_ADAPTER=openai_compatible
LLM_BASE_URL=          # 手动填写
LLM_API_KEY=           # 手动填写
LLM_MODEL=             # 手动填写
CODEX_EXEC_PATH=codex
CODEX_EXEC_TIMEOUT_SECONDS=1800

# 长文本采样
LONG_TEXT_HEAD_CHARS=1200
LONG_TEXT_MIDDLE_CHARS=1200
LONG_TEXT_TAIL_CHARS=1200

# 模型输入上限（禁止整书全文）
MAX_LLM_INPUT_CHARS=3600
MAX_EMBED_INPUT_CHARS=3600

# 标签
TAG_VOCAB_VERSION=v1
REFERENCE_RULE_VERSION=v1
QUERY_PROMPT_VERSION=v1

# 本批单书检索参数
QUERY_MAX_CHARS=2000
QUERY_ROUTE_TOP_N=20
QUERY_RRF_TOP_N=20
RRF_K=60

```

------

## 十五、部署配置

服务器地址：`172.16.43.125`

```yaml
services:
  postgres:
    image: postgres:17.10
    container_name: postgres
    restart: always
    environment:
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
      POSTGRES_DB: "novel_rag"
      POSTGRES_USER: "rag"
    ports:
      - "5432:5432"
    volumes:
      - /data/postgresql:/var/lib/postgresql/data

  qdrant:
    image: qdrant/qdrant:v1.19.1
    container_name: qdrant
    restart: unless-stopped
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - /qdrant/data:/qdrant/storage

  ollama:
    image: ollama/ollama:0.34.0
    container_name: ollama
    restart: unless-stopped
    ports:
      - "11434:11434"
    volumes:
      - /ollama/data:/root/.ollama
```

**Ollama 已有模型：**

```text
awenleven/Qwen3-Reranker-4B:Q4_K_M    2.5 GB
bge-m3:latest                         1.2 GB
```

------

## 十六、阶段路线、风险与安全边界

### 16.1 阶段路线

| 阶段 | 范围 | 完成标志 |
| :--- | :--- | :------- |
| A | PRD 定稿 | 单书边界、四路契约、RRF、观察 API 和非目标无歧义 |
| B | 实时索引观察台 | 任意 ready `book_id + version` 可实时浏览，不依赖静态导出 |
| C | 单书 Retrieval Lab | 查询解析、四路 Top-N、无权重 RRF、回表和可解释展示闭环 |
| D | 基线评估 | 《马之途》不少于 10 条人工查询形成四路与 RRF 对照记录 |
| 后续 | 多书高层聚合、Rerank、会话等 | 另行定 PRD，不反向侵入单书检索契约 |

### 16.2 主要风险与处理

| 风险 | 影响 | 本批处理 |
| :--- | :--- | :------- |
| 四种向量 score 不可直接比较 | 简单相加会产生误导排序 | 仅使用各路 rank 做无权重 RRF，原始 score 只展示 |
| 查询解析具有随机性或失败 | 四路输入不稳定 | Prompt 版本化、结构校验、缓存；失败统一回退 raw_intent |
| current_version 在请求期间变化 | 看板或检索混用两个版本 | 请求开始解析出确定 version，后续全链路固定并在响应回显 |
| 单路服务失败 | 整体查询不可用或结果被伪装 | 其余成功路继续融合，显式返回 degraded_routes；全失败才报错 |
| 全文列表导致响应过大 | 看板变慢、内存压力 | 概览不带全文，列表分页，Scene 详情按需读取 |
| 稀疏 token_id 按书维护 | 跨书直接拼 sparse 查询不可用 | 本批始终使用目标 book 的 token_map；未来多书仍分别构造和查询 |
| PCA 坐标随样本变化 | 用户误把不同版本坐标当绝对变化 | 投影绑定 book/version/vector_name，并在界面明确不可跨投影直接比较 |
| 无权重 RRF 质量不足 | 融合排名未必优于每个单路 | 本批目标是形成透明基线；不在缺乏评估数据时引入权重 |

### 16.3 安全与隐私

- 看板和检索 API 不暴露数据库凭据、LLM API Key、源文件绝对路径或内部异常栈。
- `book_id`、`version`、collection name 均由服务端校验和解析，客户端不能提交任意 SQL、文件路径或未受控 collection。
- 原文属于知识库内容，访问认证与授权方案当前为 **TBD**；在认证方案确定前仅允许可信内网使用。
- 查询日志默认不记录完整 Scene 原文；自然语言 query 是否保留全文日志为 **TBD**，实现时优先记录哈希、长度和 request id。
- 所有本批接口均无副作用；不得借由看板或检索 API 删除、重建或切换索引版本。

------

## 当前完成状态

| 项                                         | 状态             |
| :----------------------------------------- | :--------------- |
| `migrations/001_init.sql`                  | 已完成           |
| `migrations/002_tag_vocab_aliases.sql`     | 已完成           |
| `migrations/003_reference_evaluation.sql`  | 已完成           |
| `tag_vocab` v1 词表                        | 已完成           |
| `utils/epub.py` + 单元测试                 | 已完成           |
| `utils/text.py` + 单元测试                 | 已完成           |
| `db/qdrant.py` 适配器 + 单元/真实服务测试  | 已完成           |
| `db/postgres.py` 仓储 + 测试库集成测试     | 已完成           |
| `clients/ollama_client.py` + 单元测试      | 已完成           |
| `clients/llm_client.py` + 双层适配测试     | 已完成           |
| `tagger.py` + `sparse.py` + 单元测试       | 已完成           |
| `splitter.py` + `annotator.py` + `embedder.py` | 已完成       |
| `indexer.py` 编排 + 微型小说端到端          | 已完成           |
| health / add_new_book / start_index API    | 已完成           |
| PG / Qdrant 组件级直查 API                 | 已完成           |
| 微型小说、截断书与合成 EPUB fixture        | 已完成           |
| 配置 `.env`                                | 已完成本地配置   |
| `prompts/annotation/` 提示词               | 已完成 v1        |
| `prompts/reference_evaluation/` 提示词     | 已完成 v1        |
| Reference Evaluation + archived/selected gate | 已完成 |
| Qdrant 仅保留 selected Scene              | 已完成 |
| `novel-rag-test-2` 迁移与集成测试       | 4 passed；原测试库未触碰 |
| `马之途.txt` 真实上半链路验收           | 10 章 / 10 Scene；selected 9、archived 1、indexed 9、failed 0 |
| LLM 瞬时错误有限重试                    | 已完成；真实验收覆盖 502 恢复 |
| 全量 EPUB → TXT 转换                       | 允许             |
| 将全量文本直接交给模型                     | 禁止             |
| 实时索引观察台 PRD                         | 已定稿；待开发   |
| 查询解析 + 单书四路召回 PRD                | 已定稿；待开发   |
| 无权重 RRF + 可解释贡献 PRD                | 已定稿；待开发   |
| 多书高层聚合                               | 保留扩展位；本批不开发 |
| Rerank                                     | 本批不开发       |
| 上下文预算控制 / assembler                 | TBD；本批不开发  |
| 自动上下文拼接、正文生成、写作             | 非本项目职责     |
| 用户会话                                   | 后续拆书模块能力 |
| 检索质量评估                               | 本批建立基线查询集，不调权 |

------

**文档版本：** v0.8
**最后更新：** 2026-09-24
**项目名：** novel-rag
