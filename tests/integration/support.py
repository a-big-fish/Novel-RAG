from __future__ import annotations

from app.config import get_settings
from app.db.postgres import PostgresDatabase

TEST_DATABASE_NAME = "novel_rag_test"


def build_test_database() -> PostgresDatabase:
    """Connect only to the second-round test database.

    The original novel-rag-test database is intentionally never targeted by
    this test suite.
    """

    settings = get_settings().model_copy(
        update={"postgres_test_db": TEST_DATABASE_NAME}
    )
    return PostgresDatabase.from_settings(settings, test=True)
