from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_app_settings, get_ollama_client, get_qdrant_adapter,
    get_query_llm_client, get_repository,
)
from app.config import Settings
from app.main import app
from app.api.routes import multi_search as multi_search_route
from tests.unit.services.test_multi_search import (
    FakeOllama, FakeQdrant, FakeRepository,
)


class FakeLLM:
    model = "fake"


class CachedRepository(FakeRepository):
    def get_query_cache(self, _key):
        return {
            "raw_intent": "雨夜", "summary_query": "雨夜场景",
            "scene_type": [], "technique": [], "style_tags": [],
            "emotion_tags": [], "key_images": [], "fallback": False,
        }


def test_multi_search_api_returns_book_scoped_candidates():
    app.dependency_overrides[get_repository] = CachedRepository
    app.dependency_overrides[get_ollama_client] = FakeOllama
    app.dependency_overrides[get_qdrant_adapter] = FakeQdrant
    app.dependency_overrides[get_query_llm_client] = FakeLLM
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        multi_search_concurrency=2,
    )
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/search/multi",
            json={"query": "雨夜", "book_ids": [1, 2]},
        )
        assert response.status_code == 200
        result = response.json()
        assert list(result["books"]) == ["1", "2"]
        assert [(item["book_id"], item["scene_id"])
                for item in result["aggregation"]["items"]] == [
                    (1, 101), (2, 201),
                ]
        assert client.post(
            "/api/v1/search/multi",
            json={"query": "雨夜", "book_ids": [1, 1]},
        ).status_code == 422
        assert client.post(
            "/api/v1/search/multi",
            json={"query": "雨夜", "book_ids": [1], "versions": {"2": 2}},
        ).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_multi_search_api_uses_configured_reranker(monkeypatch):
    class FakeRerankClient:
        def __init__(self, **_kwargs):
            pass

        def rerank(self, _query, documents):
            return [
                {"index": index, "score": float(index)}
                for index in range(len(documents))
            ]

        def close(self):
            pass

    monkeypatch.setattr(multi_search_route, "RerankClient", FakeRerankClient)
    app.dependency_overrides[get_repository] = CachedRepository
    app.dependency_overrides[get_ollama_client] = FakeOllama
    app.dependency_overrides[get_qdrant_adapter] = FakeQdrant
    app.dependency_overrides[get_query_llm_client] = FakeLLM
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        rerank_enabled=True, rerank_url="http://reranker.test/v1/rerank",
    )
    try:
        response = TestClient(app).post(
            "/api/v1/search/multi",
            json={"query": "雨夜", "book_ids": [1, 2]},
        )
        assert response.status_code == 200
        assert response.json()["rerank"]["status"] == "ok"
        assert [item["scene_id"] for item in response.json()["final"]["items"]] == [
            201, 101,
        ]
    finally:
        app.dependency_overrides.clear()
