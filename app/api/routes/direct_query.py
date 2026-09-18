from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_qdrant_adapter, get_repository
from app.db.postgres import PostgresRepository
from app.db.qdrant import QdrantAdapter

router = APIRouter(tags=["direct-query"])


class QdrantSearchRequest(BaseModel):
    vector_name: str
    vector: list[float] | dict[str, list[int] | list[float]]
    limit: int = Field(default=10, ge=1, le=100)
    with_payload: bool = True
    filter: dict[str, Any] | None = None


@router.get("/scenes/{scene_id}")
def get_scene(
    scene_id: int,
    repository: PostgresRepository = Depends(get_repository),
) -> dict[str, Any]:
    scene = repository.get_scene(scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    return dict(scene)


@router.post(
    "/qdrant/collections/{collection_name}/points/search",
)
def qdrant_search(
    collection_name: str,
    payload: QdrantSearchRequest,
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
) -> list[dict[str, Any]]:
    try:
        points = qdrant.search(
            collection_name,
            vector_name=payload.vector_name,
            vector=payload.vector,
            limit=payload.limit,
            with_payload=payload.with_payload,
            query_filter=payload.filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [
        {
            "id": point.id,
            "score": point.score,
            "payload": point.payload,
            "vector": point.vector,
        }
        for point in points
    ]


@router.get(
    "/qdrant/collections/{collection_name}/points/{point_id}",
)
def qdrant_get_point(
    collection_name: str,
    point_id: int,
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
) -> dict[str, Any]:
    try:
        record = qdrant.get_point(
            collection_name,
            point_id,
            with_vectors=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="point not found")
    return {
        "id": record.id,
        "payload": record.payload,
        "vector": record.vector,
    }
