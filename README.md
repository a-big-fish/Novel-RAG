# novel-rag

面向小说写作范本检索的独立服务。上半链路负责构建可检索范本，下半链路提供单书、多书检索实验与实时观察台。

`源文件登记 -> EPUB/TXT 准备 -> 章节/场景拆分 -> Reference Evaluation -> selected Scene 深度标注 -> 向量化 -> PostgreSQL Archive + Qdrant Reference Index -> 组件级直查 API`

PostgreSQL 保留全部 Final Scene；Qdrant 只保留具有写作参考价值的 `selected` Scene。`archived` Scene 是正常小说结构资产，不是失败或废弃数据。

单书检索包含自然语言解析、四路 Top-N、无权重 RRF 与 Scene 详情回查。多书检索共用一次查询解析和批量 Embedding，并发执行各书召回与书内 RRF，再按书内名次轮流汇集候选。全局 Rerank 为可选步骤；上下文组装仍属后续范围。

## 当前状态

- Python 3.11，`uv` 管理依赖
- FastAPI + SQLAlchemy Core + psycopg，不使用 ORM
- PostgreSQL、Qdrant、Ollama 地址由 `.env` 配置
- LLM 支持 `openai_compatible` 与 `codex_exec` 两种适配器
- OpenAI 兼容适配器对 408 / 429 / 5xx 和网络错误做有限指数退避重试
- `add_new_book` 只登记、不解析；`start_index` 显式启动索引
- 模型输入始终经过章节/场景拆分或有界采样，禁止整书进入 LLM
- TXT 章节解析支持普通章节行及 Markdown `#` / `##` / `###` 标题

## 快速开始

```powershell
cd E:\novels\novel-rag
uv sync
Copy-Item .env.example .env
# 填写 POSTGRES_PASSWORD、LLM_BASE_URL、LLM_API_KEY、LLM_MODEL
uv run python -m app.db.migrate migrations/001_init.sql
uv run python -m app.db.migrate migrations/002_tag_vocab_aliases.sql
uv run python -m app.db.migrate migrations/003_reference_evaluation.sql
uv run python -m app.db.migrate migrations/004_query_parsing_cache.sql
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

测试库迁移时增加 `--test`；迁移工具会固定使用 `novel-rag-test-2`，即使 `.env` 指向旧测试库。正式库只在准备部署时执行迁移。

启动后打开 `http://127.0.0.1:8000/dashboard`。页面实时读取书籍、版本、场景和向量投影。在“检索实验”可选当前书籍或多书聚合：提交后依次显示需求解析、查询向量生成、四路召回、书内 RRF 融合、跨书聚合及可选重排的实时阶段。多书结果展示每本书的召回数量、书内 RRF、跨书候选顺序；点击候选可回查完整场景原文和标注。详细事件记录可展开查看。查询解析成功结果写入独立的 PostgreSQL 持久缓存；检索不修改书籍、场景或 Qdrant Point。

### 预览隔离库中的真实书籍

本地隔离库 `novel-rag-test-2` 当前有三本已完成索引的书（2026-10-02 核对）：

| 书名 | book_id | version | 总场景 | 入选并索引 | 归档 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 马之途 | 10 | 1 | 10 | 9 | 1 |
| 备用联系人 | 43 | 1 | 99 | 64 | 35 |
| 切勿操之过急 | 44 | 1 | 225 | 168 | 57 |

新导入的两本 EPUB 均完整转换、切分并完成逐场景评估；PostgreSQL 保留全部场景，Qdrant 只存入选场景，向量点数分别为 64 和 168。书籍 ID 和数量是这个本地隔离库的快照，在其他数据库中请以 `GET /api/v1/books` 和看板为准。原书文件、转换缓存及数据库与向量库数据不属于 Git 提交内容。

若 `.env` 的 `POSTGRES_DB=novel_rag` 尚未迁移，直接用普通启动命令打开看板会提示缺少项目表。以下脚本仅在服务进程中切换到隔离库，不改写 `.env`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_dashboard_preview.ps1
```

然后打开 `http://127.0.0.1:8007/dashboard`。预览脚本默认使用 8007 端口，可用 `-Port 8000` 等参数覆盖；正式库迁移和正式数据索引是独立操作，预览脚本不会执行它们。

## API

Base path 为 `/api/v1`，健康检查为 `/health`。

