BEGIN;

ALTER TABLE books
    ADD COLUMN IF NOT EXISTS selected_scenes INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS archived_scenes INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS evaluation_failed_scenes INTEGER NOT NULL DEFAULT 0;

ALTER TABLE scenes
    ADD COLUMN IF NOT EXISTS reference_status TEXT NOT NULL DEFAULT 'unevaluated',
    ADD COLUMN IF NOT EXISTS reference_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reference_reason TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS reference_prompt_version TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS reference_rule_version TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS reference_meta_json JSONB NOT NULL DEFAULT '{}';

-- Existing indexed/annotated rows predate the reference gate. Treat them as
-- legacy selected references so active collections remain semantically valid.
UPDATE scenes
SET reference_status = 'selected',
    reference_reason = CASE
        WHEN reference_reason = ''
        THEN 'legacy scene indexed before reference evaluation was introduced'
        ELSE reference_reason
    END,
    reference_rule_version = CASE
        WHEN reference_rule_version = '' THEN 'legacy'
        ELSE reference_rule_version
    END
WHERE reference_status = 'unevaluated'
  AND (annotate_status = 'annotated' OR index_status = 'indexed');

CREATE INDEX IF NOT EXISTS idx_scenes_reference_status
    ON scenes (book_id, version, reference_status);

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

UPDATE books AS b
SET selected_scenes = counts.selected,
    archived_scenes = counts.archived,
    evaluation_failed_scenes = counts.failed
FROM (
    SELECT
        s.book_id,
        count(*) FILTER (WHERE s.reference_status = 'selected')::INTEGER AS selected,
        count(*) FILTER (WHERE s.reference_status = 'archived')::INTEGER AS archived,
        count(*) FILTER (
            WHERE s.reference_status = 'evaluation_failed'
        )::INTEGER AS failed
    FROM scenes AS s
    JOIN books AS current_book
      ON current_book.id = s.book_id
     AND current_book.current_version = s.version
    GROUP BY s.book_id
) AS counts
WHERE b.id = counts.book_id;

COMMIT;
