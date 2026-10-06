"""Export explicitly approved books for the public, read-only demo fixture.

Run only after confirming redistribution rights for every selected book.
Caches, query history, jobs, local paths, and source files are intentionally omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db.postgres import PostgresDatabase
from app.db.qdrant import QdrantAdapter, scenes_collection_name


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite database number")
        return repr(value)
    if isinstance(value, (datetime, date)):
        return quoted(value.isoformat())
    if isinstance(value, dict):
        return quoted(json.dumps(value, ensure_ascii=False, sort_keys=True)) + "::jsonb"
    if isinstance(value, list):
        return "ARRAY[" + ", ".join(quoted(str(item)) for item in value) + "]::text[]"
    if isinstance(value, str):
        return quoted(value)
    raise TypeError(f"unsupported SQL value type: {type(value).__name__}")


def insert_row(table: str, row: dict[str, Any]) -> str:
    columns = ", ".join('"' + name + '"' for name in row)
    values = ", ".join(sql_value(value) for value in row.values())
    override = " OVERRIDING SYSTEM VALUE" if table in {
        "books", "chapters", "scenes", "tag_vocab"
    } else ""
    return f"INSERT INTO {table} ({columns}){override} VALUES ({values});\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_fixture(book_ids: list[int], database_name: str, output: Path) -> None:
    if not book_ids or len(book_ids) != len(set(book_ids)) or min(book_ids) <= 0:
        raise ValueError("provide distinct positive book IDs")
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")

    settings = get_settings().model_copy(update={"postgres_db": database_name})
    database = PostgresDatabase.from_settings(settings)
    qdrant = QdrantAdapter(settings=settings)
    output.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "format": "novel-rag-public-fixture-v1",
        "books": [],
        "excluded": [
            "annotation_cache", "reference_evaluation_cache", "embedding_cache",
            "query_parsing_cache", "index_jobs", "source_files",
        ],
    }
    try:
        with database.engine.connect() as connection:
            books = [dict(row) for row in connection.execute(
                text("SELECT * FROM books WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": book_ids},
            ).mappings()]
            if len(books) != len(book_ids) or any(
                book["status"] != "ready" or int(book["current_version"]) <= 0
                for book in books
            ):
                raise ValueError("all selected books must exist and be ready")

            versions = {int(book["id"]): int(book["current_version"]) for book in books}
            rows: dict[str, list[dict[str, Any]]] = {}
            for table in ("chapters", "scenes", "token_map"):
                rows[table] = [dict(row) for row in connection.execute(
                    text(f"SELECT * FROM {table} WHERE book_id = ANY(:ids)"),
                    {"ids": book_ids},
                ).mappings()]
            rows["scenes"] = [
                row for row in rows["scenes"]
                if int(row["version"]) == versions[int(row["book_id"])]
            ]
            rows["tag_vocab"] = [dict(row) for row in connection.execute(
                text("SELECT * FROM tag_vocab WHERE status = 'active' ORDER BY id")
            ).mappings()]

        for book in books:
            book_id = int(book["id"])
            book["source_path"] = f"/app/data/books/public-fixture-{book_id}"
            book["converted_path"] = None
            book["error_message"] = None

        sql_file = output / "postgres.sql"
        with sql_file.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("-- Public fixture: apply migrations 001-004 before loading.\nBEGIN;\n")
            for table, table_rows in (
                ("books", books), ("chapters", rows["chapters"]),
                ("scenes", rows["scenes"]), ("tag_vocab", rows["tag_vocab"]),
                ("token_map", rows["token_map"]),
            ):
                for row in table_rows:
                    handle.write(insert_row(table, row))
            for table in ("books", "chapters", "scenes", "tag_vocab"):
                handle.write(
                    "SELECT setval(pg_get_serial_sequence('" + table + "', 'id'), "
                    "GREATEST((SELECT COALESCE(MAX(id), 1) FROM " + table + "), 1));\n"
                )
            handle.write("COMMIT;\n")

        manifest["postgres"] = {
            "file": sql_file.name,
            "sha256": sha256(sql_file),
            "rows": {"books": len(books), **{key: len(value) for key, value in rows.items()}},
        }
        qdrant_dir = output / "qdrant"
        qdrant_dir.mkdir()
        for book in books:
            book_id = int(book["id"])
            version = versions[book_id]
            collection = scenes_collection_name(book_id, version)
            expected = sum(
                row["reference_status"] == "selected"
                for row in rows["scenes"] if int(row["book_id"]) == book_id
            )
            points_file = qdrant_dir / f"{collection}.jsonl"
            count = 0
            offset = None
            with points_file.open("w", encoding="utf-8", newline="\n") as handle:
                while True:
                    points, offset = qdrant.client.scroll(
                        collection_name=collection,
                        limit=64,
                        offset=offset,
                        with_payload=True,
                        with_vectors=True,
                    )
                    for point in points:
                        data = point.model_dump(mode="json", exclude_none=True)
                        handle.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
                        count += 1
                    if offset is None:
                        break
            if count != expected:
                raise ValueError(f"{collection}: expected {expected} points, got {count}")
            manifest["books"].append({
                "id": book_id,
                "title": book["title"],
                "version": version,
                "scenes": sum(int(row["book_id"]) == book_id for row in rows["scenes"]),
                "selected_points": count,
                "collection": collection,
                "file": "qdrant/" + points_file.name,
                "sha256": sha256(points_file),
            })
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        database.dispose()

    # Fail closed if unexpectedly sensitive configuration or machine paths leak.
    for path in output.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        if re.search(r"[A-Za-z]:[\\/]|/Users/|/home/|sk-[A-Za-z0-9_-]{16,}", content):
            raise ValueError(f"private path or key-shaped string in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-ids", type=int, nargs="+", required=True)
    parser.add_argument("--database", default="novel-rag-test-2")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-public-rights", action="store_true", required=True)
    args = parser.parse_args()
    export_fixture(args.book_ids, args.database, args.output)
    print(f"fixture exported: {args.output}")


if __name__ == "__main__":
    main()
