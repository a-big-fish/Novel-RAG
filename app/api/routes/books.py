from __future__ import annotations

import logging
import codecs
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
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
from app.utils.errors import SourceValidationError, StorageError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/books", tags=["books"])


@router.get("")
def list_books(
    repository: PostgresRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    return [
        {key: book[key] for key in (
            "id", "title", "author", "status", "current_version"
        )}
        for book in repository.list_books()
    ]


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
    next_version: int | None = None


class StartIndexResponse(BaseModel):
    book_id: int
    status: str
    index_started: bool


_UPLOAD_SUFFIXES = {".epub", ".txt", ".md"}
_UPLOAD_CHUNK_BYTES = 1024 * 1024


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


def _validate_uploaded_epub(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            if (
                archive.read("mimetype").strip() != b"application/epub+zip"
                or "META-INF/container.xml" not in archive.namelist()
            ):
                raise ValueError("not an EPUB package")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise SourceValidationError("上传文件不是有效的 EPUB") from exc


@router.post(
    "/upload", response_model=AddBookResponse, response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def upload_book(
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=300),
    author: str = Form("", max_length=300),
    duplicate_action: str = Form("reject"),
    duplicate_book_id: int | None = Form(None),
    settings: Settings = Depends(get_app_settings),
    repository: PostgresRepository = Depends(get_repository),
) -> AddBookResponse:
    """Store a browser upload and register it without starting the indexer."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _UPLOAD_SUFFIXES:
        raise HTTPException(422, "仅支持 .epub、.txt 和 .md 文件")
    upload_dir = (settings.book_source_dir / "uploads").resolve()
    if not any(upload_dir.is_relative_to(root) for root in settings.allowed_roots):
        raise HTTPException(503, "上传目录不在 ALLOWED_SOURCE_ROOTS 中")
    if not title.strip():
        raise HTTPException(422, "书名不能为空")
    if duplicate_action not in {"reject", "new_version"}:
        raise HTTPException(422, "未知的重复上传操作")
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    destination: Path | None = None
    destination_created = False
    registered = False
    try:
        with tempfile.NamedTemporaryFile(dir=upload_dir, suffix=".part", delete=False) as stream:
            temp_path = Path(stream.name)
            digest = hashlib.sha256()
            decoder = codecs.getincrementaldecoder("utf-8-sig")("strict") if suffix != ".epub" else None
            size = 0
            while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > settings.book_upload_max_bytes:
                    raise SourceValidationError("上传文件超过大小限制")
                if decoder is not None:
                    if b"\x00" in chunk:
                        raise SourceValidationError("文本包含二进制空字节")
                    try:
                        decoder.decode(chunk)
                    except UnicodeDecodeError as exc:
                        raise SourceValidationError("文本必须使用 UTF-8 编码") from exc
                digest.update(chunk)
                stream.write(chunk)
            if size == 0:
                raise SourceValidationError("上传文件为空")
            if decoder is not None:
                try:
                    decoder.decode(b"", final=True)
                except UnicodeDecodeError as exc:
                    raise SourceValidationError("文本必须使用 UTF-8 编码") from exc
        if suffix == ".epub":
            _validate_uploaded_epub(temp_path)
        source_format = "epub" if suffix == ".epub" else "txt"
        source_sha256 = digest.hexdigest()
        existing = repository.find_book_for_upload(
            title=title.strip(), author=author.strip(),
            source_sha256=source_sha256,
        )
        if existing is not None:
            book_id = int(existing["id"])
            next_version = int(existing["current_version"] or 0) + 1
            if existing["status"] not in {"pending", "failed", "ready"}:
                raise HTTPException(409, "这本书正在索引，请完成后再生成新版本")
            if duplicate_action == "reject":
                raise HTTPException(409, {
                    "code": "duplicate_book",
                    "book_id": book_id,
                    "title": existing["title"],
                    "current_version": int(existing["current_version"] or 0),
                    "next_version": next_version,
                })
            if duplicate_book_id != book_id:
                raise HTTPException(409, "重复书籍已变化，请重新上传确认")
            if source_sha256 != existing["source_sha256"]:
                destination = upload_dir / f"{source_sha256}{suffix}"
                if not destination.exists():
                    os.replace(temp_path, destination)
                    temp_path = None
                    destination_created = True
                try:
                    repository.replace_book_source(
                        book_id, source_path=str(destination),
                        source_format=source_format, source_sha256=source_sha256,
                    )
                except StorageError as exc:
                    raise HTTPException(409, str(exc)) from exc
                registered = True
            return AddBookResponse(
                book_id=book_id, status=str(existing["status"]),
                source_format=source_format, created=False,
                next_version=next_version,
            )
        if duplicate_action == "new_version":
            raise HTTPException(409, "重复书籍已变化，请重新上传确认")
        destination = upload_dir / f"{source_sha256}{suffix}"
        if not destination.exists():
            os.replace(temp_path, destination)
            temp_path = None
            destination_created = True
        book_id, created = repository.register_book(
            title=title.strip(), author=author.strip(), source_path=str(destination),
            source_format=source_format, source_sha256=source_sha256,
        )
        registered = created
        if not created:
            existing = repository.get_book(book_id)
            raise HTTPException(409, {
                "code": "duplicate_book", "book_id": book_id,
                "title": existing["title"],
                "current_version": int(existing["current_version"] or 0),
                "next_version": int(existing["current_version"] or 0) + 1,
            })
        return AddBookResponse(
            book_id=book_id, status="pending",
            source_format=source_format, created=True,
        )
    except SourceValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if destination_created and not registered and destination is not None:
            # A failed registration must not leave an orphaned source file.
            destination.unlink(missing_ok=True)


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


@router.post(
    "", response_model=AddBookResponse, response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
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
    if book["status"] in {
        "converting",
        "splitting",
        "evaluating",
        "annotating",
        "indexing",
    }:
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
