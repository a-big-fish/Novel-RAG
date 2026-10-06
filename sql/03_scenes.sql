-- PRD 4.4 scenes
CREATE TABLE scenes (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id              BIGINT  NOT NULL,
    scene_index_in_book  INTEGER NOT NULL,
    chapter_start_index  INTEGER NOT NULL,
    chapter_end_index    INTEGER NOT NULL,
    text                 TEXT    NOT NULL,
    char_count           INTEGER NOT NULL,
    split_reason         TEXT    NOT NULL DEFAULT 'rule',
    is_cross_chapter     BOOLEAN NOT NULL DEFAULT FALSE,
    reference_status     TEXT    NOT NULL DEFAULT 'unevaluated'
                         CHECK (reference_status IN (
                             'unevaluated', 'evaluating', 'selected',
                             'archived', 'discarded', 'evaluation_failed'
                         )),
    reference_score      REAL    NOT NULL DEFAULT 0,
    reference_reason     TEXT    NOT NULL DEFAULT '',
    reference_prompt_version TEXT NOT NULL DEFAULT '',
    reference_rule_version TEXT  NOT NULL DEFAULT '',
    reference_meta_json  JSONB   NOT NULL DEFAULT '{}',
    summary              TEXT    NOT NULL DEFAULT '',
    style_summary        TEXT    NOT NULL DEFAULT '',
    usage_hint           TEXT    NOT NULL DEFAULT '',
    scene_type           TEXT[]  NOT NULL DEFAULT '{}',
    scene_type_display   TEXT[]  NOT NULL DEFAULT '{}',
    technique            TEXT[]  NOT NULL DEFAULT '{}',
    technique_display    TEXT[]  NOT NULL DEFAULT '{}',
    style_tags           TEXT[]  NOT NULL DEFAULT '{}',
    style_tags_display   TEXT[]  NOT NULL DEFAULT '{}',
    emotion_tags         TEXT[]  NOT NULL DEFAULT '{}',
    emotion_tags_display TEXT[]  NOT NULL DEFAULT '{}',
    key_images           TEXT[]  NOT NULL DEFAULT '{}',
    key_images_display   TEXT[]  NOT NULL DEFAULT '{}',
    narrative_func       TEXT    NOT NULL DEFAULT '',
    meta_json            JSONB   NOT NULL DEFAULT '{}',
    annotate_status      TEXT    NOT NULL DEFAULT 'pending'
                         CHECK (annotate_status IN (
                             'pending', 'running', 'annotated',
                             'failed_retryable', 'failed_permanent',
                             'not_applicable'
                         )),
    index_status         TEXT    NOT NULL DEFAULT 'pending'
                         CHECK (index_status IN (
                             'pending', 'indexed', 'failed', 'not_applicable'
                         )),
    error_message        TEXT,
    version              INTEGER NOT NULL,
    is_active            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_scenes_book_scene_version
        UNIQUE (book_id, scene_index_in_book, version)
);

CREATE INDEX idx_scenes_book ON scenes (book_id);
CREATE INDEX idx_scenes_active
    ON scenes (book_id, version, is_active);
CREATE INDEX idx_scenes_scene_type ON scenes USING GIN (scene_type);
CREATE INDEX idx_scenes_technique ON scenes USING GIN (technique);
CREATE INDEX idx_scenes_style_tags ON scenes USING GIN (style_tags);
CREATE INDEX idx_scenes_emotion_tags ON scenes USING GIN (emotion_tags);
CREATE INDEX idx_scenes_key_images ON scenes USING GIN (key_images);
CREATE INDEX idx_scenes_reference_status
    ON scenes (book_id, version, reference_status);
