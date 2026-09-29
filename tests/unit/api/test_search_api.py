from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_app_settings, get_ollama_client, get_qdrant_adapter,
    get_query_llm_client, get_repository,
)
from app.config import Settings
from app.main import app


class FakeRepository:
    def get_book(self, _):
        return {"current_version": 2}

    def list_versions(self, _):
        return [{"version": 2}]

    def get_query_cache(self, _):
        return {"raw_intent": "雨夜", "summary_query": "", "scene_type": [],
                "technique": [], "style_tags": [], "emotion_tags": [],
                "key_images": [], "fallback": False}

    def get_token_map(self, _):
        return {}

    def get_scene(self, _):
        return {"book_id": 3, "version": 2, "reference_status": "selected",
                "scene_index_in_book": 1, "chapter_start_index": 1,
                "chapter_end_index": 1, "text": "全文", "summary": "摘要",
                "style_summary": "风格", "usage_hint": "提示"}


class FakeOllama:
    def embed(self, _):
        return [[0.1, 0.2]]


class FakeQdrant:
    def search(self, *_args, **_kwargs):
        return []


class FakeLLM:
    model = "fake"


def test_search_api_pins_ready_version_and_returns_four_routes():
    app.dependency_overrides[get_repository] = FakeRepository
    app.dependency_overrides[get_ollama_client] = FakeOllama
    app.dependency_overrides[get_qdrant_adapter] = FakeQdrant
    app.dependency_overrides[get_query_llm_client] = FakeLLM
    app.dependency_overrides[get_app_settings] = lambda: Settings()
    try:
        client = TestClient(app)
        response = client.post("/api/v1/books/3/search", json={"query": "雨夜"})
        assert response.status_code == 200
        assert response.json()["version"] == 2
        assert response.json()["routes"]["text_sparse"]["status"] == "skipped"
        assert client.post("/api/v1/books/3/search", json={"query": "雨夜", "version": 3}).status_code == 404
    finally:
        app.dependency_overrides.clear()
