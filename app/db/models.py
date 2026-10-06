from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB


metadata = MetaData()
_id_type = BigInteger()
_identity = Identity(always=True)


books = Table(
    "books",
    metadata,
    Column("id", _id_type, _identity, primary_key=True),
    Column("title", Text, nullable=False),
    Column("author", Text, nullable=False, server_default=""),
    Column("source_path", Text, nullable=False),
    Column("source_format", Text, nullable=False),
    Column("source_sha256", Text, nullable=False, unique=True),
    Column("converted_path", Text),
    Column("converter_version", Text),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("current_version", Integer, nullable=False, server_default="0"),
    Column("total_chapters", Integer, nullable=False, server_default="0"),
    Column("total_scenes", Integer, nullable=False, server_default="0"),
    Column("selected_scenes", Integer, nullable=False, server_default="0"),
    Column("archived_scenes", Integer, nullable=False, server_default="0"),
    Column("discarded_scenes", Integer, nullable=False, server_default="0"),
    Column(
        "evaluation_failed_scenes",
        Integer,
        nullable=False,
        server_default="0",
    ),
    Column("error_message", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


chapters = Table(
    "chapters",
    metadata,
    Column("id", _id_type, _identity, primary_key=True),
    Column("book_id", BigInteger, nullable=False),
    Column("chapter_index", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("raw_text", Text, nullable=False),
    Column("char_count", Integer, nullable=False),
    Column("start_paragraph_index", Integer, nullable=False),
    Column("end_paragraph_index", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("book_id", "chapter_index", name="uq_chapters_book_chapter"),
)
Index("idx_chapters_book", chapters.c.book_id)


scenes = Table(
    "scenes",
    metadata,
    Column("id", _id_type, _identity, primary_key=True),
    Column("book_id", BigInteger, nullable=False),
    Column("scene_index_in_book", Integer, nullable=False),
    Column("chapter_start_index", Integer, nullable=False),
    Column("chapter_end_index", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("char_count", Integer, nullable=False),
    Column("split_reason", Text, nullable=False, server_default="rule"),
    Column("is_cross_chapter", Boolean, nullable=False, server_default="false"),
    Column("quality_screen_status", Text, nullable=False, server_default="not_run"),
    Column("quality_screen_reason", Text, nullable=False, server_default=""),
    Column("quality_screen_prompt_version", Text, nullable=False, server_default=""),
    Column("quality_screen_meta_json", JSONB, nullable=False, server_default="{}"),
    Column(
        "reference_status",
        Text,
        nullable=False,
        server_default="unevaluated",
    ),
    Column("reference_score", Float, nullable=False, server_default="0"),
    Column("reference_reason", Text, nullable=False, server_default=""),
    Column("reference_prompt_version", Text, nullable=False, server_default=""),
    Column("reference_rule_version", Text, nullable=False, server_default=""),
    Column("reference_meta_json", JSONB, nullable=False, server_default="{}"),
    Column("summary", Text, nullable=False, server_default=""),
    Column("style_summary", Text, nullable=False, server_default=""),
    Column("usage_hint", Text, nullable=False, server_default=""),
    Column("scene_type", ARRAY(Text), nullable=False, server_default="{}"),
    Column("scene_type_display", ARRAY(Text), nullable=False, server_default="{}"),
    Column("technique", ARRAY(Text), nullable=False, server_default="{}"),
    Column("technique_display", ARRAY(Text), nullable=False, server_default="{}"),
    Column("style_tags", ARRAY(Text), nullable=False, server_default="{}"),
    Column("style_tags_display", ARRAY(Text), nullable=False, server_default="{}"),
    Column("emotion_tags", ARRAY(Text), nullable=False, server_default="{}"),
    Column("emotion_tags_display", ARRAY(Text), nullable=False, server_default="{}"),
    Column("key_images", ARRAY(Text), nullable=False, server_default="{}"),
    Column("key_images_display", ARRAY(Text), nullable=False, server_default="{}"),
    Column("narrative_func", Text, nullable=False, server_default=""),
    Column("meta_json", JSONB, nullable=False, server_default="{}"),
    Column("annotate_status", Text, nullable=False, server_default="pending"),
    Column("index_status", Text, nullable=False, server_default="pending"),
    Column("error_message", Text),
    Column("version", Integer, nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "book_id",
        "scene_index_in_book",
        "version",
        name="uq_scenes_book_scene_version",
    ),
)
Index("idx_scenes_book", scenes.c.book_id)
Index("idx_scenes_active", scenes.c.book_id, scenes.c.version, scenes.c.is_active)
Index(
    "idx_scenes_reference_status",
    scenes.c.book_id,
    scenes.c.version,
    scenes.c.reference_status,
)
Index("idx_scenes_scene_type", scenes.c.scene_type, postgresql_using="gin")
Index("idx_scenes_technique", scenes.c.technique, postgresql_using="gin")
Index("idx_scenes_style_tags", scenes.c.style_tags, postgresql_using="gin")
Index("idx_scenes_emotion_tags", scenes.c.emotion_tags, postgresql_using="gin")


tag_vocab = Table(
    "tag_vocab",
    metadata,
    Column("id", _id_type, _identity, primary_key=True),
    Column("namespace", Text, nullable=False),
    Column("canonical_key", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "namespace",
        "display_name",
        "status",
        name="uq_tag_vocab_display",
    ),
)
Index(
    "idx_tag_vocab_canonical",
    tag_vocab.c.namespace,
    tag_vocab.c.canonical_key,
)


token_map = Table(
    "token_map",
    metadata,
    Column("book_id", BigInteger, nullable=False),
    Column("token_id", Integer, nullable=False),
    Column("token", Text, nullable=False),
    Column("doc_freq", Integer, nullable=False, server_default="0"),
    PrimaryKeyConstraint("book_id", "token_id", name="pk_token_map"),
    UniqueConstraint("book_id", "token", name="uq_token_map_book_token"),
)


annotation_cache = Table(
    "annotation_cache",
    metadata,
    Column("id", _id_type, _identity, primary_key=True),
    Column("input_hash", Text, nullable=False, unique=True),
    Column("model", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("tag_vocab_version", Text, nullable=False),
    Column("input_text", Text, nullable=False),
    Column("output_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


reference_evaluation_cache = Table(
    "reference_evaluation_cache",
    metadata,
    Column("id", _id_type, _identity, primary_key=True),
    Column("input_hash", Text, nullable=False, unique=True),
    Column("model", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("rule_version", Text, nullable=False),
    Column("input_text", Text, nullable=False),
    Column("output_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


scene_quality_cache = Table(
    "scene_quality_cache",
    metadata,
    Column("input_hash", Text, primary_key=True),
    Column("model", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("input_text", Text, nullable=False),
    Column("output_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


query_parsing_cache = Table(
    "query_parsing_cache",
    metadata,
    Column("input_hash", Text, primary_key=True),
    Column("model", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("tag_vocab_version", Text, nullable=False),
    Column("query_text", Text, nullable=False),
    Column("output_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


scene_split_cache = Table(
    "scene_split_cache",
    metadata,
    Column("input_hash", Text, primary_key=True),
    Column("model", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("input_text", Text, nullable=False),
    Column("output_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


embedding_cache = Table(
    "embedding_cache",
    metadata,
    Column("id", _id_type, _identity, primary_key=True),
    Column("input_hash", Text, nullable=False, unique=True),
    Column("model", Text, nullable=False),
    Column("input_text", Text, nullable=False),
    Column("vector", ARRAY(Float), nullable=False),
    Column("dim", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


index_jobs = Table(
    "index_jobs",
    metadata,
    Column("id", _id_type, _identity, primary_key=True),
    Column("book_id", BigInteger, nullable=False),
    Column("stage", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="running"),
    Column("total_items", Integer, nullable=False, server_default="0"),
    Column("done_items", Integer, nullable=False, server_default="0"),
    Column("error_message", Text),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
)
Index("idx_index_jobs_book", index_jobs.c.book_id)
