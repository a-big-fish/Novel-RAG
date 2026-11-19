from __future__ import annotations

import json

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


def _fake_search(self, **kwargs):
    on_progress = kwargs.get("on_progress")
    if on_progress is not None:
        on_progress("assembling", {"candidate_count": 1})
        on_progress("assembling_complete", {
            "status": "ok", "included": 1, "dropped": 0,
            "prompt_chars": 4, "latency_ms": 1.0,
        })
    return {
        "query_plan": {"query": kwargs["writing_goal"]},
        "book_ids": [10],
        "search": {},
        "prompt": {"text": "拼装结果", "char_count": 4, "budget": {}},
        "fragments": None,
        "references": [],
        "budget": {},
    }


def _override_dependencies():
    app.dependency_overrides[get_repository] = lambda: object()
    app.dependency_overrides[get_qdrant_adapter] = lambda: object()
    app.dependency_overrides[get_ollama_client] = lambda: object()
    app.dependency_overrides[get_query_llm_client] = lambda: object()
    app.dependency_overrides[get_multi_search_settings] = lambda: Settings()


def test_assemble_endpoint_returns_service_payload(monkeypatch):
    captured = {}

    def fake_search(self, **kwargs):
        captured.update(kwargs)
        return _fake_search(self, **kwargs)

    monkeypatch.setattr(assemble.WritingReferenceService, "search", fake_search)
    _override_dependencies()
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


def test_assemble_stream_emits_progress_and_result(monkeypatch):
    monkeypatch.setattr(assemble.WritingReferenceService, "search", _fake_search)
    _override_dependencies()
    try:
        with TestClient(app).stream(
            "POST", "/api/v1/assemble/stream",
            json={"writing_goal": "写一段雨夜追杀", "book_ids": [10]},
        ) as response:
            assert response.status_code == 200
            events = [
                json.loads(line) for line in response.iter_lines() if line.strip()
            ]
    finally:
        app.dependency_overrides.clear()

    stages = [event.get("stage") for event in events if event["type"] == "progress"]
    assert "assembling" in stages
    assert "assembling_complete" in stages
    result = next(event for event in events if event["type"] == "result")
    assert result["data"]["prompt"]["text"] == "拼装结果"
