from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.dependencies import (
    get_multi_search_settings,
    get_ollama_client,
    get_qdrant_adapter,
    get_query_llm_client,
    get_repository,
)
from app.clients.llm_client import JsonLLMClient
from app.clients.ollama_client import OllamaClient
from app.clients.rerank_client import RerankClient
from app.config import Settings
from app.db.postgres import PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.multi_search import MultiBookSearcher
from app.services.query_parser import QueryParser
from app.services.retriever import Retriever
from app.services.writing_reference import WritingReferenceError, WritingReferenceService

router = APIRouter(prefix="/assemble", tags=["writing-reference"])


class AssembleRequest(BaseModel):
    writing_goal: str = Field(min_length=1)
    outline_context: str = ""
    constraints: dict[str, Any] | None = None
    book_ids: list[int] | None = None
    versions: dict[int, int] = Field(default_factory=dict)
    output: Literal["prompt", "fragments", "both"] = "prompt"
    budget_chars: int | None = Field(default=None, ge=500, le=50000)
    max_scenes: int | None = Field(default=None, ge=1, le=50)
    per_scene_chars: int | None = Field(default=None, ge=100, le=10000)
    book_quota: int | None = Field(default=None, ge=0, le=50)

    @model_validator(mode="after")
    def validate_scope(self) -> AssembleRequest:
        if self.book_ids:
            if any(book_id <= 0 for book_id in self.book_ids):
                raise ValueError("book_ids must be positive")
            if len(self.book_ids) != len(set(self.book_ids)):
                raise ValueError("book_ids must be unique")
        if any(book_id <= 0 or version <= 0
               for book_id, version in self.versions.items()):
            raise ValueError("versions must be positive")
        return self


@router.post("")
def assemble(
    payload: AssembleRequest,
    repository: PostgresRepository = Depends(get_repository),
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
    ollama: OllamaClient = Depends(get_ollama_client),
    llm: JsonLLMClient = Depends(get_query_llm_client),
    settings: Settings = Depends(get_multi_search_settings),
) -> dict:
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
    service = WritingReferenceService(repository, searcher, settings)
    try:
        return service.search(
            writing_goal=payload.writing_goal,
            outline_context=payload.outline_context,
            constraints=payload.constraints,
            book_ids=payload.book_ids,
            versions=payload.versions,
            output=payload.output,
            budget_chars=payload.budget_chars,
            max_scenes=payload.max_scenes,
            per_scene_chars=payload.per_scene_chars,
            book_quota=payload.book_quota,
        )
    except WritingReferenceError as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        if reranker is not None:
            reranker.close()
