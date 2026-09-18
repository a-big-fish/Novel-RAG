from __future__ import annotations

from qdrant_client import QdrantClient

from app.db.qdrant import (
    DENSE_VECTOR_NAMES,
    QdrantAdapter,
    scenes_collection_name,
    validate_scenes_collection,
)


def _point(point_id: int) -> dict:
    return {
        "id": point_id,
        "vector": {
            "text-dense": [1.0, 0.0, 0.0],
            "meta-dense": [0.0, 1.0, 0.0],
            "summary-dense": [0.0, 0.0, 1.0],
            "text-sparse": {"indices": [1, 2], "values": [1.0, 0.5]},
        },
        "payload": {"scene_id": point_id, "summary": "summary"},
    }


def test_collection_name_and_guard() -> None:
    assert scenes_collection_name(2, 3) == "scenes_book_2_v3"
    assert validate_scenes_collection("scenes_book_2_v3") == "scenes_book_2_v3"


def test_create_upsert_count_and_get() -> None:
    client = QdrantClient(location=":memory:")
    adapter = QdrantAdapter(client, dimension=3)
    name = adapter.create_scenes_collection(10, 1)
    adapter.create_scenes_collection(10, 1)

    assert adapter.upsert_scene_points(name, [_point(101), _point(102)]) == 2
    assert adapter.count(name) == 2
    record = adapter.get_point(name, 101)
    assert record is not None
    assert record.payload["summary"] == "summary"


def test_direct_dense_and_sparse_search() -> None:
    client = QdrantClient(location=":memory:")
    adapter = QdrantAdapter(client, dimension=3)
    name = adapter.create_scenes_collection(10, 1)
    adapter.upsert_scene_points(name, [_point(101), _point(102)])

    dense = adapter.search(name, vector_name="text-dense", vector=[1, 0, 0])
    sparse = adapter.search(
        name,
        vector_name="text-sparse",
        vector={"indices": [1], "values": [1.0]},
    )

    assert dense[0].id in {101, 102}
    assert all(vector in DENSE_VECTOR_NAMES for vector in {"text-dense"})
    assert sparse
