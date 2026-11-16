from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_multi_search_settings,
    get_ollama_client,
    get_qdrant_adapter,
    get_query_llm_client,
    get_repository,
)
from app.api.routes import assemble
from app.config import Settings
from app.main import app


def test_assemble_endpoint_returns_service_payload(monkeypatch):
    captured = {}

    def fake_search(self, **kwargs):
        captured.update(kwargs)
        return {
            "query_plan": {"query": kwargs["writing_goal"]},
            "book_ids": [10],
            "search": {},
            "prompt": {"text": "拼装结果", "char_count": 4, "budget": {}},
            "fragments": None,
            "references": [],
            "budget": {},
        }

    monkeypatch.setattr(assemble.WritingReferenceService, "search", fake_search)
    app.dependency_overrides[get_repository] = lambda: object()
    app.dependency_overrides[get_qdrant_adapter] = lambda: object()
    app.dependency_overrides[get_ollama_client] = lambda: object()
    app.dependency_overrides[get_query_llm_client] = lambda: object()
    app.dependency_overrides[get_multi_search_settings] = lambda: Settings()
    try:
        response = TestClient(app).post("/api/v1/assemble", json={
            "writing_goal": "写一段雨夜追杀",
            "outline_context": "主角被背叛",
            "constraints": {"情绪": "紧张"},
            "book_ids": [10],
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["prompt"]["text"] == "拼装结果"
    assert captured["writing_goal"] == "写一段雨夜追杀"
    assert captured["book_ids"] == [10]
