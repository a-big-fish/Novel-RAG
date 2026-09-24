from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.services.indexer import Indexer
from app.utils.epub import sha256_file

TEST_DATABASE_NAME = "novel-rag-test-2"


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    source = project_root / "tests" / "马之途.txt"
    settings = get_settings().model_copy(
        update={"postgres_test_db": TEST_DATABASE_NAME}
    )
    database = PostgresDatabase.from_settings(settings, test=True)
    repository = PostgresRepository(database)
    indexer: Indexer | None = None
    try:
        book_id, created = repository.register_book(
            title="马之途",
            author="",
            source_path=str(source),
            source_format="txt",
            source_sha256=sha256_file(source),
        )
        indexer = Indexer.from_settings(repository, settings=settings)
        result = indexer.run(book_id)

        rows = [
            dict(row)
            for row in repository.list_scenes(book_id, result["version"])
        ]
        selected = [row for row in rows if row["reference_status"] == "selected"]
        archived = [row for row in rows if row["reference_status"] == "archived"]
        if len(rows) != result["scenes"]:
            raise AssertionError("PostgreSQL scene count does not match result")
        if len(selected) != result["selected_scenes"]:
            raise AssertionError("selected scene count does not match result")
        if len(archived) != result["archived_scenes"]:
            raise AssertionError("archived scene count does not match result")
        if any(row["annotate_status"] != "annotated" for row in selected):
            raise AssertionError("a selected scene was not deeply annotated")
        if any(row["index_status"] != "indexed" for row in selected):
            raise AssertionError("a selected scene was not indexed")
        if any(
            row["annotate_status"] != "not_applicable"
            or row["index_status"] != "not_applicable"
            for row in archived
        ):
            raise AssertionError("an archived scene entered the deep pipeline")

        report = {
            "database": TEST_DATABASE_NAME,
            "book_id": book_id,
            "book_created": created,
            "version": result["version"],
            "collection": result["collection"],
            "chapters": result["chapters"],
            "total_scenes": result["scenes"],
            "selected_scenes": result["selected_scenes"],
            "archived_scenes": result["archived_scenes"],
            "evaluation_failed_scenes": result["evaluation_failed_scenes"],
            "indexed_scenes": result["indexed_scenes"],
            "selection_rate": round(
                result["selected_scenes"] / result["scenes"],
                4,
            ),
            "selected": [
                {
                    "scene_index": row["scene_index_in_book"],
                    "chapter_start": row["chapter_start_index"],
                    "chapter_end": row["chapter_end_index"],
                    "score": row["reference_score"],
                    "reason": row["reference_reason"],
                }
                for row in selected
            ],
            "archived": [
                {
                    "scene_index": row["scene_index_in_book"],
                    "chapter_start": row["chapter_start_index"],
                    "chapter_end": row["chapter_end_index"],
                    "score": row["reference_score"],
                    "reason": row["reference_reason"],
                }
                for row in archived
            ],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if indexer is not None:
            indexer.close()
        database.dispose()


if __name__ == "__main__":
    main()
