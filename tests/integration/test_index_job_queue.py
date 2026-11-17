from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update

from app.db.models import books, index_jobs
from app.db.postgres import PostgresRepository
from tests.integration.support import build_test_database

pytestmark = pytest.mark.integration


def _register(repository: PostgresRepository) -> int:
    marker = uuid.uuid4().hex
    book_id, _ = repository.register_book(
        title="队列集成测试书",
        author="test",
        source_path=f"E:/novels/tool/{marker}.txt",
        source_format="txt",
        source_sha256=hashlib.sha256(marker.encode()).hexdigest(),
    )
    return book_id


def _cleanup(repository: PostgresRepository, book_id: int) -> None:
    with repository.engine.begin() as connection:
        connection.execute(delete(index_jobs).where(index_jobs.c.book_id == book_id))
        connection.execute(delete(books).where(books.c.id == book_id))


def test_queue_lease_takeover_and_completion() -> None:
    repository = PostgresRepository(build_test_database())
    book_id = _register(repository)
    try:
        job = repository.enqueue_index_job(book_id, max_attempts=3)
        assert job["status"] == "queued"
        duplicate = repository.enqueue_index_job(book_id)
        assert duplicate["id"] == job["id"]

        claimed = repository.claim_index_job(worker_id="worker-a", lease_seconds=120)
        assert claimed is not None
        assert claimed["id"] == job["id"]
        assert claimed["status"] == "leased"
        assert claimed["attempts"] == 1

        # A second active lease cannot be claimed until the first expires.
        assert repository.claim_index_job(
            worker_id="worker-b", lease_seconds=120,
        ) is None
        assert repository.heartbeat_index_job(
            job["id"], worker_id="worker-a", lease_seconds=120,
        )

        with repository.engine.begin() as connection:
            connection.execute(
                update(index_jobs)
                .where(index_jobs.c.id == job["id"])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert repository.reclaim_expired_index_jobs() >= 1
        assert repository.get_index_job(job["id"])["status"] == "retrying"

        takeover = repository.claim_index_job(worker_id="worker-b", lease_seconds=120)
        assert takeover is not None
        assert takeover["id"] == job["id"]
        assert takeover["attempts"] == 2
        from app.utils.errors import StorageError
        with pytest.raises(StorageError, match="lease lost"):
            repository.update_index_job_checkpoint(
                job["id"], {"wrong": True}, worker_id="worker-a",
            )
        with pytest.raises(StorageError, match="lease lost"):
            with repository.index_job_lease(job["id"], "worker-a"):
                pytest.fail("stale worker entered protected storage operation")
        assert repository.fail_index_job(
            job["id"], worker_id="worker-a", error="late failure",
            retry_in_seconds=30,
        ) == "lease_lost"
        assert repository.get_index_job(job["id"])["worker_id"] == "worker-b"
        assert repository.complete_index_job(
            job["id"], worker_id="worker-b", result={"ok": True},
        )
        assert repository.get_index_job(job["id"])["status"] == "completed"

        # Completed jobs no longer block a new index request.
        new_job = repository.enqueue_index_job(book_id)
        assert new_job["id"] != job["id"]
    finally:
        _cleanup(repository, book_id)


def test_retry_backoff_and_max_attempts() -> None:
    repository = PostgresRepository(build_test_database())
    book_id = _register(repository)
    try:
        job = repository.enqueue_index_job(book_id, max_attempts=2)
        repository.claim_index_job(worker_id="worker-a", lease_seconds=60)
        status = repository.fail_index_job(
            job["id"], worker_id="worker-a", error="timeout",
            retry_in_seconds=600,
        )
        assert status == "retrying"
        assert repository.get_index_job(job["id"])["status"] == "retrying"
        # Still inside the backoff window.
        assert repository.claim_index_job(
            worker_id="worker-a", lease_seconds=60,
        ) is None

        with repository.engine.begin() as connection:
            connection.execute(
                update(index_jobs)
                .where(index_jobs.c.id == job["id"])
                .values(next_retry_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert repository.claim_index_job(
            worker_id="worker-a", lease_seconds=60,
        ) is not None
        assert repository.fail_index_job(
            job["id"], worker_id="worker-a", error="timeout again",
            retry_in_seconds=600,
        ) == "failed"
        record = repository.get_index_job(job["id"])
        assert record["status"] == "failed"
        assert record["finished_at"] is not None
    finally:
        _cleanup(repository, book_id)
