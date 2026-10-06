-- PRD 4.2 books
CREATE TABLE books (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title            TEXT        NOT NULL,
    author           TEXT        NOT NULL DEFAULT '',
    source_path      TEXT        NOT NULL,
    source_format    TEXT        NOT NULL
                     CHECK (source_format IN ('epub', 'txt')),
    source_sha256    TEXT        NOT NULL UNIQUE,
    converted_path   TEXT,
    converter_version TEXT,
    status           TEXT        NOT NULL DEFAULT 'pending'
                     CHECK (status IN (
                         'pending', 'converting', 'splitting', 'screening', 'evaluating',
                         'annotating', 'indexing', 'ready', 'failed'
                     )),
    current_version  INTEGER     NOT NULL DEFAULT 0,
    total_chapters   INTEGER     NOT NULL DEFAULT 0,
    total_scenes     INTEGER     NOT NULL DEFAULT 0,
    selected_scenes  INTEGER     NOT NULL DEFAULT 0,
    archived_scenes  INTEGER     NOT NULL DEFAULT 0,
    discarded_scenes INTEGER     NOT NULL DEFAULT 0,
    evaluation_failed_scenes INTEGER NOT NULL DEFAULT 0,
    error_message    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
