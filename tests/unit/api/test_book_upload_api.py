from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_app_settings, get_indexer_factory, get_repository
from app.config import Settings
from app.main import app
from tests.fixtures.build_synthetic_epub import write_synthetic_epub
from tests.unit.api.test_books_api import FakeIndexer, FakeRepository


@pytest.fixture
def upload_client(tmp_path: Path):
    repository = FakeRepository()
    settings = Settings(
        _env_file=None,
        book_source_dir=tmp_path,
        allowed_source_roots=str(tmp_path),
        book_upload_max_bytes=1024 * 1024,
    )
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        yield TestClient(app), repository, tmp_path
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("小说.txt", "第一章\n正文。".encode()),
        ("随笔.md", "# 第一章\n正文。".encode()),
    ],
)
def test_upload_text_registers_without_indexing(upload_client, filename, content):
    client, repository, root = upload_client
    response = client.post(
        "/api/v1/books/upload",
        data={"title": "测试小说", "author": "作者"},
        files={"file": (filename, content, "text/plain")},
    )

    assert response.status_code == 201
    assert response.json()["source_format"] == "txt"
    book = repository.get_book(response.json()["book_id"])
    assert book["status"] == "pending"
    assert Path(book["source_path"]).read_bytes() == content
    assert Path(book["source_path"]).is_relative_to(root / "uploads")
    assert list((root / "uploads").glob("*.part")) == []


def test_upload_epub_registers(upload_client, tmp_path):
    client, repository, _ = upload_client
    source = write_synthetic_epub(tmp_path / "book.epub")
    response = client.post(
        "/api/v1/books/upload",
        data={"title": "EPUB 书"},
        files={"file": ("book.epub", source.read_bytes(), "application/epub+zip")},
    )
    assert response.status_code == 201
    assert response.json()["source_format"] == "epub"
    assert repository.get_book(response.json()["book_id"])["status"] == "pending"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("bad.pdf", b"content"),
        ("bad.md", b"\xff\xfe"),
        ("bad.txt", b"a\x00b"),
        ("bad.epub", b"not a zip"),
        ("empty.txt", b""),
    ],
)
def test_upload_rejects_invalid_files_and_cleans_temp(upload_client, filename, content):
    client, repository, root = upload_client
    response = client.post(
        "/api/v1/books/upload",
        data={"title": "不应登记"},
        files={"file": (filename, content)},
    )
    assert response.status_code == 422
    assert repository.books == {}
    upload_dir = root / "uploads"
    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


def test_upload_rejects_oversized_file(upload_client):
    client, repository, root = upload_client
    response = client.post(
        "/api/v1/books/upload",
        data={"title": "太大"},
        files={"file": ("large.txt", b"a" * (1024 * 1024 + 1))},
    )
    assert response.status_code == 422
    assert repository.books == {}
    assert list((root / "uploads").iterdir()) == []


def test_duplicate_content_reuses_book_and_discards_second_upload(tmp_path: Path):
    class DeduplicatingRepository(FakeRepository):
        def register_book(self, **kwargs):
            for book in self.books.values():
                if book["source_sha256"] == kwargs["source_sha256"]:
                    return book["id"], False
            return super().register_book(**kwargs)

    repository = DeduplicatingRepository()
    settings = Settings(
        _env_file=None, book_source_dir=tmp_path,
        allowed_source_roots=str(tmp_path),
    )
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        client = TestClient(app)
        content = "# 第一章\n正文。".encode()
        first = client.post("/api/v1/books/upload", data={"title": "首次"}, files={"file": ("a.txt", content)})
        second = client.post("/api/v1/books/upload", data={"title": "重复"}, files={"file": ("a.md", content)})
    finally:
        app.dependency_overrides.clear()
    assert first.status_code == second.status_code == 201
    assert second.json()["created"] is False
    assert second.json()["book_id"] == first.json()["book_id"]
    assert len(repository.books) == 1
    assert len(list((tmp_path / "uploads").iterdir())) == 1


def test_uploaded_book_stays_pending_until_index_is_explicitly_started(upload_client):
    client, repository, _ = upload_client
    created: list[FakeIndexer] = []

    def factory(repo):
        indexer = FakeIndexer(repo)
        created.append(indexer)
        return indexer

    app.dependency_overrides[get_indexer_factory] = lambda: factory
    try:
        upload = client.post(
            "/api/v1/books/upload",
            data={"title": "新书"},
            files={"file": ("book.md", "# 第一章\n正文。".encode())},
        )
        book_id = upload.json()["book_id"]
        assert repository.get_book(book_id)["status"] == "pending"
        assert created == []
        started = client.post(f"/api/v1/books/{book_id}/index")
    finally:
        app.dependency_overrides.pop(get_indexer_factory, None)

    assert started.status_code == 202
    assert started.json()["index_started"] is True
    assert created[0].ran is True
    assert repository.get_book(book_id)["status"] == "ready"
