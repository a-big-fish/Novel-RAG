BEGIN;

CREATE TABLE IF NOT EXISTS books (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL,
    source_format TEXT NOT NULL CHECK (source_format IN ('epub', 'txt')),
    source_sha256 TEXT NOT NULL UNIQUE,
    converted_path TEXT,
    converter_version TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    current_version INTEGER NOT NULL DEFAULT 0,
    total_chapters INTEGER NOT NULL DEFAULT 0,
    total_scenes INTEGER NOT NULL DEFAULT 0,
    selected_scenes INTEGER NOT NULL DEFAULT 0,
    archived_scenes INTEGER NOT NULL DEFAULT 0,
    evaluation_failed_scenes INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chapters (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id BIGINT NOT NULL,
    chapter_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    start_paragraph_index INTEGER NOT NULL,
    end_paragraph_index INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_chapters_book_chapter UNIQUE (book_id, chapter_index)
);
CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters (book_id);

CREATE TABLE IF NOT EXISTS scenes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id BIGINT NOT NULL,
    scene_index_in_book INTEGER NOT NULL,
    chapter_start_index INTEGER NOT NULL,
    chapter_end_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    split_reason TEXT NOT NULL DEFAULT 'rule',
    is_cross_chapter BOOLEAN NOT NULL DEFAULT false,
    reference_status TEXT NOT NULL DEFAULT 'unevaluated',
    reference_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    reference_reason TEXT NOT NULL DEFAULT '',
    reference_prompt_version TEXT NOT NULL DEFAULT '',
    reference_rule_version TEXT NOT NULL DEFAULT '',
    reference_meta_json JSONB NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    style_summary TEXT NOT NULL DEFAULT '',
    usage_hint TEXT NOT NULL DEFAULT '',
    scene_type TEXT[] NOT NULL DEFAULT '{}',
    scene_type_display TEXT[] NOT NULL DEFAULT '{}',
    technique TEXT[] NOT NULL DEFAULT '{}',
    technique_display TEXT[] NOT NULL DEFAULT '{}',
    style_tags TEXT[] NOT NULL DEFAULT '{}',
    style_tags_display TEXT[] NOT NULL DEFAULT '{}',
    emotion_tags TEXT[] NOT NULL DEFAULT '{}',
    emotion_tags_display TEXT[] NOT NULL DEFAULT '{}',
    key_images TEXT[] NOT NULL DEFAULT '{}',
    key_images_display TEXT[] NOT NULL DEFAULT '{}',
    narrative_func TEXT NOT NULL DEFAULT '',
    meta_json JSONB NOT NULL DEFAULT '{}',
    annotate_status TEXT NOT NULL DEFAULT 'pending',
    index_status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    version INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_scenes_book_scene_version
        UNIQUE (book_id, scene_index_in_book, version)
);
CREATE INDEX IF NOT EXISTS idx_scenes_book ON scenes (book_id);
CREATE INDEX IF NOT EXISTS idx_scenes_active
    ON scenes (book_id, version, is_active);
CREATE INDEX IF NOT EXISTS idx_scenes_reference_status
    ON scenes (book_id, version, reference_status);
CREATE INDEX IF NOT EXISTS idx_scenes_scene_type ON scenes USING gin (scene_type);
CREATE INDEX IF NOT EXISTS idx_scenes_technique ON scenes USING gin (technique);
CREATE INDEX IF NOT EXISTS idx_scenes_style_tags ON scenes USING gin (style_tags);
CREATE INDEX IF NOT EXISTS idx_scenes_emotion_tags ON scenes USING gin (emotion_tags);

CREATE TABLE IF NOT EXISTS tag_vocab (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    namespace TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_tag_vocab_id PRIMARY KEY (id),
    CONSTRAINT uq_tag_vocab_display UNIQUE (namespace, display_name, status)
);
CREATE INDEX IF NOT EXISTS idx_tag_vocab_canonical
    ON tag_vocab (namespace, canonical_key);

CREATE TABLE IF NOT EXISTS token_map (
    book_id BIGINT NOT NULL,
    token_id INTEGER NOT NULL,
    token TEXT NOT NULL,
    doc_freq INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT pk_token_map PRIMARY KEY (book_id, token_id),
    CONSTRAINT uq_token_map_book_token UNIQUE (book_id, token)
);

CREATE TABLE IF NOT EXISTS annotation_cache (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    input_hash TEXT NOT NULL UNIQUE,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    tag_vocab_version TEXT NOT NULL,
    input_text TEXT NOT NULL,
    output_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reference_evaluation_cache (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    input_hash TEXT NOT NULL UNIQUE,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    input_text TEXT NOT NULL,
    output_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    input_hash TEXT NOT NULL UNIQUE,
    model TEXT NOT NULL,
    input_text TEXT NOT NULL,
    vector DOUBLE PRECISION[] NOT NULL,
    dim INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS index_jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id BIGINT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    total_items INTEGER NOT NULL DEFAULT 0,
    done_items INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_index_jobs_book ON index_jobs (book_id);

COMMIT;
