from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_app_settings,
    get_database,
    get_ollama_client,
    get_qdrant_adapter,
)
from app.config import Settings
from app.main import app


class FakeDatabase:
    def ping(self) -> None:
        return None


class FakeQdrant:
    def ping(self) -> None:
        return None


class FakeOllama:
    def ping(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_version="test-version",
        llm_adapter="openai_compatible",
        llm_base_url="http://llm.test/v1",
        llm_model="test-model",
    )


def test_health_reports_all_dependencies() -> None:
    app.dependency_overrides[get_app_settings] = _settings
    app.dependency_overrides[get_database] = lambda: FakeDatabase()
    app.dependency_overrides[get_qdrant_adapter] = lambda: FakeQdrant()
    app.dependency_overrides[get_ollama_client] = lambda: FakeOllama()
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "test-version"
    assert body["dependencies"]["postgres"]["status"] == "ok"
    assert body["dependencies"]["qdrant"]["status"] == "ok"
    assert body["dependencies"]["ollama"]["status"] == "ok"
    assert body["dependencies"]["llm"] == {
        "status": "configured",
        "adapter": "openai_compatible",
    }
