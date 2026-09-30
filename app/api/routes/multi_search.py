from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.dependencies import (
    get_app_settings, get_ollama_client, get_qdrant_adapter,
    get_query_llm_client, get_repository,
)
from app.clients.llm_client import JsonLLMClient
from app.clients.ollama_client import OllamaClient
from app.clients.rerank_client import RerankClient
from app.config import Settings
from app.db.postgres import PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.multi_search import MultiBookSearcher, MultiBookSearchError
from app.services.query_parser import QueryParser
from app.services.retriever import Retriever

router = APIRouter(prefix="/search", tags=["retrieval"])


class MultiSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    book_ids: list[int] = Field(min_length=1)
    versions: dict[int, int] = Field(default_factory=dict)
    route_top_n: int | None = Field(default=None, ge=1, le=100)
    per_book_limit: int | None = Field(default=None, ge=1, le=100)
    global_limit: int | None = Field(default=None, ge=1, le=500)

    @model_validator(mode="after")
    def validate_book_scope(self) -> MultiSearchRequest:
        if any(book_id <= 0 for book_id in self.book_ids):
            raise ValueError("book_ids must be positive")
        if len(self.book_ids) != len(set(self.book_ids)):
            raise ValueError("book_ids must be unique")
        if any(book_id not in self.book_ids or version <= 0
               for book_id, version in self.versions.items()):
            raise ValueError("versions must match requested book_ids and be positive")
        return self


@router.post("/multi")
def search_multiple_books(
    payload: MultiSearchRequest,
    repository: PostgresRepository = Depends(get_repository),
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
    ollama: OllamaClient = Depends(get_ollama_client),
    llm: JsonLLMClient = Depends(get_query_llm_client),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    if (not payload.query.strip()
            or len(payload.query.strip()) > settings.query_max_chars):
        raise HTTPException(422, "query length is outside configured bounds")
    if len(payload.book_ids) > settings.multi_search_max_books:
        raise HTTPException(422, "too many books in one search")

    parser = QueryParser(repository, llm, settings)
    retriever = Retriever(repository, qdrant, ollama, parser, settings)
    reranker = (
        RerankClient(
            base_url=settings.ollama_url,
            model=settings.ollama_rerank_model,
            timeout_seconds=settings.rerank_timeout_seconds,
            concurrency=settings.rerank_concurrency,
        )
        if settings.rerank_enabled else None
    )
    searcher = MultiBookSearcher(repository, retriever, settings, reranker)
    try:
        return searcher.search(
            query=payload.query, book_ids=payload.book_ids,
            versions=payload.versions,
            route_top_n=payload.route_top_n or settings.query_route_top_n,
            per_book_limit=(payload.per_book_limit
                            or settings.multi_search_per_book_limit),
            global_limit=payload.global_limit or settings.multi_search_global_limit,
        )
    except MultiBookSearchError as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        if reranker is not None:
            reranker.close()
