from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import Settings, get_settings
from app.db.models import (
    annotation_cache,
    books,
    chapters,
    embedding_cache,
    index_jobs,
    query_parsing_cache,
    reference_evaluation_cache,
    scenes,
    tag_vocab,
    token_map,
)
from app.utils.errors import StorageError


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PostgresDatabase:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        test: bool = False,
    ) -> "PostgresDatabase":
        settings = settings or get_settings()
        engine = create_engine(
            settings.postgres_url(test=test),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        return cls(engine)

    def ping(self) -> None:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise StorageError(f"PostgreSQL unavailable: {exc}") from exc

    def apply_migration(self, migration_path: Path) -> None:
        sql = Path(migration_path).read_text(encoding="utf-8")
        try:
            with self.engine.begin() as connection:
                connection.exec_driver_sql(sql)
        except Exception as exc:
            raise StorageError(f"migration failed: {exc}") from exc

    def dispose(self) -> None:
        self.engine.dispose()


class PostgresRepository:
    """Repository implemented with SQLAlchemy Core.

    No ORM sessions or lazy relationships are used. Every public method is a
    complete transaction, which keeps retries and background workers simple.
    """

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database
        self.engine = database.engine

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------
    def register_book(
        self,
        *,
        title: str,
        author: str,
        source_path: str,
        source_format: str,
        source_sha256: str,
    ) -> tuple[int, bool]:
        statement = (
            pg_insert(books)
            .values(
                title=title,
                author=author,
                source_path=source_path,
                source_format=source_format,
                source_sha256=source_sha256,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=[books.c.source_sha256])
            .returning(books.c.id)
        )
        with self.engine.begin() as connection:
            inserted_id = connection.execute(statement).scalar_one_or_none()
            if inserted_id is not None:
                return int(inserted_id), True
            existing_id = connection.execute(
                select(books.c.id).where(books.c.source_sha256 == source_sha256)
            ).scalar_one()
            return int(existing_id), False

    def get_book(self, book_id: int) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(books).where(books.c.id == book_id)
            ).mappings().first()
        return row

    def list_books(self) -> list[Mapping[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(books).order_by(books.c.id.desc())
            ).mappings().all()
        return list(rows)

    def list_versions(self, book_id: int) -> list[dict[str, Any]]:
        statement = (
            select(
                scenes.c.version,
                func.count().label("total_scenes"),
                func.count().filter(scenes.c.reference_status == "selected").label("selected"),
                func.count().filter(scenes.c.reference_status == "archived").label("archived"),
                func.count().filter(scenes.c.reference_status == "evaluation_failed").label("evaluation_failed"),
            )
            .where(scenes.c.book_id == book_id)
            .group_by(scenes.c.version)
            .order_by(scenes.c.version.desc())
        )
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]

    def list_version_scenes_page(
        self, book_id: int, version: int, *, limit: int, offset: int,
        reference_status: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        predicate = [scenes.c.book_id == book_id, scenes.c.version == version]
        if reference_status:
            predicate.append(scenes.c.reference_status == reference_status)
        with self.engine.connect() as connection:
            total = int(connection.execute(
                select(func.count()).select_from(scenes).where(*predicate)
            ).scalar_one())
            rows = connection.execute(
                select(
                    scenes.c.id, scenes.c.scene_index_in_book,
                    scenes.c.chapter_start_index, scenes.c.chapter_end_index,
                    scenes.c.reference_status, scenes.c.reference_score,
                    scenes.c.reference_reason, scenes.c.summary,
                    scenes.c.style_summary, scenes.c.usage_hint,
                    scenes.c.scene_type, scenes.c.technique,
                    scenes.c.style_tags, scenes.c.emotion_tags,
                    scenes.c.key_images, scenes.c.char_count,
                    scenes.c.annotate_status, scenes.c.index_status,
                ).where(*predicate).order_by(scenes.c.scene_index_in_book)
                .limit(limit).offset(offset)
            ).mappings().all()
        return total, [dict(row) for row in rows]

    def list_version_scene_metrics(
        self, book_id: int, version: int,
    ) -> list[dict[str, Any]]:
        statement = select(
            scenes.c.reference_status, scenes.c.annotate_status,
            scenes.c.index_status, scenes.c.char_count,
            scenes.c.scene_type, scenes.c.technique,
            scenes.c.style_tags, scenes.c.emotion_tags,
            scenes.c.key_images,
        ).where(scenes.c.book_id == book_id, scenes.c.version == version)
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]

    def list_scene_fingerprints(
        self, book_id: int, version: int,
    ) -> list[dict[str, Any]]:
        statement = select(
            func.md5(scenes.c.text).label("text_hash"),
            scenes.c.reference_status,
        ).where(scenes.c.book_id == book_id, scenes.c.version == version)
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]

    def update_book(self, book_id: int, **values: Any) -> None:
        values["updated_at"] = _utc_now()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(books).where(books.c.id == book_id).values(**values)
            )
            if result.rowcount != 1:
                raise StorageError(f"book not found: {book_id}")

    def claim_book_for_index(
        self,
        book_id: int,
        *,
        initial_status: str = "converting",
    ) -> bool:
        if initial_status not in {"converting", "splitting"}:
            raise ValueError(f"invalid initial_status: {initial_status}")
        with self.engine.begin() as connection:
            result = connection.execute(
                update(books)
                .where(
                    books.c.id == book_id,
                    books.c.status.in_(("pending", "failed", "ready")),
                )
                .values(
                    status=initial_status,
                    error_message=None,
                    updated_at=_utc_now(),
                )
            )
        return result.rowcount == 1

    def replace_chapters(
        self,
        book_id: int,
        chapter_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(chapters).where(chapters.c.book_id == book_id)
            )
            if chapter_rows:
                connection.execute(
                    insert(chapters),
                    [dict(row) | {"book_id": book_id} for row in chapter_rows],
                )
            connection.execute(
                update(books)
                .where(books.c.id == book_id)
                .values(total_chapters=len(chapter_rows), updated_at=_utc_now())
            )

    def get_chapter(
        self,
        book_id: int,
        chapter_index: int,
    ) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(chapters).where(
                    chapters.c.book_id == book_id,
                    chapters.c.chapter_index == chapter_index,
                )
            ).mappings().first()
        return row

    # ------------------------------------------------------------------
    # Scenes
    # ------------------------------------------------------------------
    def upsert_scenes(
        self,
        book_id: int,
        version: int,
        rows: Sequence[Mapping[str, Any]],
    ) -> list[int]:
        if not rows:
            return []
        values = [dict(row) | {"book_id": book_id, "version": version} for row in rows]
        statement = pg_insert(scenes).values(values)
        statement = statement.on_conflict_do_update(
            constraint="uq_scenes_book_scene_version",
            set_={
                "chapter_start_index": statement.excluded.chapter_start_index,
                "chapter_end_index": statement.excluded.chapter_end_index,
                "text": statement.excluded.text,
                "char_count": statement.excluded.char_count,
                "split_reason": statement.excluded.split_reason,
                "is_cross_chapter": statement.excluded.is_cross_chapter,
                "reference_status": statement.excluded.reference_status,
                "reference_score": statement.excluded.reference_score,
                "reference_reason": statement.excluded.reference_reason,
                "reference_prompt_version": statement.excluded.reference_prompt_version,
                "reference_rule_version": statement.excluded.reference_rule_version,
                "reference_meta_json": statement.excluded.reference_meta_json,
                "summary": statement.excluded.summary,
                "style_summary": statement.excluded.style_summary,
                "usage_hint": statement.excluded.usage_hint,
                "scene_type": statement.excluded.scene_type,
                "scene_type_display": statement.excluded.scene_type_display,
                "technique": statement.excluded.technique,
                "technique_display": statement.excluded.technique_display,
                "style_tags": statement.excluded.style_tags,
                "style_tags_display": statement.excluded.style_tags_display,
                "emotion_tags": statement.excluded.emotion_tags,
                "emotion_tags_display": statement.excluded.emotion_tags_display,
                "key_images": statement.excluded.key_images,
                "key_images_display": statement.excluded.key_images_display,
                "narrative_func": statement.excluded.narrative_func,
                "meta_json": statement.excluded.meta_json,
                "annotate_status": statement.excluded.annotate_status,
                "index_status": statement.excluded.index_status,
                "error_message": statement.excluded.error_message,
                "is_active": statement.excluded.is_active,
                "updated_at": _utc_now(),
            },
        ).returning(scenes.c.id, scenes.c.scene_index_in_book)

        with self.engine.begin() as connection:
            returned = connection.execute(statement).all()
            connection.execute(
                update(books)
                .where(books.c.id == book_id)
                .values(total_scenes=len(rows), updated_at=_utc_now())
            )
        by_index = {int(index): int(scene_id) for scene_id, index in returned}
        return [
            by_index[int(row["scene_index_in_book"])]
            for row in rows
        ]

    def get_scene(self, scene_id: int) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(scenes).where(scenes.c.id == scene_id)
            ).mappings().first()
        return row

    def list_scenes(
        self,
        book_id: int,
        version: int,
        *,
        annotate_status: str | None = None,
        index_status: str | None = None,
        reference_status: str | None = None,
        active_only: bool = False,
    ) -> list[Mapping[str, Any]]:
        statement = select(scenes).where(
            scenes.c.book_id == book_id,
            scenes.c.version == version,
        )
        if annotate_status:
            statement = statement.where(scenes.c.annotate_status == annotate_status)
        if index_status:
            statement = statement.where(scenes.c.index_status == index_status)
        if reference_status:
            statement = statement.where(
                scenes.c.reference_status == reference_status
            )
        if active_only:
            statement = statement.where(scenes.c.is_active.is_(True))
        statement = statement.order_by(scenes.c.scene_index_in_book)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return list(rows)

    def update_scene(self, scene_id: int, **values: Any) -> None:
        values["updated_at"] = _utc_now()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(scenes).where(scenes.c.id == scene_id).values(**values)
            )
            if result.rowcount != 1:
                raise StorageError(f"scene not found: {scene_id}")

    def activate_version(self, book_id: int, version: int) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(scenes)
                .where(scenes.c.book_id == book_id)
                .values(is_active=False, updated_at=_utc_now())
            )
            result = connection.execute(
                update(scenes)
                .where(
                    scenes.c.book_id == book_id,
                    scenes.c.version == version,
                )
                .values(is_active=True, updated_at=_utc_now())
            )
            if result.rowcount == 0:
                raise StorageError(
                    f"cannot activate empty scene version: book={book_id}, version={version}"
                )
            connection.execute(
                update(books)
                .where(books.c.id == book_id)
                .values(
                    current_version=version,
                    status="ready",
                    error_message=None,
                    updated_at=_utc_now(),
                )
            )

    def active_scene_count(self, book_id: int, version: int) -> int:
        with self.engine.connect() as connection:
            count = connection.execute(
                select(func.count())
                .select_from(scenes)
                .where(
                    scenes.c.book_id == book_id,
                    scenes.c.version == version,
                    scenes.c.is_active.is_(True),
                )
            ).scalar_one()
        return int(count)

    # ------------------------------------------------------------------
    # Caches
    # ------------------------------------------------------------------
    @staticmethod
    def reference_evaluation_cache_key(
        *,
        model: str,
        prompt_version: str,
        rule_version: str,
        input_text: str,
    ) -> str:
        payload = "\x1f".join(
            (model, prompt_version, rule_version, input_text)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_reference_evaluation_cache(
        self,
        input_hash: str,
    ) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(reference_evaluation_cache).where(
                    reference_evaluation_cache.c.input_hash == input_hash
                )
            ).mappings().first()
        return row

    def put_reference_evaluation_cache(
        self,
        *,
        input_hash: str,
        model: str,
        prompt_version: str,
        rule_version: str,
        input_text: str,
        output_json: Mapping[str, Any],
    ) -> None:
        statement = (
            pg_insert(reference_evaluation_cache)
            .values(
                input_hash=input_hash,
                model=model,
                prompt_version=prompt_version,
                rule_version=rule_version,
                input_text=input_text,
                output_json=dict(output_json),
            )
            .on_conflict_do_nothing(
                index_elements=[reference_evaluation_cache.c.input_hash]
            )
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    @staticmethod
    def annotation_cache_key(
        *,
        model: str,
        prompt_version: str,
        tag_vocab_version: str,
        input_text: str,
    ) -> str:
        payload = "\x1f".join(
            (model, prompt_version, tag_vocab_version, input_text)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_annotation_cache(self, input_hash: str) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(annotation_cache).where(
                    annotation_cache.c.input_hash == input_hash
                )
            ).mappings().first()
        return row

    def put_annotation_cache(
        self,
        *,
        input_hash: str,
        model: str,
        prompt_version: str,
        tag_vocab_version: str,
        input_text: str,
        output_json: Mapping[str, Any],
    ) -> None:
        statement = (
            pg_insert(annotation_cache)
            .values(
                input_hash=input_hash,
                model=model,
                prompt_version=prompt_version,
                tag_vocab_version=tag_vocab_version,
                input_text=input_text,
                output_json=dict(output_json),
            )
            .on_conflict_do_nothing(index_elements=[annotation_cache.c.input_hash])
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    @staticmethod
    def embedding_cache_key(*, model: str, input_text: str) -> str:
        payload = f"{model}\x1f{input_text}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_embedding_cache(self, input_hash: str) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(embedding_cache).where(
                    embedding_cache.c.input_hash == input_hash
                )
            ).mappings().first()
        return row

    def put_embedding_cache(
        self,
        *,
        input_hash: str,
        model: str,
        input_text: str,
        vector: Sequence[float],
    ) -> None:
        statement = (
            pg_insert(embedding_cache)
            .values(
                input_hash=input_hash,
                model=model,
                input_text=input_text,
                vector=list(vector),
                dim=len(vector),
            )
            .on_conflict_do_nothing(index_elements=[embedding_cache.c.input_hash])
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    # ------------------------------------------------------------------
    # Tag vocabulary and sparse token map
    # ------------------------------------------------------------------
    def upsert_tag_vocab(
        self,
        rows: Iterable[Mapping[str, str]],
    ) -> int:
        values = [dict(row) for row in rows]
        if not values:
            return 0
        statement = pg_insert(tag_vocab).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[
                tag_vocab.c.namespace,
                tag_vocab.c.display_name,
                tag_vocab.c.status,
            ],
            set_={
                "canonical_key": statement.excluded.canonical_key,
                "updated_at": _utc_now(),
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)
        return len(values)

    def get_tag_vocab(self) -> list[Mapping[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(tag_vocab).where(tag_vocab.c.status == "active")
            ).mappings().all()
        return list(rows)

    def sync_token_map(
        self,
        book_id: int,
        token_doc_freq: Mapping[str, int],
    ) -> dict[str, int]:
        if not token_doc_freq:
            return {}
        with self.engine.begin() as connection:
            existing_rows = connection.execute(
                select(token_map.c.token, token_map.c.token_id).where(
                    token_map.c.book_id == book_id
                )
            ).all()
            mapping = {token: int(token_id) for token, token_id in existing_rows}
            next_id = max(mapping.values(), default=-1) + 1
            new_rows: list[dict[str, Any]] = []
            for token in sorted(set(token_doc_freq) - set(mapping)):
                mapping[token] = next_id
                new_rows.append(
                    {
                        "book_id": book_id,
                        "token_id": next_id,
                        "token": token,
                        "doc_freq": int(token_doc_freq[token]),
                    }
                )
                next_id += 1

            if new_rows:
                connection.execute(
                    pg_insert(token_map)
                    .values(new_rows)
                    .on_conflict_do_nothing()
                )

            updates = [
                {
                    "book_id": book_id,
                    "token_id": mapping[token],
                    "token": token,
                    "doc_freq": int(freq),
                }
                for token, freq in token_doc_freq.items()
            ]
            if updates:
                statement = pg_insert(token_map).values(updates)
                connection.execute(
                    statement.on_conflict_do_update(
                        constraint="pk_token_map",
                        set_={"doc_freq": statement.excluded.doc_freq},
                    )
                )
        return mapping

    def get_token_doc_freq(self, book_id: int) -> dict[str, int]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(token_map.c.token, token_map.c.doc_freq).where(
                    token_map.c.book_id == book_id
                )
            ).all()
        return {token: int(freq) for token, freq in rows}

    def get_token_map(self, book_id: int) -> dict[str, int]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(token_map.c.token, token_map.c.token_id).where(
                    token_map.c.book_id == book_id
                )
            ).all()
        return {str(token): int(token_id) for token, token_id in rows}

    def get_query_cache(self, input_hash: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(query_parsing_cache.c.output_json).where(
                    query_parsing_cache.c.input_hash == input_hash
                )
            ).scalar_one_or_none()
        return dict(row) if row is not None else None

    def put_query_cache(
        self, *, input_hash: str, model: str, prompt_version: str,
        tag_vocab_version: str, query_text: str, output_json: dict[str, Any],
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                pg_insert(query_parsing_cache).values(
                    input_hash=input_hash, model=model,
                    prompt_version=prompt_version,
                    tag_vocab_version=tag_vocab_version,
                    query_text=query_text, output_json=output_json,
                ).on_conflict_do_nothing(index_elements=[query_parsing_cache.c.input_hash])
            )

    # ------------------------------------------------------------------
    # Index jobs
    # ------------------------------------------------------------------
    def create_job(
        self,
        book_id: int,
        stage: str,
        *,
        total_items: int = 0,
    ) -> int:
        with self.engine.begin() as connection:
            return int(
                connection.execute(
                    insert(index_jobs)
                    .values(
                        book_id=book_id,
                        stage=stage,
                        status="running",
                        total_items=total_items,
                    )
                    .returning(index_jobs.c.id)
                ).scalar_one()
            )

    def update_job(
        self,
        job_id: int,
        *,
        status: str | None = None,
        total_items: int | None = None,
        done_items: int | None = None,
        error_message: str | None = None,
        finished: bool = False,
    ) -> None:
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status
        if total_items is not None:
            values["total_items"] = total_items
        if done_items is not None:
            values["done_items"] = done_items
        if error_message is not None:
            values["error_message"] = error_message
        if finished:
            values["finished_at"] = _utc_now()
        if not values:
            return
        with self.engine.begin() as connection:
            connection.execute(
                update(index_jobs).where(index_jobs.c.id == job_id).values(**values)
            )

    def list_jobs(self, book_id: int) -> list[Mapping[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(index_jobs)
                .where(index_jobs.c.book_id == book_id)
                .order_by(index_jobs.c.id.desc())
            ).mappings().all()
        return list(rows)

    @staticmethod
    def json_dumps(value: Mapping[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
