# novel-rag

面向小说写作范本检索的独立服务。当前只实现上半链路：

`源文件登记 -> EPUB/TXT 准备 -> 章节/场景拆分 -> Reference Evaluation -> selected Scene 深度标注 -> 向量化 -> PostgreSQL Archive + Qdrant Reference Index -> 组件级直查 API`

PostgreSQL 保留全部 Final Scene；Qdrant 只保留具有写作参考价值的 `selected` Scene。`archived` Scene 是正常小说结构资产，不是失败或废弃数据。

下半链路（自然语言查询解析、多路召回合、RRF、rerank、上下文组装）不在本轮开发范围内。

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
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

测试库迁移时增加 `--test`，并确保 `POSTGRES_TEST_DB=novel-rag-test-2`。正式库 `novel_rag` 由用户确认后再迁移；本轮集成测试使用 `novel-rag-test-2`，原 `novel-rag-test` / `novel_rag_test` 保留不动。

## API

Base path 为 `/api/v1`，健康检查为 `/health`。

- `POST /api/v1/books`
- `POST /api/v1/books/{book_id}/index`
- `GET /api/v1/books/{book_id}`
- `GET /api/v1/books/{book_id}/chapters/{chapter_index}`
- `GET /api/v1/books/{book_id}/jobs`
- `GET /api/v1/scenes/{scene_id}`
- `POST /api/v1/qdrant/collections/{collection}/points/search`
- `GET /api/v1/qdrant/collections/{collection}/points/{point_id}`

Qdrant 直查只接收调用方提供的原始向量，不包含查询解析、embedding、RRF、rerank 或上下文组装。

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
- 手工验收固定使用 `tests/马之途.txt`；逐 Scene 有界调用，不把整本原文放入单次 Prompt。

## 目录

```text
app/
  api/                FastAPI 路由与依赖
  clients/            LLM / Ollama 适配器
  db/                 PostgreSQL Core 仓储、Qdrant 适配器、迁移入口
  prompts/            Reference Evaluation / Deep Annotation 版本化提示词
  services/           拆分、范本准入、深度标注、embedding、稀疏向量、索引编排
  utils/              EPUB 转换、文本处理、异常
data/
  books/
  converted/          EPUB/TXT 转换缓存
migrations/           001 初始化、002 tag_vocab 兼容、003 Reference Evaluation
tests/
  unit/
  integration/
  real/               手工真实链路验收脚本
  fixtures/           微型小说、截断书、合成 EPUB
```