- `POST /api/v1/books`
- `GET /api/v1/books`
- `POST /api/v1/books/{book_id}/index`
- `GET /api/v1/books/{book_id}`
- `GET /api/v1/books/{book_id}/chapters/{chapter_index}`
- `GET /api/v1/books/{book_id}/jobs`
- `GET /api/v1/books/{book_id}/versions`
- `GET /api/v1/books/{book_id}/versions/{version}/overview`
- `GET /api/v1/books/{book_id}/versions/{version}/scenes`
- `GET /api/v1/books/{book_id}/versions/{version}/scenes/{scene_id}`
- `GET /api/v1/books/{book_id}/versions/{version}/projection`
- `GET /api/v1/books/{book_id}/versions/{version}/compare?other_version=1`
- `POST /api/v1/books/{book_id}/search`
- `POST /api/v1/books/{book_id}/search/stream`
- `POST /api/v1/search/multi`
- `POST /api/v1/search/multi/stream`
- `GET /api/v1/scenes/{scene_id}`
- `POST /api/v1/qdrant/collections/{collection}/points/search`
- `GET /api/v1/qdrant/collections/{collection}/points/{point_id}`

Qdrant 直查只接收调用方提供的原始向量；自然语言查询请使用单书或多书 search 接口。RRF 列表返回 `scene_id` 与限长预览，完整原文通过版本化 Scene 详情接口获取。

两个 `/stream` 接口使用与普通搜索相同的 JSON 请求体，返回 `application/x-ndjson`：`progress` 事件包含 `stage`，以及可能的 `book_id`、`route` 和完成数量；最终为 `result.data`，失败为 `error.message`。页面据此显示真实执行阶段，客户端需逐行读取响应流。

多书请求示例：

```json
{"query":"寻找人物关系紧张、对话带有试探意味的场景","book_ids":[10,43,44],"per_book_limit":20,"global_limit":50}
```

`versions` 可省略；服务端在请求开始时固定各书当前版本。响应保留 `books` 内各书四路及 RRF 结果、逐书失败信息、`aggregation.items` 和 `final.items`。各书结果写结构化日志，不记录原文。某书失败不会抹去其他书结果；全部失败返回 503。跨书候选池使用轮流取候选，不直接比较不同书的 RRF 分数。

默认 `RERANK_ENABLED=true`。直接使用 `OLLAMA_URL` 上的 `OLLAMA_RERANK_MODEL`，对聚合后的前 `RERANK_TOP_N` 条候选逐条计算 Qwen3-Reranker 的 yes/no token 概率；其余候选保持原顺序接在后面。`RERANK_CONCURRENCY` 控制同时提交给 Ollama 的评分请求。`RERANK_TIMEOUT_SECONDS` 默认为 180 秒，覆盖模型冷加载和排队时间；临时网络错误或 Ollama 429/502/503/504 会重试一次。Ollama 最多返回前 20 个候选 token 的概率；若 yes 或 no 超出范围，候选会得到保守边界分数并标记 `rerank_score_exact=false`，响应同时给出 `censored_count`。模型或服务失败会在 `rerank.status` 标明，并让 `final.items` 使用完整聚合顺序。该模型调用需要 Ollama `/api/generate` 支持 `logprobs` 和 `top_logprobs`，开启后会增加推理延迟；若当前机器不运行重排模型，可在 `.env` 设为 `RERANK_ENABLED=false`。

对 `ready` 书籍再次调用索引接口会构建新 version；旧 version 在新 version 通过 selected-point 一致性校验并激活前保持可用。

## 测试

```powershell
# 无外部服务依赖
uv run pytest tests/unit -q

# 测试库、真实 Qdrant 与合成 EPUB
uv run pytest tests/integration -q -m integration

# 手工真实链路验收：结果保留在 novel-rag-test-2
uv run python -B -m tests.real.run_ma_zhitu
```

测试数据约束：

- 允许全量 EPUB 转 TXT 作为本地缓存。
- 禁止把全量书或整章无界文本送入 LLM / Embedding。
- 默认自动化模型测试只使用约 4000 字合成微型小说，或从 TXT 截取的开头/中间/结尾章节。
- `tests.real.run_ma_zhitu` 手工脚本使用 `tests/马之途.txt`。隔离库中的另外两本 EPUB 已完成全书索引；每次模型调用仍只接收单个场景的有界采样，不把整本原文放入单次 Prompt。

## 目录

```text
app/
  api/                FastAPI 路由与依赖
  clients/            LLM / Ollama 适配器
  db/                 PostgreSQL Core 仓储、Qdrant 适配器、迁移入口
  prompts/            Reference Evaluation / Deep Annotation / 查询解析提示词
  services/           索引编排、查询解析、四路召回和 RRF
                      多书并发聚合与可选全局 Rerank
  utils/              EPUB 转换、文本处理、异常
data/
  books/
  converted/          EPUB/TXT 转换缓存
migrations/           001 初始化、002 tag_vocab 兼容、003 Reference Evaluation、004 查询缓存
dashboard/            实时只读索引观察与检索实验页面
tests/
  unit/
  integration/
  real/               手工真实链路验收脚本
  fixtures/           微型小说、截断书、合成 EPUB
```
