from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import get_qdrant_adapter, get_repository
from app.main import app


class FakeRepository:
    def get_scene(self, scene_id: int) -> dict[str, Any] | None:
        if scene_id != 101:
            return None
        return {
            "id": 101,
            "book_id": 7,
            "scene_index_in_book": 1,
            "summary": "雨夜交锋",
        }


class FakeQdrant:
    def search(self, collection_name: str, **kwargs: Any) -> list[Any]:
        if kwargs["vector_name"] == "unsupported":
            raise ValueError("unsupported vector name: unsupported")
        return [
            SimpleNamespace(
                id=101,
                score=0.93,
                payload={"scene_id": 101},
                vector=None,
            )
        ]

    def get_point(
        self,
        collection_name: str,
        point_id: int,
        *,
        with_vectors: bool,
    ) -> Any:
        if point_id != 101:
            return None
        return SimpleNamespace(
            id=101,
            payload={"scene_id": 101},
            vector={"text-dense": [0.1, 0.2]},
        )


def test_get_scene_and_qdrant_direct_query() -> None:
    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    app.dependency_overrides[get_qdrant_adapter] = lambda: FakeQdrant()
    try:
        client = TestClient(app)
        scene = client.get("/api/v1/scenes/101")
        missing_scene = client.get("/api/v1/scenes/999")
        search = client.post(
            "/api/v1/qdrant/collections/scenes_book_7_v1/points/search",
            json={
                "vector_name": "text-dense",
                "vector": [0.1, 0.2],
                "limit": 5,
            },
        )
        point = client.get(
            "/api/v1/qdrant/collections/scenes_book_7_v1/points/101"
        )
        missing_point = client.get(
            "/api/v1/qdrant/collections/scenes_book_7_v1/points/999"
        )
    finally:
        app.dependency_overrides.clear()

    assert scene.status_code == 200
    assert scene.json()["summary"] == "雨夜交锋"
    assert missing_scene.status_code == 404
    assert search.status_code == 200
    assert search.json() == [
        {
            "id": 101,
            "score": 0.93,
            "payload": {"scene_id": 101},
            "vector": None,
        }
    ]
    assert point.status_code == 200
    assert point.json()["vector"] == {"text-dense": [0.1, 0.2]}
    assert missing_point.status_code == 404


def test_qdrant_rejects_unsupported_vector_name() -> None:
    app.dependency_overrides[get_qdrant_adapter] = lambda: FakeQdrant()
    try:
        response = TestClient(app).post(
            "/api/v1/qdrant/collections/scenes_book_7_v1/points/search",
            json={
                "vector_name": "unsupported",
                "vector": [0.1, 0.2],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
