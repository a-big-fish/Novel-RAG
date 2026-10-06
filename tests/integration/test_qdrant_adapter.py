from __future__ import annotations

import uuid

import pytest

from app.config import get_settings
from app.db.qdrant import QdrantAdapter, scenes_collection_name

pytestmark = pytest.mark.integration


def _point(point_id: int) -> dict:
    return {
        "id": point_id,
        "vector": {
            "text-dense": [1.0, 0.0, 0.0, 0.0],
            "meta-dense": [0.0, 1.0, 0.0, 0.0],
            "summary-dense": [0.0, 0.0, 1.0, 0.0],
            "text-sparse": {"indices": [1], "values": [1.0]},
        },
        "payload": {
            "scene_id": point_id,
            "summary": "真实 Qdrant 组件测试",
        },
    }


def test_qdrant_adapter_round_trip_against_configured_service() -> None:
    settings = get_settings().model_copy(update={"embedding_dimension": 4})
    adapter = QdrantAdapter(settings=settings, dimension=4)
    book_id = uuid.uuid4().int % 900_000_000 + 1
    collection_name = scenes_collection_name(book_id, 1)

    try:
        adapter.create_scenes_collection(book_id, 1, recreate=True)
        assert adapter.upsert_scene_points(collection_name, [_point(101)]) == 1
        assert adapter.count(collection_name) == 1

        record = adapter.get_point(collection_name, 101, with_vectors=True)
        assert record is not None
        assert record.payload["summary"] == "真实 Qdrant 组件测试"
        assert "text-dense" in record.vector

        hits = adapter.search(
            collection_name,
            vector_name="text-dense",
            vector=[1.0, 0.0, 0.0, 0.0],
            limit=1,
        )
        assert [hit.id for hit in hits] == [101]
    finally:
        if adapter.client.collection_exists(collection_name):
            adapter.delete_collection(collection_name)
        adapter.close()
