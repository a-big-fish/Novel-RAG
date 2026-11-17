"""In-process index worker backed by the PostgreSQL job queue.

No external broker: jobs are leased with ``FOR UPDATE SKIP LOCKED``, heartbeats
keep the lease alive, expired leases are reclaimed, and completed stages are
written to ``index_jobs.checkpoint`` so a restart resumes after ``split`` instead
of discarding downstream work.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from contextlib import contextmanager
from threading import Event, RLock, Thread
from typing import Any
from uuid import uuid4

from app.config import Settings, get_settings
from app.db.postgres import PostgresRepository
from app.services.indexer import Indexer
from app.utils.errors import (
    EpubConversionError,
    ModelInputTooLargeError,
    SourceValidationError,
    TextProcessingError,
    StorageError,
)

logger = logging.getLogger(__name__)

IndexerFactory = Callable[[PostgresRepository], Indexer]

PERMANENT_ERRORS = (
    FileNotFoundError,
    ValueError,
    EpubConversionError,
    SourceValidationError,
    TextProcessingError,
    ModelInputTooLargeError,
)


class LeaseProtected:
    """Fence all pipeline storage operations, including nested service writes."""

    def __init__(self, target: Any, guard: Callable) -> None:
        self._target = target
        self._guard = guard

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        if not callable(value) or name == "close":
            return value

        def protected(*args: Any, **kwargs: Any) -> Any:
            with self._guard():
                return value(*args, **kwargs)
        return protected


class IndexWorker:
    def __init__(
        self,
        repository: PostgresRepository,
        indexer_factory: IndexerFactory,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.indexer_factory = indexer_factory
        self.settings = settings or get_settings()
        self.worker_id = (
            f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        )
        self._stop = Event()
        self._thread: Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        reclaimed = self.repository.reclaim_expired_index_jobs()
        if reclaimed:
            logger.info("reclaimed expired index jobs", extra={"count": reclaimed})
        self._stop.clear()
        self._thread = Thread(
            target=self._loop, name="novel-rag-index-worker", daemon=True,
        )
        self._thread.start()
        logger.info(
            "index worker started",
            extra={"worker_id": self.worker_id,
                   "poll_seconds": self.settings.index_worker_poll_seconds},
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        logger.info("index worker stopped", extra={"worker_id": self.worker_id})

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            worked = False
            try:
                worked = self.process_one()
            except Exception:
                logger.exception("index worker loop failed")
            if not worked:
                self._stop.wait(self.settings.index_worker_poll_seconds)

    def process_one(self) -> bool:
        """Lease and execute one queued job. Returns whether a job was found."""

        self.repository.reclaim_expired_index_jobs()
        job = self.repository.claim_index_job(
            worker_id=f"{self.worker_id}-{uuid4().hex}",
            lease_seconds=self.settings.index_worker_lease_seconds,
        )
        if job is None:
            return False
        self._execute(job)
        return True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _execute(self, job: dict[str, Any]) -> None:
        holder: dict[str, Any] = {}
        thread = Thread(
            target=self._run_job, args=(job, holder),
            name=f"index-job-{job['id']}", daemon=True,
        )
        thread.start()
        heartbeat = self.settings.index_worker_heartbeat_seconds
        while thread.is_alive() and not self._stop.is_set():
            thread.join(timeout=heartbeat)
            if not thread.is_alive():
                break
            if not self.repository.heartbeat_index_job(
                int(job["id"]),
                worker_id=job.get("worker_id") or self.worker_id,
                lease_seconds=self.settings.index_worker_lease_seconds,
            ):
                logger.warning(
                    "index job lease lost while running",
                    extra={"job_id": job["id"], "book_id": job["book_id"]},
                )
                break
        if thread.is_alive():
            logger.info(
                "worker stopping; current job stays leased for takeover",
                extra={"job_id": job["id"], "book_id": job["book_id"]},
            )
            return
        if "error" in holder:
            logger.error(
                "index job failed",
                extra={"job_id": job["id"], "book_id": job["book_id"],
                       "error": str(holder["error"])[:500]},
            )

    def _run_job(self, job: dict[str, Any], holder: dict[str, Any]) -> None:
        indexer: Indexer | None = None
        owner = job.get("worker_id") or self.worker_id
        lock = RLock()

        @contextmanager
        def guard():
            with lock:
                if self._stop.is_set():
                    raise StorageError("index worker stopped")
                with self.repository.index_job_lease(int(job["id"]), owner):
                    yield

        try:
            book = self.repository.get_book(int(job["book_id"]))
            if book is None:
                raise ValueError(f"book not found: {job['book_id']}")
            checkpoint = dict(job.get("checkpoint") or {})
            target_version = int(
                checkpoint.get("target_version")
                or int(book["current_version"] or 0) + 1
            )
            stages = list(checkpoint.get("completed_stages") or [])
            self.repository.update_index_job_checkpoint(
                int(job["id"]),
                {"target_version": target_version, "completed_stages": stages},
                stage="starting",
                worker_id=owner,
            )

            def on_stage_complete(stage: str) -> None:
                if stage not in stages:
                    stages.append(stage)
                self.repository.update_index_job_checkpoint(
                    int(job["id"]),
                    {"target_version": target_version,
                     "completed_stages": stages},
                    stage=stage,
                    worker_id=owner,
                )

            indexer = self.indexer_factory(LeaseProtected(self.repository, guard))
            if hasattr(indexer, "qdrant"):
                indexer.qdrant = LeaseProtected(indexer.qdrant, guard)
            result = indexer.run(
                int(job["book_id"]),
                version=target_version,
                completed_stages=set(stages),
                on_stage_complete=on_stage_complete,
            )
            completed = self.repository.complete_index_job(
                int(job["id"]), worker_id=owner, result=result,
            )
            if not completed:
                logger.warning(
                    "job finished after the lease was taken over",
                    extra={"job_id": job["id"]},
                )
            holder["result"] = result
        except Exception as exc:  # noqa: BLE001 - worker must survive job failures
            holder["error"] = exc
            retry_in = self._retry_delay(job, exc)
            status = self.repository.fail_index_job(
                int(job["id"]), worker_id=owner,
                error=str(exc), retry_in_seconds=retry_in,
            )
            logger.warning(
                "index job scheduled",
                extra={"job_id": job["id"], "book_id": job["book_id"],
                       "status": status, "retry_in_seconds": retry_in},
            )
        finally:
            if indexer is not None:
                indexer.close()

    def _retry_delay(
        self, job: dict[str, Any], exc: Exception,
    ) -> int | None:
        attempts = int(job.get("attempts") or 1)
        max_attempts = int(job.get("max_attempts") or 1)
        if attempts >= max_attempts:
            return None
        if isinstance(exc, PERMANENT_ERRORS):
            return None
        delay = self.settings.index_job_retry_backoff_seconds * (
            2 ** max(0, attempts - 1)
        )
        return min(delay, self.settings.index_job_retry_max_seconds)


def build_index_worker(
    repository: PostgresRepository,
    *,
    settings: Settings | None = None,
) -> IndexWorker:
    settings = settings or get_settings()

    def factory(repo: PostgresRepository) -> Indexer:
        return Indexer.from_settings(repo, settings=settings)

    return IndexWorker(repository, factory, settings)
