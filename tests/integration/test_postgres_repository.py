from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import delete

from app.db.models import (
    annotation_cache,
    books,
    chapters,
    embedding_cache,
    index_jobs,
    scenes,
    tag_vocab,
    token_map,
)
from app.db.postgres import PostgresDatabase, PostgresRepository

pytestmark = pytest.mark.integration


def test_postgres_repository_round_trip() -> None:
    database = PostgresDatabase.from_settings(test=True)
    repository = PostgresRepository(database)
    marker = uuid.uuid4().hex
    source_sha = hashlib.sha256(marker.encode()).hexdigest()
    book_id: int | None = None

    try:
        book_id, created = repository.register_book(
            title="集成测试微型小说",
            author="test",
            source_path=f"E:/novels/tool/{marker}.txt",
            source_format="txt",
            source_sha256=source_sha,
        )
        assert created is True
        duplicate_id, created_again = repository.register_book(
            title="重复登记",
            author="test",
            source_path=f"E:/novels/tool/{marker}.txt",
            source_format="txt",
            source_sha256=source_sha,
        )
        assert duplicate_id == book_id
        assert created_again is False

        repository.replace_chapters(
            book_id,
            [
                {
                    "chapter_index": 1,
                    "title": "第一章",
                    "raw_text": "第一章\n\n测试正文。",
                    "char_count": 8,
                    "start_paragraph_index": 0,
                    "end_paragraph_index": 1,
                }
            ],
        )
        chapter = repository.get_chapter(book_id, 1)
        assert chapter is not None
        assert chapter["title"] == "第一章"

        scene_ids = repository.upsert_scenes(
            book_id,
            1,
            [
                {
                    "scene_index_in_book": 1,
                    "chapter_start_index": 1,
                    "chapter_end_index": 1,
                    "text": "测试正文。",
                    "char_count": 5,
                    "split_reason": "rule",
                    "is_cross_chapter": False,
                    "annotate_status": "pending",
                    "index_status": "pending",
                    "is_active": False,
                }
            ],
        )
        assert len(scene_ids) == 1

        # Re-running must update rather than duplicate the scene.
        repository.upsert_scenes(
            book_id,
            1,
            [
                {
                    "scene_index_in_book": 1,
                    "chapter_start_index": 1,
                    "chapter_end_index": 1,
                    "text": "更新后的正文。",
                    "char_count": 6,
                    "split_reason": "rule",
                    "is_cross_chapter": False,
                }
            ],
        )
        assert len(repository.list_scenes(book_id, 1)) == 1

        repository.update_scene(
            scene_ids[0],
            summary="摘要",
            annotate_status="annotated",
        )
        repository.activate_version(book_id, 1)
        assert repository.active_scene_count(book_id, 1) == 1
        book = repository.get_book(book_id)
        assert book is not None
        assert book["status"] == "ready"
        assert book["current_version"] == 1

        annotation_key = repository.annotation_cache_key(
            model="test-model",
            prompt_version="v1",
            tag_vocab_version="v1",
            input_text="测试正文。",
        )
        repository.put_annotation_cache(
            input_hash=annotation_key,
            model="test-model",
            prompt_version="v1",
            tag_vocab_version="v1",
            input_text="测试正文。",
            output_json={"summary": "摘要"},
        )
        assert repository.get_annotation_cache(annotation_key) is not None

        embedding_key = repository.embedding_cache_key(
            model="test-model",
            input_text="测试正文。",
        )
        repository.put_embedding_cache(
            input_hash=embedding_key,
            model="test-model",
            input_text="测试正文。",
            vector=[0.1, 0.2],
        )
        assert repository.get_embedding_cache(embedding_key) is not None

        repository.upsert_tag_vocab(
            [
                {
                    "namespace": "style",
                    "canonical_key": f"test_{marker[:8]}",
                    "display_name": f"测试标签{marker[:8]}",
                    "status": "active",
                }
            ]
        )
        assert any(
            row["canonical_key"] == f"test_{marker[:8]}"
            for row in repository.get_tag_vocab()
        )

        token_mapping = repository.sync_token_map(book_id, {"测试": 1, "正文": 1})
        assert set(token_mapping) == {"测试", "正文"}
        assert repository.get_token_doc_freq(book_id) == {"测试": 1, "正文": 1}

        job_id = repository.create_job(book_id, "sync", total_items=1)
        repository.update_job(
            job_id,
            status="completed",
            done_items=1,
            finished=True,
        )
        assert repository.list_jobs(book_id)[0]["status"] == "completed"
    finally:
        if book_id is not None:
            with database.engine.begin() as connection:
                for table in (
                    annotation_cache,
                    embedding_cache,
                    index_jobs,
                    token_map,
                    scenes,
                    chapters,
                    books,
                ):
                    if table is books:
                        connection.execute(delete(table).where(table.c.id == book_id))
                    elif table is annotation_cache or table is embedding_cache:
                        continue
                    else:
                        connection.execute(
                            delete(table).where(table.c.book_id == book_id)
                        )
                connection.execute(
                    delete(tag_vocab).where(
                        tag_vocab.c.canonical_key == f"test_{marker[:8]}"
                    )
                )
        database.dispose()
