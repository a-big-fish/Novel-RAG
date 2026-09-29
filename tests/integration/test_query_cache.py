from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from app.db.models import query_parsing_cache
from app.db.postgres import PostgresRepository
from tests.integration.support import build_test_database

pytestmark = pytest.mark.integration


def test_query_cache_survives_repository_recreation():
    database = build_test_database()
    key = uuid.uuid4().hex
    try:
        first = PostgresRepository(database)
        first.put_query_cache(
            input_hash=key, model="fake", prompt_version="v1",
            tag_vocab_version="v1", query_text="雨夜追逐",
            output_json={"raw_intent": "雨夜追逐", "summary_query": "追逐"},
        )
        second = PostgresRepository(database)
        assert second.get_query_cache(key)["summary_query"] == "追逐"
        second.put_query_cache(
            input_hash=key, model="fake", prompt_version="v1",
            tag_vocab_version="v1", query_text="雨夜追逐",
            output_json={"raw_intent": "雨夜追逐", "summary_query": "冲突"},
        )
        assert second.get_query_cache(key)["summary_query"] == "追逐"
    finally:
        with database.engine.begin() as connection:
            connection.execute(delete(query_parsing_cache).where(query_parsing_cache.c.input_hash == key))
        database.dispose()
