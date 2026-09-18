from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import (
    IndexerFactory,
    get_app_settings,
    get_indexer_factory,
    get_repository,
)
from app.config import Settings
from app.db.postgres import PostgresRepository
from app.utils.epub import sha256_file
from app.utils.errors import SourceValidationError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/books", tags=["books"])


class AddBookRequest(BaseModel):
    source_path: str
    title: str = Field(min_length=1, max_length=300)
    author: str = Field(default="", max_length=300)


class AddBookResponse(BaseModel):
    book_id: int
    status: str
    source_format: str
    index_started: bool = False
    created: bool


class StartIndexResponse(BaseModel):
    book_id: int
    status: str
    index_started: bool


def _validate_source_path(source_path: str, settings: Settings) -> Path:
    path = Path(source_path).expanduser().resolve()
    if not path.is_file():
        raise SourceValidationError(f"source file not found: {path}")
    if not any(path.is_relative_to(root) for root in settings.allowed_roots):
        raise SourceValidationError(
            f"source file is outside ALLOWED_SOURCE_ROOTS: {path}"
        )
    return path


def _detect_source_format(path: Path) -> str:
    if zipfile.is_zipfile(path):
        if path.suffix.lower() != ".epub":
            raise SourceValidationError("ZIP source must use the .epub suffix")
        return "epub"

    try:
        sample = path.read_bytes()[:8192]
        sample.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceValidationError("TXT source must be UTF-8 encoded") from exc
    if b"\x00" in sample:
        raise SourceValidationError("TXT source contains binary NUL bytes")
    return "txt"


def _run_index(
    book_id: int,
    repository: PostgresRepository,
    indexer_factory: IndexerFactory,
) -> None:
    indexer = None
    try:
        indexer = indexer_factory(repository)
        result = indexer.run(book_id)
        logger.info("index completed", extra={"book_id": book_id, **result})
    except Exception:
        logger.exception("background index failed", extra={"book_id": book_id})
    finally:
        if indexer is not None:
            indexer.close()


@router.post("", response_model=AddBookResponse, status_code=status.HTTP_201_CREATED)
def add_new_book(
    payload: AddBookRequest,
    settings: Settings = Depends(get_app_settings),
    repository: PostgresRepository = Depends(get_repository),
) -> AddBookResponse:
    try:
        source_path = _validate_source_path(payload.source_path, settings)
        source_format = _detect_source_format(source_path)
        source_sha256 = sha256_file(source_path)
    except SourceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    book_id, created = repository.register_book(
        title=payload.title,
        author=payload.author,
        source_path=str(source_path),
        source_format=source_format,
        source_sha256=source_sha256,
    )
    return AddBookResponse(
        book_id=book_id,
        status="pending",
        source_format=source_format,
        created=created,
    )


@router.post(
    "/{book_id}/index",
    response_model=StartIndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_index(
    book_id: int,
    background_tasks: BackgroundTasks,
    repository: PostgresRepository = Depends(get_repository),
    indexer_factory: IndexerFactory = Depends(get_indexer_factory),
) -> StartIndexResponse:
    book = repository.get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    if book["status"] == "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="book is already indexed; rebuild is not enabled in this version",
        )
    if book["status"] in {"converting", "splitting", "annotating", "indexing"}:
        return StartIndexResponse(
            book_id=book_id,
            status=str(book["status"]),
            index_started=False,
        )

    initial_status = "converting" if book["source_format"] == "epub" else "splitting"
    claimed = repository.claim_book_for_index(
        book_id,
        initial_status=initial_status,
    )
    if not claimed:
        latest = repository.get_book(book_id)
        return StartIndexResponse(
            book_id=book_id,
            status=str(latest["status"] if latest else "unknown"),
            index_started=False,
        )
    background_tasks.add_task(
        _run_index,
        book_id,
        repository,
        indexer_factory,
    )
    return StartIndexResponse(
        book_id=book_id,
        status=initial_status,
        index_started=True,
    )


@router.get("/{book_id}")
def get_book(
    book_id: int,
    repository: PostgresRepository = Depends(get_repository),
) -> dict[str, Any]:
    book = repository.get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    return dict(book)


@router.get("/{book_id}/chapters/{chapter_index}")
def get_chapter(
    book_id: int,
    chapter_index: int,
    repository: PostgresRepository = Depends(get_repository),
) -> dict[str, Any]:
    chapter = repository.get_chapter(book_id, chapter_index)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    return dict(chapter)


@router.get("/{book_id}/jobs")
def list_jobs(
    book_id: int,
    repository: PostgresRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    return [dict(row) for row in repository.list_jobs(book_id)]
