from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_qdrant_adapter, get_repository
from app.db.postgres import PostgresRepository
from app.db.qdrant import DENSE_VECTOR_NAMES, QdrantAdapter, scenes_collection_name

router = APIRouter(prefix="/books", tags=["observation"])


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _version_or_404(repository: PostgresRepository, book_id: int, version: int):
    book = repository.get_book(book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    versions = repository.list_versions(book_id)
    if not any(int(item["version"]) == version for item in versions):
        raise HTTPException(404, "version not found")
    return book, versions


@router.get("/{book_id}/versions")
def list_versions(
    book_id: int, repository: PostgresRepository = Depends(get_repository),
) -> dict:
    book = repository.get_book(book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    return {
        "book_id": book_id, "title": book["title"],
        "current_version": int(book["current_version"] or 0),
        "versions": repository.list_versions(book_id),
        "generated_at": _timestamp(),
    }


@router.get("/{book_id}/versions/{version}/overview")
def overview(
    book_id: int, version: int,
    repository: PostgresRepository = Depends(get_repository),
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
) -> dict:
    book, versions = _version_or_404(repository, book_id, version)
    info = next(item for item in versions if int(item["version"]) == version)
    scenes = repository.list_version_scene_metrics(book_id, version)
    tags = {
        name: Counter(tag for scene in scenes for tag in (scene[name] or []))
        for name in ("scene_type", "technique", "style_tags", "emotion_tags", "key_images")
    }
    namespaces = {
        "scene_type": "scene_type", "technique": "technique",
        "style_tags": "style", "emotion_tags": "emotion", "key_images": "image",
    }
    display_names = {}
    for row in repository.get_tag_vocab():
        display_names.setdefault(
            (row["namespace"], row["canonical_key"]), row["display_name"]
        )
    collection = scenes_collection_name(book_id, version)
    try:
        qdrant_count = qdrant.count(collection)
    except Exception:
        qdrant_count = None
    indexed = sum(
        scene["reference_status"] == "selected"
        and scene["annotate_status"] == "annotated"
        and scene["index_status"] == "indexed"
        for scene in scenes
    )
    return {
        "book_id": book_id, "version": version,
        "title": book["title"], "author": book["author"],
        "current_version": int(book["current_version"] or 0),
        "book_status": book["status"],
        "counts": dict(info), "indexed_selected": indexed,
        "qdrant_count": qdrant_count,
        "counts_match": qdrant_count == indexed if qdrant_count is not None else None,
        "lengths": [int(scene["char_count"]) for scene in scenes],
        "tags": {
            name: [
                {"key": key, "label": display_names.get((namespaces[name], key), key),
                 "count": count}
                for key, count in counter.most_common(20)
            ] for name, counter in tags.items()
        },
        "recent_book_jobs": [
            {"stage": row["stage"], "status": row["status"],
             "done_items": row["done_items"], "total_items": row["total_items"]}
            for row in repository.list_jobs(book_id)[-10:]
        ],
        "generated_at": _timestamp(),
    }


@router.get("/{book_id}/versions/{version}/compare")
def compare_versions(
    book_id: int, version: int, other_version: int = Query(ge=1),
    repository: PostgresRepository = Depends(get_repository),
) -> dict:
    _version_or_404(repository, book_id, version)
    _version_or_404(repository, book_id, other_version)
    current = repository.list_scene_fingerprints(book_id, version)
    other = repository.list_scene_fingerprints(book_id, other_version)
    current_by_text = {scene["text_hash"]: scene for scene in current}
    other_by_text = {scene["text_hash"]: scene for scene in other}
    shared = current_by_text.keys() & other_by_text.keys()
    return {
        "book_id": book_id, "version": version,
        "other_version": other_version,
        "unchanged_text": len(shared),
        "new_text": len(current_by_text.keys() - other_by_text.keys()),
        "removed_text": len(other_by_text.keys() - current_by_text.keys()),
        "reference_status_changed": sum(
            current_by_text[item]["reference_status"] != other_by_text[item]["reference_status"]
            for item in shared
        ),
        "matching_rule": "exact_scene_text_md5",
        "generated_at": _timestamp(),
    }


@router.get("/{book_id}/versions/{version}/scenes")
def list_scenes(
    book_id: int, version: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    reference_status: str | None = Query(default=None),
    repository: PostgresRepository = Depends(get_repository),
) -> dict:
    _version_or_404(repository, book_id, version)
    if reference_status not in {None, "selected", "archived", "evaluation_failed", "unevaluated"}:
        raise HTTPException(422, "invalid reference_status")
    total, items = repository.list_version_scenes_page(
        book_id, version, limit=limit, offset=offset,
        reference_status=reference_status,
    )
    return {
        "book_id": book_id, "version": version,
        "total": total, "limit": limit, "offset": offset,
        "items": items, "generated_at": _timestamp(),
    }


def project_vectors(vectors: list[list[float]]) -> list[list[float]]:
    if not vectors:
        return []
    if len(vectors) == 1:
        return [[0.0, 0.0]]
    matrix = np.asarray(vectors, dtype=float)
    centered = matrix - matrix.mean(axis=0)
    _, _, right = np.linalg.svd(centered, full_matrices=False)
    projected = centered @ right[:2].T
    if projected.shape[1] == 1:
        projected = np.column_stack((projected[:, 0], np.zeros(len(vectors))))
    return projected.tolist()


@router.get("/{book_id}/versions/{version}/projection")
def projection(
    book_id: int, version: int,
    vector_name: str = Query(default="text-dense"),
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
    repository: PostgresRepository = Depends(get_repository),
) -> dict:
    _version_or_404(repository, book_id, version)
    if vector_name not in DENSE_VECTOR_NAMES:
        raise HTTPException(422, "unsupported vector_name")
    collection = scenes_collection_name(book_id, version)
    try:
        records, next_offset = qdrant.client.scroll(
            collection_name=collection, limit=500,
            with_payload=True, with_vectors=[vector_name],
        )
    except Exception as exc:
        raise HTTPException(503, "projection source unavailable") from exc
    valid = [record for record in records if record.vector and vector_name in record.vector]
    coordinates = project_vectors([record.vector[vector_name] for record in valid])
    return {
        "book_id": book_id, "version": version, "vector_name": vector_name,
        "sampled": next_offset is not None,
        "points": [
            {"scene_id": int(record.id), "x": float(x), "y": float(y)}
            for record, (x, y) in zip(valid, coordinates)
        ],
        "generated_at": _timestamp(),
    }


@router.get("/{book_id}/versions/{version}/scenes/{scene_id}/similar")
def similar_scenes(
    book_id: int, version: int, scene_id: int,
    repository: PostgresRepository = Depends(get_repository),
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
) -> dict:
    _version_or_404(repository, book_id, version)
    collection = scenes_collection_name(book_id, version)
    record = qdrant.get_point(collection, scene_id, with_vectors=True)
    if record is None or not record.vector:
        raise HTTPException(404, "indexed scene not found")
    hits = qdrant.search(
        collection, vector_name="text-dense",
        vector=record.vector["text-dense"], limit=9,
    )
    return {
        "book_id": book_id, "version": version, "scene_id": scene_id,
        "items": [
            {"scene_id": int(hit.id), "score": float(hit.score)}
            for hit in hits if int(hit.id) != scene_id
        ][:8],
        "generated_at": _timestamp(),
    }
