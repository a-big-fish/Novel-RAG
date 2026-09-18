from __future__ import annotations

import hashlib
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
    scenes,
    token_map,
)
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.indexer import Indexer
from app.services.schema import SceneAnnotation
from app.services.tagger import TagVocabulary
from tests.fixtures.build_synthetic_micro_novel import write_micro_novel

pytestmark = pytest.mark.integration


class FakeLLM:
    model = "fake-annotation-model"

    def request_typed(self, **kwargs: Any) -> SceneAnnotation:
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
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    database = PostgresDatabase.from_settings(test=True)
    repository = PostgresRepository(database)
    book_id: int | None = None
    settings = Settings(
        _env_file=None,
        embedding_dimension=4,
        data_converted_dir=tmp_path / "converted",
        llm_concurrency=1,
        max_llm_input_chars=3600,
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
        indexer = Indexer(
            repository=repository,
            qdrant=qdrant,
            llm_client=FakeLLM(),
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
        assert qdrant.count(result["collection"]) == result["scenes"]
        assert len(repository.list_jobs(book_id)) == 5
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
                    delete(embedding_cache).where(
                        embedding_cache.c.model == FakeOllama.model
                    )
                )
        database.dispose()
