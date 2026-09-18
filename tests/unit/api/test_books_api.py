from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_app_settings,
    get_indexer_factory,
    get_repository,
)
from app.config import Settings
from app.main import app


class FakeRepository:
    def __init__(self) -> None:
        self.books: dict[int, dict[str, Any]] = {}
        self.jobs: list[dict[str, Any]] = []

    def register_book(self, **kwargs: Any) -> tuple[int, bool]:
        book_id = len(self.books) + 1
        self.books[book_id] = {
            "id": book_id,
            **kwargs,
            "status": "pending",
            "current_version": 0,
        }
        return book_id, True

    def get_book(self, book_id: int) -> dict[str, Any] | None:
        return self.books.get(book_id)

    def claim_book_for_index(
        self,
        book_id: int,
        *,
        initial_status: str = "converting",
    ) -> bool:
        book = self.books.get(book_id)
        if not book or book["status"] not in {"pending", "failed"}:
            return False
        book["status"] = initial_status
        return True

    def get_chapter(self, book_id: int, chapter_index: int) -> dict[str, Any] | None:
        book = self.books.get(book_id)
        if not book or chapter_index != 1:
            return None
        return {
            "id": 11,
            "book_id": book_id,
            "chapter_index": 1,
            "title": "第一章",
            "raw_text": "正文。",
        }

    def list_jobs(self, book_id: int) -> list[dict[str, Any]]:
        return [job for job in self.jobs if job["book_id"] == book_id]


class FakeIndexer:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.closed = False
        self.ran = False

    def run(self, book_id: int) -> dict[str, Any]:
        self.ran = True
        self.repository.books[book_id]["status"] = "ready"
        self.repository.books[book_id]["current_version"] = 1
        return {"book_id": book_id, "version": 1}

    def close(self) -> None:
        self.closed = True


def _book(
    *,
    book_id: int,
    source_format: str,
    status: str = "pending",
) -> dict[str, Any]:
    return {
        "id": book_id,
        "title": "测试书",
        "author": "作者",
        "source_path": f"E:/novels/tool/{book_id}.{source_format}",
        "source_format": source_format,
        "source_sha256": f"sha-{book_id}",
        "status": status,
        "current_version": 0,
    }


def test_add_new_book_registers_without_indexing(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("第一章\n\n正文。", encoding="utf-8")
    repository = FakeRepository()
    settings = Settings(
        _env_file=None,
        allowed_source_roots=str(tmp_path),
    )
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        response = TestClient(app).post(
            "/api/v1/books",
            json={
                "source_path": str(source),
                "title": "微型小说",
                "author": "作者",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {
        "book_id": 1,
        "status": "pending",
        "source_format": "txt",
        "index_started": False,
        "created": True,
    }


def test_add_new_book_rejects_path_outside_roots(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("正文", encoding="utf-8")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        _env_file=None,
        allowed_source_roots=str(allowed),
    )
    try:
        response = TestClient(app).post(
            "/api/v1/books",
            json={"source_path": str(source), "title": "书"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_start_index_txt_starts_from_splitting(tmp_path: Path) -> None:
    repository = FakeRepository()
    repository.books[7] = _book(book_id=7, source_format="txt")
    created: list[FakeIndexer] = []

    def factory(repo: FakeRepository) -> FakeIndexer:
        indexer = FakeIndexer(repo)
        created.append(indexer)
        return indexer

    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_indexer_factory] = lambda: factory
    try:
        response = TestClient(app).post("/api/v1/books/7/index")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "book_id": 7,
        "status": "splitting",
        "index_started": True,
    }
    assert created[0].ran is True
    assert created[0].closed is True
    assert repository.books[7]["status"] == "ready"


def test_start_index_epub_starts_from_converting(tmp_path: Path) -> None:
    repository = FakeRepository()
    repository.books[8] = _book(book_id=8, source_format="epub")
    created: list[FakeIndexer] = []

    def factory(repo: FakeRepository) -> FakeIndexer:
        indexer = FakeIndexer(repo)
        created.append(indexer)
        return indexer

    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_indexer_factory] = lambda: factory
    try:
        response = TestClient(app).post("/api/v1/books/8/index")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "book_id": 8,
        "status": "converting",
        "index_started": True,
    }
    assert created[0].ran is True
    assert created[0].closed is True


def test_start_index_does_not_duplicate_running_job() -> None:
    repository = FakeRepository()
    repository.books[9] = _book(
        book_id=9,
        source_format="txt",
        status="annotating",
    )
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_indexer_factory] = lambda: (
        lambda _: (_ for _ in ()).throw(AssertionError("must not start"))
    )
    try:
        response = TestClient(app).post("/api/v1/books/9/index")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "book_id": 9,
        "status": "annotating",
        "index_started": False,
    }


def test_start_index_rejects_ready_book() -> None:
    repository = FakeRepository()
    repository.books[10] = _book(
        book_id=10,
        source_format="txt",
        status="ready",
    )
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        response = TestClient(app).post("/api/v1/books/10/index")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_postgres_direct_query_endpoints() -> None:
    repository = FakeRepository()
    repository.books[1] = _book(book_id=1, source_format="txt", status="ready")
    repository.jobs.append(
        {"id": 21, "book_id": 1, "stage": "sync", "status": "completed"}
    )
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        client = TestClient(app)
        book = client.get("/api/v1/books/1")
        chapter = client.get("/api/v1/books/1/chapters/1")
        jobs = client.get("/api/v1/books/1/jobs")
        missing = client.get("/api/v1/books/1/chapters/2")
    finally:
        app.dependency_overrides.clear()

    assert book.status_code == 200
    assert book.json()["status"] == "ready"
    assert chapter.status_code == 200
    assert chapter.json()["title"] == "第一章"
    assert jobs.json() == [
        {"id": 21, "book_id": 1, "stage": "sync", "status": "completed"}
    ]
    assert missing.status_code == 404
