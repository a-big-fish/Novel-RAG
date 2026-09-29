from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import (
    get_app_settings, get_ollama_client, get_qdrant_adapter,
    get_query_llm_client, get_repository,
)
from app.clients.llm_client import JsonLLMClient
from app.clients.ollama_client import OllamaClient
from app.config import Settings
from app.db.postgres import PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.query_parser import QueryParser
from app.services.retriever import Retriever, RetrievalError

router = APIRouter(prefix="/books", tags=["retrieval"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)
    route_top_n: int | None = Field(default=None, ge=1, le=100)
    rrf_top_n: int | None = Field(default=None, ge=1, le=100)


@router.post("/{book_id}/search")
def search_book(
    book_id: int, payload: SearchRequest,
    repository: PostgresRepository = Depends(get_repository),
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
    ollama: OllamaClient = Depends(get_ollama_client),
    llm: JsonLLMClient = Depends(get_query_llm_client),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    book = repository.get_book(book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    version = payload.version or int(book["current_version"] or 0)
    if version <= 0:
        raise HTTPException(409, "book has no ready index version")
    if (version > int(book["current_version"] or 0)
            or not any(int(row["version"]) == version for row in repository.list_versions(book_id))):
        raise HTTPException(404, "ready index version not found")
    if not payload.query.strip() or len(payload.query.strip()) > settings.query_max_chars:
        raise HTTPException(422, "query length is outside configured bounds")
    parser = QueryParser(repository, llm, settings)
    retriever = Retriever(repository, qdrant, ollama, parser, settings)
    try:
        return retriever.search(
            book_id=book_id, version=version, query=payload.query,
            route_top_n=payload.route_top_n or settings.query_route_top_n,
            rrf_top_n=payload.rrf_top_n or settings.query_rrf_top_n,
        )
    except RetrievalError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/{book_id}/versions/{version}/scenes/{scene_id}")
def get_version_scene(
    book_id: int, version: int, scene_id: int,
    repository: PostgresRepository = Depends(get_repository),
) -> dict:
    scene = repository.get_scene(scene_id)
    if (scene is None or int(scene["book_id"]) != book_id
            or int(scene["version"]) != version):
        raise HTTPException(404, "scene not found in requested version")
    return dict(scene)
