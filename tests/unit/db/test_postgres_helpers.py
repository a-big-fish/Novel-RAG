from __future__ import annotations

from app.db.postgres import PostgresRepository


def test_annotation_cache_key_is_deterministic() -> None:
    kwargs = {
        "model": "model-a",
        "prompt_version": "v1",
        "tag_vocab_version": "v1",
        "input_text": "text",
    }
    assert PostgresRepository.annotation_cache_key(**kwargs) == (
        PostgresRepository.annotation_cache_key(**kwargs)
    )


def test_embedding_cache_key_changes_with_model() -> None:
    first = PostgresRepository.embedding_cache_key(model="a", input_text="x")
    second = PostgresRepository.embedding_cache_key(model="b", input_text="x")
    assert first != second
