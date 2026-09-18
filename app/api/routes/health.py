from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    get_app_settings,
    get_database,
    get_ollama_client,
    get_qdrant_adapter,
)
from app.clients.ollama_client import OllamaClient
from app.config import Settings
from app.db.postgres import PostgresDatabase
from app.db.qdrant import QdrantAdapter

router = APIRouter()


def _check(callable_: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        callable_()
    except Exception as exc:
        return {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": str(exc)[:500],
        }
    return {
        "status": "ok",
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@router.get("/health")
def health(
    settings: Settings = Depends(get_app_settings),
    database: PostgresDatabase = Depends(get_database),
    qdrant: QdrantAdapter = Depends(get_qdrant_adapter),
    ollama: OllamaClient = Depends(get_ollama_client),
) -> JSONResponse:
    dependencies: dict[str, Any] = {
        "postgres": _check(database.ping),
        "qdrant": _check(qdrant.ping),
        "ollama": _check(ollama.ping),
    }
    llm_configured = settings.llm_adapter == "codex_exec" or bool(
        settings.llm_base_url and settings.llm_model
    )
    dependencies["llm"] = {
        "status": "configured" if llm_configured else "not_configured",
        "adapter": settings.llm_adapter,
    }

    if any(
        dependencies[name]["status"] != "ok"
        for name in ("postgres", "qdrant")
    ):
        status = "unhealthy"
        http_status = 503
    elif dependencies["ollama"]["status"] != "ok" or not llm_configured:
        status = "degraded"
        http_status = 200
    else:
        status = "ok"
        http_status = 200

    return JSONResponse(
        status_code=http_status,
        content={
            "status": status,
            "version": settings.app_version,
            "dependencies": dependencies,
        },
    )
