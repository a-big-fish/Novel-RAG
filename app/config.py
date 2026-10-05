from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RERANK_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def read_rerank_enabled() -> bool:
    """Read the rerank switch from .env for each search request."""
    value = dotenv_values(RERANK_ENV_FILE, encoding="utf-8").get("RERANK_ENABLED")
    if value is None:
        raise ValueError(f"RERANK_ENABLED is missing from {RERANK_ENV_FILE}")
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"RERANK_ENABLED must be true or false in {RERANK_ENV_FILE}")


class Settings(BaseSettings):
    """Application configuration loaded from ``.env``.

    Secrets are wrapped by ``SecretStr`` so accidental logging produces
    ``**********`` instead of the configured value.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_version: str = "0.8.0"
    api_prefix: str = "/api/v1"

    book_source_dir: Path = Path("E:/novels/tool")
    allowed_source_roots: str = "E:/novels/tool"
    book_upload_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    data_books_dir: Path = Path("data/books")
    data_converted_dir: Path = Path("data/converted")
    epub_converter_version: str = "epub-v1"

    postgres_host: str = "172.16.43.125"
    postgres_port: int = 5432
    postgres_db: str = "novel_rag"
    postgres_user: str = "admin"
    postgres_password: SecretStr = SecretStr("")
    postgres_test_db: str = "novel-rag-test-2"

    qdrant_url: str = "http://172.16.43.125:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_timeout_seconds: float = 30.0
    embedding_dimension: int = 1024

    ollama_url: str = "http://172.16.43.125:11434"
    ollama_embed_model: str = "bge-m3"
    ollama_rerank_model: str = "awenleven/Qwen3-Reranker-4B:Q4_K_M"
    ollama_timeout_seconds: float = 120.0

    llm_adapter: str = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = 180.0
    llm_max_retries: int = 2
    llm_retry_backoff_seconds: float = 1.0

    codex_exec_path: str = "codex"
    codex_exec_timeout_seconds: int = 1800

    long_text_head_chars: int = 1200
    long_text_middle_chars: int = 1200
    long_text_tail_chars: int = 1200
    max_llm_input_chars: int = 3600
    max_embed_input_chars: int = 3600
    llm_concurrency: int = 10
    embed_concurrency: int = 3

    tag_vocab_version: str = "v1"
    reference_rule_version: str = "v1"
    prompt_version: str = "v1.0"
    query_prompt_version: str = "v1"
    query_max_chars: int = 2000
    query_route_top_n: int = 20
    query_rrf_top_n: int = 20
    rrf_k: int = 60
    multi_search_max_books: int = Field(default=20, ge=1, le=100)
    multi_search_concurrency: int = Field(default=4, ge=1, le=32)
    multi_search_attempts: int = Field(default=2, ge=1, le=3)
    multi_search_per_book_limit: int = Field(default=20, ge=1, le=100)
    multi_search_global_limit: int = Field(default=50, ge=1, le=500)
    rerank_enabled: bool | None = None
    rerank_top_n: int = Field(default=10, ge=1, le=100)
    rerank_concurrency: int = Field(default=1, ge=1, le=8)
    rerank_timeout_seconds: float = Field(default=180.0, gt=0)
    rerank_max_document_chars: int = Field(default=3600, ge=500, le=20000)
    log_level: str = "INFO"

    @field_validator(
        "book_source_dir",
        "data_books_dir",
        "data_converted_dir",
        mode="after",
    )
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        roots = [
            Path(item.strip()).expanduser().resolve()
            for item in self.allowed_source_roots.split(",")
            if item.strip()
        ]
        return tuple(roots)

    def postgres_url(self, *, test: bool = False) -> str:
        database = self.postgres_test_db if test else self.postgres_db
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
