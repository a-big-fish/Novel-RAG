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


def test_duplicate_upload_requires_confirmation_before_new_version(tmp_path: Path):
    repository = FakeRepository()
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
        confirmed = client.post(
            "/api/v1/books/upload",
            data={"title": "重复", "duplicate_action": "new_version", "duplicate_book_id": first.json()["book_id"]},
            files={"file": ("a.md", content)},
        )
    finally:
        app.dependency_overrides.clear()
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == {
        "code": "duplicate_book", "book_id": first.json()["book_id"],
        "title": "首次", "current_version": 0, "next_version": 1,
    }
    assert confirmed.status_code == 201
    assert confirmed.json()["created"] is False
    assert confirmed.json()["next_version"] == 1
    assert len(repository.books) == 1
    assert len(list((tmp_path / "uploads").iterdir())) == 1


def test_same_title_new_file_updates_source_only_after_confirmation(upload_client):
    client, repository, root = upload_client
    first = client.post(
        "/api/v1/books/upload", data={"title": "同一本书", "author": "甲"},
        files={"file": ("book.txt", b"first version")},
    )
    book_id = first.json()["book_id"]
    repository.books[book_id]["current_version"] = 1
    repository.books[book_id]["status"] = "ready"
    original_path = repository.books[book_id]["source_path"]
    changed = {"file": ("book.txt", b"second version")}
    duplicate = client.post(
        "/api/v1/books/upload", data={"title": "同一本书", "author": "甲"},
        files=changed,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["next_version"] == 2
    assert repository.books[book_id]["source_path"] == original_path
    stale_confirmation = client.post(
        "/api/v1/books/upload",
        data={"title": "同一本书", "author": "甲", "duplicate_action": "new_version", "duplicate_book_id": book_id + 1},
        files=changed,
    )
    assert stale_confirmation.status_code == 409
    assert repository.books[book_id]["source_path"] == original_path
    confirmed = client.post(
        "/api/v1/books/upload",
        data={"title": "同一本书", "author": "甲", "duplicate_action": "new_version", "duplicate_book_id": book_id},
        files=changed,
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["next_version"] == 2
    assert repository.books[book_id]["current_version"] == 1
    assert repository.books[book_id]["source_path"] != original_path
    assert Path(repository.books[book_id]["source_path"]).read_bytes() == b"second version"
    assert Path(original_path).read_bytes() == b"first version"
    assert len(list((root / "uploads").iterdir())) == 2


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
