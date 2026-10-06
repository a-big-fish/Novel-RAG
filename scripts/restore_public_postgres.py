"""Restore the public PostgreSQL fixture into an empty, migrated database."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import psycopg

from app.config import get_settings


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def restore(fixture_dir: Path, connection: psycopg.Connection) -> None:
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "novel-rag-public-fixture-v1":
        raise ValueError("unsupported fixture format")
    source = fixture_dir / manifest["postgres"]["file"]
    if source.resolve().parent != fixture_dir.resolve():
        raise ValueError("PostgreSQL fixture must be inside fixture directory")
    if sha256(source) != manifest["postgres"]["sha256"]:
        raise ValueError("PostgreSQL fixture checksum mismatch")
    if connection.execute("SELECT count(*) FROM books").fetchone()[0] != 0:
        raise ValueError("target database is not empty")

    connection.execute(source.read_text(encoding="utf-8"), prepare=False)
    for table, expected in manifest["postgres"]["rows"].items():
        if table not in {"books", "chapters", "scenes", "tag_vocab", "token_map"}:
            raise ValueError(f"unexpected table in fixture manifest: {table}")
        actual = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if actual != expected:
            raise ValueError(f"{table}: expected {expected} rows, got {actual}")
    print("PostgreSQL fixture restored and row counts verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_dir", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        autocommit=True,
    ) as connection:
        restore(args.fixture_dir, connection)


if __name__ == "__main__":
    main()
