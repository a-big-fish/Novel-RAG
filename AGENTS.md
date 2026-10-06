# Repository Guidelines

## Project Structure & Module Organization

`Novel-RAG` is a Python 3.11 FastAPI service for the novel-writing RAG ingestion pipeline. Application code lives in `app/`: `api/` for routes, `clients/` for LLM/Ollama adapters, `db/` for PostgreSQL and Qdrant, `services/` for pipeline stages, `prompts/` for versioned prompts, and `utils/` for EPUB conversion and text handling.

Maintain all PostgreSQL and Qdrant create statements in `./sql/`, with PostgreSQL DDL at `sql/postgres/` (for example `001_init.sql`) and Qdrant collection definitions at `sql/qdrant/`. `migrations/` records applied migration history. `tests/unit/`, `tests/integration/`, and `tests/fixtures/` mirror the application domains; local generated books and converted files belong under `data/`.

## Build, Test, and Development Commands

```powershell
uv sync
uv run python -m app.db.migrate migrations/001_init.sql
uv run python -m app.db.migrate migrations/002_tag_vocab_aliases.sql
uv run python -m app.db.migrate migrations/003_reference_evaluation.sql
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run pytest tests/unit -q
uv run pytest tests/integration -q -m integration
```

`uv sync` installs dependencies, migration commands initialize the database, and `uvicorn` starts the API at `/api/v1` with health checking at `/health`. Unit tests run without external services; integration tests require PostgreSQL, Qdrant, and fixtures.

## Coding Style & Naming Conventions

Use PEP 8 with 4-space indentation and descriptive snake_case names for Python modules, functions, and variables. Prefer SQLAlchemy Core over introducing ORM models. Keep FastAPI dependencies, service orchestration, and database access in their existing layers. Add type hints and Pydantic settings in `app/config.py` for environment values.

## Testing Guidelines

Add pytest tests under the matching `tests/unit` or `tests/integration` path, naming files `test_*.py`. Mark external-service tests with `@pytest.mark.integration`. Cover API contracts, adapters, and pipeline orchestration changes. Never send entire books or unbounded chapters to LLM or embedding services in tests; use synthetic micro novels or bounded excerpts.

## Commit & Pull Request Guidelines

Commit each completed feature change, fix, or refactor as a focused Git commit. Use Conventional Commit format: `type(scope): summary`, for example `feat(indexer): add scene indexing` or `refactor(db): extract repository helpers`. Pull requests should state the behavioral change, affected pipeline stages, test results, and any required migration or `.env` change. Link related issues when applicable.

## Security & Configuration Tips

Copy `.env.example` to `.env` and provide local credentials; never commit `.env`, book contents, converted caches, or API keys. Run production migrations only after explicit confirmation; use `--test` for `novel_rag_test`.
