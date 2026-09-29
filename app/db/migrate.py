from __future__ import annotations

import argparse
from pathlib import Path

from app.db.postgres import PostgresDatabase
from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply a novel-rag SQL migration")
    parser.add_argument(
        "migration",
        nargs="?",
        default="migrations/001_init.sql",
        help="path to the SQL migration",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="apply to POSTGRES_TEST_DB instead of the main database",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.test:
        # Keep migrations away from older test databases even when .env
        # overrides POSTGRES_TEST_DB.
        settings = settings.model_copy(
            update={"postgres_test_db": "novel-rag-test-2"}
        )
    database = PostgresDatabase.from_settings(settings, test=args.test)
    database.apply_migration(Path(args.migration))
    database.dispose()
    print(f"migration applied: {args.migration} (test={args.test})")


if __name__ == "__main__":
    main()
