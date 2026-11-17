from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services.index_worker import IndexWorker
from app.utils.errors import StorageError


class FakeRepository:
    def __init__(self, job: dict[str, Any] | None) -> None:
        self.job = job
        self.completed: dict[str, Any] = {}
        self.failed: dict[str, Any] = {}
        self.checkpoints: list[dict[str, Any]] = []
        self.heartbeats = 0
        self.reclaimed = 0

    def reclaim_expired_index_jobs(self) -> int:
        return self.reclaimed

    def claim_index_job(self, **kwargs: Any):
        job, self.job = self.job, None
        return job

    def heartbeat_index_job(self, *args: Any, **kwargs: Any) -> bool:
        self.heartbeats += 1
        return True

    def complete_index_job(self, job_id: int, **kwargs: Any) -> bool:
        self.completed = {"job_id": job_id, **kwargs}
        return True

    def fail_index_job(self, job_id: int, **kwargs: Any) -> str:
        self.failed = {"job_id": job_id, **kwargs}
        return "retrying" if kwargs.get("retry_in_seconds") else "failed"

    def update_index_job_checkpoint(self, job_id: int, checkpoint: dict, **kwargs: Any):
        self.checkpoints.append({"job_id": job_id, "checkpoint": checkpoint, **kwargs})

    def get_book(self, book_id: int):
        return {"id": book_id, "current_version": 2, "status": "ready"}


class FakeIndexer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.kwargs: dict[str, Any] = {}
        self.closed = False

    def run(self, book_id: int, **kwargs: Any):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        kwargs["on_stage_complete"]("split")
        return {"book_id": book_id, "version": kwargs["version"]}

    def close(self) -> None:
        self.closed = True


def job(**overrides: Any) -> dict[str, Any]:
    value = {
        "id": 11, "book_id": 7, "attempts": 1, "max_attempts": 3,
        "checkpoint": {}, "job_type": "index", "status": "leased",
    }
    value.update(overrides)
    return value


def build(repository: FakeRepository, indexer: FakeIndexer) -> IndexWorker:
    return IndexWorker(
        repository,  # type: ignore[arg-type]
        lambda _repo: indexer,
        Settings(index_worker_heartbeat_seconds=5, index_worker_poll_seconds=0.5),
    )


def test_worker_completes_job_and_persists_checkpoint():
    repository = FakeRepository(job())
    indexer = FakeIndexer()
    worker = build(repository, indexer)
    assert worker.process_one() is True
    assert indexer.kwargs["version"] == 3
    assert indexer.kwargs["completed_stages"] == set()
    assert repository.completed["job_id"] == 11
    assert repository.completed["result"]["version"] == 3
    assert any("split" in checkpoint["checkpoint"]["completed_stages"]
               for checkpoint in repository.checkpoints)
    assert indexer.closed is True


def test_worker_resumes_from_checkpoint():
    repository = FakeRepository(job(checkpoint={
        "target_version": 5, "completed_stages": ["split"],
    }))
    indexer = FakeIndexer()
    worker = build(repository, indexer)
    worker.process_one()
    assert indexer.kwargs["version"] == 5
    assert indexer.kwargs["completed_stages"] == {"split"}


def test_worker_schedules_retry_for_transient_error():
    repository = FakeRepository(job())
    worker = build(repository, FakeIndexer(StorageError("timeout")))
    worker.process_one()
    assert repository.failed["retry_in_seconds"] == 30
    assert repository.completed == {}


def test_worker_fails_permanent_error_without_retry():
    repository = FakeRepository(job())
    worker = build(repository, FakeIndexer(ValueError("bad source")))
    worker.process_one()
    assert repository.failed["retry_in_seconds"] is None


def test_worker_returns_false_when_queue_is_empty():
    worker = build(FakeRepository(None), FakeIndexer())
    assert worker.process_one() is False


def test_worker_reclaims_expired_leases_on_start():
    repository = FakeRepository(None)
    repository.reclaimed = 2
    worker = build(repository, FakeIndexer())
    worker.start()
    worker.stop()
    assert repository.reclaimed == 2


def test_poll_reclaims_leases_that_expire_after_start():
    class Repository(FakeRepository):
        scans = 0

        def reclaim_expired_index_jobs(self):
            self.scans += 1
            if self.scans == 2:
                self.job = job()
            return 0

    repository = Repository(None)
    worker = build(repository, FakeIndexer())
    assert worker.process_one() is False
    assert worker.process_one() is True
    assert repository.completed["job_id"] == 11


def test_storage_proxy_rejects_old_owner_after_takeover():
    from contextlib import contextmanager
    from app.services.index_worker import LeaseProtected
    import pytest

    class Storage:
        writes = 0

        def upsert_scene_points(self):
            self.writes += 1

    owner = ["first"]

    @contextmanager
    def guard():
        if owner[0] != "first":
            raise StorageError("index job lease lost")
        yield

    storage = Storage()
    protected = LeaseProtected(storage, guard)
    protected.upsert_scene_points()
    owner[0] = "second"
    with pytest.raises(StorageError, match="lease lost"):
        protected.upsert_scene_points()
    assert storage.writes == 1
