from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.dependencies import close_app_resources
from app.api.routes import books, direct_query, health, search
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


@app.exception_handler(NovelRagError)
async def novel_rag_error_handler(
    _: Request,
    exc: NovelRagError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "error_type": exc.__class__.__name__},
    )
