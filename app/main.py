from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import ProgrammingError

from app.api.dependencies import close_app_resources
from app.api.routes import books, direct_query, health, observe, search
from app.config import get_settings
from app.logging_config import configure_logging
from app.utils.errors import NovelRagError


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    try:
        yield
    finally:
        close_app_resources()


settings = get_settings()
app = FastAPI(
    title="novel-rag",
    version=settings.app_version,
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(books.router, prefix=settings.api_prefix)
app.include_router(direct_query.router, prefix=settings.api_prefix)
app.include_router(search.router, prefix=settings.api_prefix)
app.include_router(observe.router, prefix=settings.api_prefix)


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[1] / "dashboard" / "index.html")


@app.exception_handler(NovelRagError)
async def novel_rag_error_handler(
    _: Request,
    exc: NovelRagError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "error_type": exc.__class__.__name__},
    )


@app.exception_handler(ProgrammingError)
async def database_schema_error_handler(
    _: Request,
    exc: ProgrammingError,
) -> JSONResponse:
    if getattr(exc.orig, "sqlstate", None) == "42P01":
        return JSONResponse(
            status_code=503,
            content={"detail": "配置的 PostgreSQL 库缺少项目表。预览现有测试数据请运行 scripts/start_dashboard_preview.ps1；正式库需先完成迁移。"},
        )
    return JSONResponse(status_code=500, content={"detail": "PostgreSQL 查询失败"})
