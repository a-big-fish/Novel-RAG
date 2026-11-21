# Novel-RAG 文档索引

## 版本与运行

| 类型 | 当前口径 | 查阅位置 |
| --- | --- | --- |
| 软件版本 | 0.9.0 | [pyproject.toml](../pyproject.toml)、[服务默认配置](../app/config.py)、[环境变量模板](../.env.example) |
| 需求版本 | v0.9 | [当前 PRD](prd/novel-rag-prd-v0.9.md) |
| 历史需求版本 | v0.8 | [归档 PRD](prd/archive/novel-rag-prd-v0.8-2026-09-24.md) |
| 数据库迁移 | SQL 文件名前缀决定执行顺序 | [迁移目录](../migrations/)、[本机启动](../README.md#本机开发) |
| 书籍索引版本 | 书籍重新建库后递增。公开快照固定到清单记录的版本 | [快照清单](../sql/fixtures/public-books/manifest.json) |

运行与接入说明见[项目 README](../README.md)和[写作参考 Skill](../skills/novel-rag-writing/SKILL.md)。

## 数据与评测

- [三书公开测试库](../README.md#公开测试库)用于演示部署与恢复。作品数据的公开范围见[数据说明](../sql/fixtures/public-books/NOTICE.md)。
- [写作对照结果](../README.md#rag-写作对照结果)比较两种场景的八篇终稿，使用 DeepSeek 和 GPT 评分。
- 历史检索评测使用本地观察数据。复现四书结果需要报告列出的版本和索引，公开测试库可用于三书检索演示。

### 检索报告

| 报告 | 范围 |
| --- | --- |
| [四书检索基线](../reports/retrieval-baseline-multi-book-2026-11-13.md) | 28 条单书探针、4 条聚合探针与重排耗时，2026 年 11 月 16 日更新 |
| [风格与技法路由诊断](../reports/meta-route-diagnosis-2026-11-13.md) | 查询构造与向量同质化问题 |
| [Meta 向量重建](../reports/meta-vector-rebuild-2026-11-16.md) | 重建前后指标、命令和复现条件 |
| [单书检索基线](../reports/archive/retrieval-baseline-ma-zhitu-2026-09-29.md) | 归档的《马之途》10 条探针 |
