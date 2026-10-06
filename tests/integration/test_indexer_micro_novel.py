from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import delete

from app.config import Settings
from app.db.models import (
    annotation_cache,
    books,
    chapters,
    embedding_cache,
    index_jobs,
    reference_evaluation_cache,
    scenes,
    token_map,
)
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.indexer import Indexer
from app.services.llm_splitter import CrossChapterDecision, SceneBoundaryDecision
from app.services.schema import (
    ReferenceDimensions,
    ReferenceEvaluation,
    SceneAnnotation,
)
from app.services.tagger import TagVocabulary
from tests.fixtures.build_synthetic_micro_novel import write_micro_novel
from tests.integration.support import build_test_database

pytestmark = pytest.mark.integration


class FakeLLM:
    model = "fake-annotation-model"

    def __init__(self) -> None:
        self.archive_all = False

    def request_typed(self, **kwargs: Any) -> Any:
        if kwargs["response_model"] is SceneBoundaryDecision:
            return SceneBoundaryDecision(
                boundaries=[2] if "[2]" in kwargs["user_prompt"] else [],
            )
        if kwargs["response_model"] is CrossChapterDecision:
            return CrossChapterDecision(same_scene=False)
        if kwargs["response_model"] is ReferenceEvaluation:
            scene_text = kwargs["user_prompt"]
            selected = not self.archive_all and (
                "雨" in scene_text or "对峙" in scene_text
            )
            return ReferenceEvaluation(
                reference_status="selected" if selected else "archived",
                reference_score=4.5 if selected else 1.5,
                reference_reason=(
                    "对白与氛围控制具有可迁移参考价值。"
                    if selected
                    else "该场景主要承担转场与信息交代。"
                ),
                dimensions=ReferenceDimensions(
                    prose_quality=3.5,
                    technique_value=4.5 if selected else 1.0,
                    scene_completeness=4.0,
                    context_independence=3.5,
                    distinctiveness=4.0 if selected else 1.0,
                    reference_value=4.5 if selected else 1.0,
                ),
            )
        return SceneAnnotation(
            summary="两个人在雨夜交换线索，并决定继续追查。",
            style_summary="短句与停顿营造克制、紧绷的对峙感。",
            usage_hint="适合参考悬疑对话中的留白与信息递进。",
            scene_type=["对话冲突"],
            technique=["以行动代替说明", "钩子"],
            style_tags=["短句", "克制"],
            emotion_tags=["紧张"],
            key_images=["雨", "灯火"],
            narrative_func="推进剧情",
        )


class FakeOllama:
    model = "fake-embedding-model"

    def embed(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            result.append(
                [
                    digest[0] / 255,
                    digest[1] / 255,
                    digest[2] / 255,
                    digest[3] / 255,
                ]
            )
        return result


def test_indexer_micro_novel_end_to_end(tmp_path: Path) -> None:
    source = write_micro_novel(tmp_path / "micro-novel.txt")
    marker = uuid.uuid4().hex[:8]
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "第一章 雨夜来客",
            f"第一章 雨夜来客 {marker}",
            1,
        ),
        encoding="utf-8",
    )
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    database = build_test_database()
    repository = PostgresRepository(database)
    book_id: int | None = None
    settings = Settings(
        _env_file=None,
        embedding_dimension=4,
        data_converted_dir=tmp_path / "converted",
        llm_concurrency=1,
        max_llm_input_chars=3600,
        scene_min_chars=1,
        max_embed_input_chars=3600,
        tag_vocab_version="v1",
    )
    qdrant = QdrantAdapter(
        QdrantClient(location=":memory:"),
        settings=settings,
        dimension=4,
    )

    try:
        book_id, _ = repository.register_book(
            title="合成微型小说",
            author="fixture",
            source_path=str(source),
            source_format="txt",
            source_sha256=source_sha,
        )
        vocabulary_path = (
            Path(__file__).resolve().parents[2]
            / "app"
            / "data"
            / "tag_vocab"
            / "v1.json"
        )
        fake_llm = FakeLLM()
        indexer = Indexer(
            repository=repository,
            qdrant=qdrant,
            llm_client=fake_llm,
            ollama_client=FakeOllama(),
            vocabulary=TagVocabulary.from_json(vocabulary_path),
            settings=settings,
        )

        result = indexer.run(book_id)

        assert result["version"] == 1
        assert result["chapters"] >= 4
        assert result["scenes"] >= 4
        book = repository.get_book(book_id)
        assert book is not None
        assert book["status"] == "ready"
        assert book["current_version"] == 1
        assert repository.active_scene_count(book_id, 1) == result["scenes"]
        assert result["selected_scenes"] > 0
        assert result["archived_scenes"] > 0
        assert result["indexed_scenes"] == result["selected_scenes"]
        assert qdrant.count(result["collection"]) == result["selected_scenes"]
        jobs = repository.list_jobs(book_id)
        assert [job["stage"] for job in reversed(jobs)] == [
            "prepare_text", "split", "evaluate", "annotate", "embed", "store", "sync",
        ]
        assert all(job["status"] == "completed" for job in jobs)
        assert next(job for job in jobs if job["stage"] == "store")["done_items"] == result["indexed_scenes"]

        first_collection = result["collection"]
        first_selected_count = result["selected_scenes"]
        fake_llm.archive_all = True
        indexer.settings.reference_rule_version = "v2"

        reindexed = indexer.run(book_id)

        assert reindexed["version"] == 2
        assert reindexed["selected_scenes"] == 0
        assert reindexed["archived_scenes"] == reindexed["scenes"]
        assert qdrant.count(reindexed["collection"]) == 0
        assert qdrant.count(first_collection) == first_selected_count
        book = repository.get_book(book_id)
        assert book is not None
        assert book["current_version"] == 2
    finally:
        if book_id is not None:
            with database.engine.begin() as connection:
                for table in (
                    index_jobs,
                    token_map,
                    scenes,
                    chapters,
                ):
                    connection.execute(delete(table).where(table.c.book_id == book_id))
                connection.execute(delete(books).where(books.c.id == book_id))
                connection.execute(
                    delete(annotation_cache).where(
                        annotation_cache.c.model == FakeLLM.model
                    )
                )
                connection.execute(
                    delete(reference_evaluation_cache).where(
                        reference_evaluation_cache.c.model == FakeLLM.model
                    )
                )
                connection.execute(
                    delete(embedding_cache).where(
                        embedding_cache.c.model == FakeOllama.model
                    )
                )
        database.dispose()
