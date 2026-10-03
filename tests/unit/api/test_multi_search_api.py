import json

from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_app_settings, get_multi_search_settings, get_ollama_client, get_qdrant_adapter,
    get_query_llm_client, get_repository,
)
from app import config
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
    app.dependency_overrides[get_multi_search_settings] = lambda: Settings(
        multi_search_concurrency=2, rerank_enabled=False,
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
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "http://ollama.test:11434"
            assert kwargs["model"] == "my-ollama-reranker"

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
    app.dependency_overrides[get_multi_search_settings] = lambda: Settings(
        rerank_enabled=True, ollama_url="http://ollama.test:11434",
        ollama_rerank_model="my-ollama-reranker",
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


def test_multi_search_api_reloads_rerank_switch_without_restart(tmp_path, monkeypatch):
    class FakeRerankClient:
        def __init__(self, **_kwargs):
            pass

        def rerank(self, _query, documents):
            return [{"index": index, "score": float(index)}
                    for index in range(len(documents))]

        def close(self):
            pass

    env_file = tmp_path / ".env"
    monkeypatch.setattr(config, "RERANK_ENV_FILE", env_file)
    monkeypatch.setattr(multi_search_route, "RerankClient", FakeRerankClient)
    app.dependency_overrides[get_repository] = CachedRepository
    app.dependency_overrides[get_ollama_client] = FakeOllama
    app.dependency_overrides[get_qdrant_adapter] = FakeQdrant
    app.dependency_overrides[get_query_llm_client] = FakeLLM
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        _env_file=None, rerank_enabled=None,
    )
    try:
        client = TestClient(app)
        env_file.write_text("RERANK_ENABLED=false\n", encoding="utf-8")
        disabled = client.post(
            "/api/v1/search/multi", json={"query": "雨夜", "book_ids": [1, 2]},
        )
        assert disabled.status_code == 200
        assert disabled.json()["rerank"]["status"] == "disabled"

        env_file.write_text("RERANK_ENABLED=true\n", encoding="utf-8")
        enabled = client.post(
            "/api/v1/search/multi", json={"query": "雨夜", "book_ids": [1, 2]},
        )
        assert enabled.status_code == 200
        assert enabled.json()["rerank"]["status"] == "ok"
    finally:
        app.dependency_overrides.clear()


def test_multi_search_stream_reports_aggregation_after_books():
    app.dependency_overrides[get_repository] = CachedRepository
    app.dependency_overrides[get_ollama_client] = FakeOllama
    app.dependency_overrides[get_qdrant_adapter] = FakeQdrant
    app.dependency_overrides[get_query_llm_client] = FakeLLM
    app.dependency_overrides[get_multi_search_settings] = lambda: Settings(
        multi_search_concurrency=2, rerank_enabled=False,
    )
    try:
        response = TestClient(app).post(
            "/api/v1/search/multi/stream",
            json={"query": "雨夜", "book_ids": [1, 2]},
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines()]
        stages = [event["stage"] for event in events if event["type"] == "progress"]
        assert stages[:3] == ["checking_books", "checking_books_complete", "parsing"]
        parsed = next(event for event in events if event.get("stage") == "parsing_complete")
        assert parsed["parsed_query"]["summary_query"] == "雨夜场景"
        assert parsed["latency_ms"] >= 0
        embedded = next(event for event in events if event.get("stage") == "embedding_complete")
        assert embedded["dimension"] == 2
        assert embedded["latency_ms"] >= 0
        assert all(event["latency_ms"] >= 0 for event in events
                   if event.get("stage") == "route_complete")
        assert stages.count("fusion_complete") == 2
        assert stages.count("book_complete") == 2
        assert stages.index("aggregating") > max(
            index for index, stage in enumerate(stages) if stage == "book_complete"
        )
        assert events[-1]["type"] == "result"
        assert len(events[-1]["data"]["aggregation"]["items"]) == 2
        assert stages.index("aggregating_complete") > stages.index("aggregating")
    finally:
        app.dependency_overrides.clear()
