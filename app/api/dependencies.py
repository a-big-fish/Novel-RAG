from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, HTTPException

from app.clients.ollama_client import OllamaClient
from app.clients.llm_client import JsonLLMClient, build_llm_client
from app.config import Settings, get_settings, read_rerank_enabled
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.indexer import Indexer

IndexerFactory = Callable[[PostgresRepository], Indexer]


@lru_cache(maxsize=1)
def get_database() -> PostgresDatabase:
    return PostgresDatabase.from_settings()


@lru_cache(maxsize=1)
def get_repository() -> PostgresRepository:
    return PostgresRepository(get_database())


@lru_cache(maxsize=1)
def get_qdrant_adapter() -> QdrantAdapter:
    return QdrantAdapter(settings=get_settings())


@lru_cache(maxsize=1)
def get_ollama_client() -> OllamaClient:
    return OllamaClient(settings=get_settings())


@lru_cache(maxsize=1)
def get_query_llm_client() -> JsonLLMClient:
    return build_llm_client(get_settings())


def get_app_settings() -> Settings:
    return get_settings()


def get_multi_search_settings(
    settings: Settings = Depends(get_app_settings),
) -> Settings:
    try:
        enabled = read_rerank_enabled()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return settings.model_copy(update={"rerank_enabled": enabled})


def get_indexer_factory() -> IndexerFactory:
    def build_indexer(repository: PostgresRepository) -> Indexer:
        return Indexer.from_settings(repository, settings=get_settings())

    return build_indexer


def close_app_resources() -> None:
    """Best-effort release of process-level clients during shutdown."""

    if get_ollama_client.cache_info().currsize:
        get_ollama_client().close()
        get_ollama_client.cache_clear()
    if get_query_llm_client.cache_info().currsize:
        get_query_llm_client().close()
        get_query_llm_client.cache_clear()
    if get_qdrant_adapter.cache_info().currsize:
        get_qdrant_adapter().close()
        get_qdrant_adapter.cache_clear()
    if get_database.cache_info().currsize:
        get_database().dispose()
        get_database.cache_clear()
