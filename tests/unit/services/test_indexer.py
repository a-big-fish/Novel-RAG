from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.services.indexer import Indexer
from app.services.llm_splitter import SceneBoundaryDecision
from app.services.tagger import TagVocabulary
from app.utils.epub import sha256_file
from app.utils.errors import NovelRagError


class FakeRepository:
    def __init__(self) -> None:
        self.embedding_cache_written = False

    def scene_split_cache_key(self, **_kwargs: Any) -> str:
        return "split-test-key"

    def get_scene_split_cache(self, _input_hash: str) -> None:
        return None

    def put_scene_split_cache(self, **_kwargs: Any) -> None:
        return None

    def sync_token_map(
        self,
        book_id: int,
        token_doc_freq: dict[str, int],
    ) -> dict[str, int]:
        return {token: index for index, token in enumerate(token_doc_freq)}

    def embedding_cache_key(self, **kwargs: Any) -> str:
        return "test-key"

    def get_embedding_cache(self, input_hash: str) -> None:
        return None

    def put_embedding_cache(self, **kwargs: Any) -> None:
        self.embedding_cache_written = True


class FakeClosable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _indexer(repository: FakeRepository, settings: Settings) -> Indexer:
    return Indexer(
        repository=repository,  # type: ignore[arg-type]
        qdrant=FakeClosable(),  # type: ignore[arg-type]
        llm_client=FakeClosable(),  # type: ignore[arg-type]
        ollama_client=FakeClosable(),  # type: ignore[arg-type]
        vocabulary=TagVocabulary([]),
        settings=settings,
    )


def test_build_points_rejects_unannotated_scene() -> None:
    repository = FakeRepository()
    indexer = _indexer(repository, Settings(_env_file=None))

    with pytest.raises(NovelRagError, match="without successful annotation"):
        indexer._build_points(
            book_id=1,
            version=1,
            scene_rows=[
                {
                    "id": 10,
                    "text": "雨落在旧城的屋檐上。",
                    "reference_status": "selected",
                    "annotate_status": "failed_permanent",
                }
            ],
        )

    assert repository.embedding_cache_written is False


def test_build_points_rejects_archived_scene() -> None:
    repository = FakeRepository()
    indexer = _indexer(repository, Settings(_env_file=None))

    with pytest.raises(NovelRagError, match="not selected"):
        indexer._build_points(
            book_id=1,
            version=1,
            scene_rows=[
                {
                    "id": 11,
                    "text": "他们离开房间，去往下一处地点。",
                    "reference_status": "archived",
                    "annotate_status": "not_applicable",
                }
            ],
        )

    assert repository.embedding_cache_written is False


def test_txt_prepare_uses_current_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("第一章\n\n第一版正文。", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        data_converted_dir=tmp_path / "converted",
    )
    indexer = _indexer(FakeRepository(), settings)

    first = indexer._converted_txt(
        {"source_path": str(source), "source_format": "txt"}
    )
    assert first.name.startswith(sha256_file(source))
    assert "第一版正文" in first.read_text(encoding="utf-8")

    source.write_text("第一章\n\n第二版正文。", encoding="utf-8")
    second = indexer._converted_txt(
        {"source_path": str(source), "source_format": "txt"}
    )
    assert second != first
    assert second.name.startswith(sha256_file(source))
    assert "第二版正文" in second.read_text(encoding="utf-8")


def test_list_stage_records_item_count_for_dashboard() -> None:
    class JobRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.job_update: dict[str, Any] = {}

        def create_job(self, book_id: int, stage: str, *, total_items: int) -> int:
            assert (book_id, stage, total_items) == (1, "split", 0)
            return 27

        def update_job(self, job_id: int, **values: Any) -> None:
            assert job_id == 27
            self.job_update = values

    repository = JobRepository()
    indexer = _indexer(repository, Settings(_env_file=None))
    result = indexer._run_job(1, "split", lambda: [{"id": 1}, {"id": 2}])
    assert len(result) == 2
    assert repository.job_update["status"] == "completed"
    assert repository.job_update["done_items"] == 2
    assert repository.job_update["total_items"] == 2


def test_split_stage_reports_window_progress() -> None:
    class JobRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.updates: list[dict[str, Any]] = []

        def create_job(self, *_args: Any, **_kwargs: Any) -> int:
            return 31

        def update_job(self, job_id: int, **values: Any) -> None:
            assert job_id == 31
            self.updates.append(values)

    repository = JobRepository()
    indexer = _indexer(repository, Settings(_env_file=None))

    def split_operation(report: Any) -> list[int]:
        report(0, 3)
        report(1, 3)
        report(3, 3)
        return [1, 2]

    result = indexer._run_job(
        1, "split", split_operation,
        progress_operation=True,
    )

    assert result == [1, 2]
    assert repository.updates[-1]["status"] == "completed"
    assert repository.updates[-1]["done_items"] == 3
    assert repository.updates[-1]["total_items"] == 3


def test_close_releases_owned_clients() -> None:
    repository = FakeRepository()
    indexer = _indexer(repository, Settings(_env_file=None))
    qdrant = indexer.qdrant
    llm = indexer.llm_client
    ollama = indexer.ollama_client

    indexer.close()

    assert qdrant.closed is True
    assert llm.closed is True
    assert ollama.closed is True


def test_failed_llm_split_does_not_replace_existing_chapters(tmp_path: Path) -> None:
    class TrackingRepository(FakeRepository):
        chapters_replaced = False

        def replace_chapters(self, *_args: Any, **_kwargs: Any) -> None:
            self.chapters_replaced = True

    class InvalidBoundaryClient:
        model = "invalid-boundary-model"

        def request_typed(self, **_kwargs: Any) -> SceneBoundaryDecision:
            raise RuntimeError("LLM unavailable")

    source = tmp_path / "book.txt"
    source.write_text("第一章\n\n第一段。\n\n第二段。", encoding="utf-8")
    repository = TrackingRepository()
    indexer = _indexer(repository, Settings(_env_file=None))
    indexer.llm_client = InvalidBoundaryClient()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="LLM unavailable"):
        indexer._split_and_store(1, 2, source)

    assert repository.chapters_replaced is False
