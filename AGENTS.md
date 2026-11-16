# Repository Guidelines

## Project Structure & Module Organization

`Novel-RAG` is a Python 3.11 FastAPI service for the novel-writing RAG ingestion and retrieval pipeline. Application code lives in `app/`: `api/` for routes, `clients/` for LLM/Ollama adapters, `db/` for PostgreSQL and Qdrant, `services/` for pipeline stages, `prompts/` for versioned prompts, and `utils/` for EPUB conversion and text handling.

Maintain all PostgreSQL create statements in `./sql/` (for example `sql/03_scenes.sql`) and mirror applied changes in `migrations/`. Qdrant collection layout is defined by `app/db/qdrant.py` and the public fixture under `sql/fixtures/public-books/`. `tests/unit/`, `tests/integration/`, `tests/fixtures/`, and `tests/real/` mirror the application domains; local generated books and converted files belong under `data/`. Current PRDs live in `docs/prd/` (archived versions under `docs/prd/archive/`), retrieval baselines live in `reports/`, and installable agent skills live in `skills/`.

The stdio MCP surface lives in `app/mcp_server.py` and must stay aligned with the HTTP `/assemble` flow: both call `WritingReferenceService` and share `app/mcp_guide.py` / `skills/novel-rag-writing/SKILL.md`.

## Build, Test, and Development Commands

```powershell
uv sync
uv run python -m app.db.migrate migrations/001_init.sql
uv run python -m app.db.migrate migrations/002_tag_vocab_aliases.sql
uv run python -m app.db.migrate migrations/003_reference_evaluation.sql
uv run python -m app.db.migrate migrations/004_query_parsing_cache.sql
uv run python -m app.db.migrate migrations/005_scene_split_cache.sql
uv run python -m app.db.migrate migrations/006_scene_quality_screen.sql
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run python -m app.mcp_server
uv run pytest tests/unit -q
uv run pytest tests/integration -q -m integration
uv run python -B -m tests.real.run_retrieval_baseline
```

`uv sync` installs dependencies, migration commands initialize the database, and `uvicorn` starts the API at `/api/v1` with health checking at `/health`. The local default database is the observation database `novel-rag-test-2`; it contains the multi-book demo data (including the locally indexed 《百年孤独》) and is also used by integration tests. CI should use a separate database. Unit tests run without external services; integration tests require PostgreSQL, Qdrant, and fixtures.

## Coding Style & Naming Conventions

Use PEP 8 with 4-space indentation and descriptive snake_case names for Python modules, functions, and variables. Prefer SQLAlchemy Core over introducing ORM models. Keep FastAPI dependencies, service orchestration, and database access in their existing layers. Add type hints and Pydantic settings in `app/config.py` for environment values.

## Testing Guidelines

Add pytest tests under the matching `tests/unit` or `tests/integration` path, naming files `test_*.py`. Mark external-service tests with `@pytest.mark.integration`. Cover API contracts, adapters, and pipeline orchestration changes. Never send entire books or unbounded chapters to LLM or embedding services in tests; use synthetic micro novels or bounded excerpts. Retrieval baseline scripts under `tests/real/` may query the observation database, but must remain read-only with respect to books, scenes, token_map, and Qdrant points.

## Commit & Pull Request Guidelines

Commit each completed feature change, fix, or refactor as a focused Git commit. Use Conventional Commit format: `type(scope): summary`, for example `feat(indexer): add scene indexing` or `refactor(db): extract repository helpers`. Pull requests should state the behavioral change, affected pipeline stages, test results, and any required migration or `.env` change. Link related issues when applicable.

## Security & Configuration Tips

Copy `.env.example` to `.env` and provide local credentials; never commit `.env`, book contents, converted caches, or API keys. Run production migrations only after explicit confirmation; `--test` targets `novel-rag-test-2`, which is also the local observation database. Keep the release version aligned across `pyproject.toml`, `app/config.py`, and `.env.example` (currently `0.9.0`).
