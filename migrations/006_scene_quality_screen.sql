BEGIN;

ALTER TABLE scenes
    ADD COLUMN IF NOT EXISTS quality_screen_status TEXT NOT NULL DEFAULT 'not_run',
    ADD COLUMN IF NOT EXISTS quality_screen_reason TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS quality_screen_prompt_version TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS quality_screen_meta_json JSONB NOT NULL DEFAULT '{}';

ALTER TABLE books
    ADD COLUMN IF NOT EXISTS discarded_scenes INTEGER NOT NULL DEFAULT 0;

ALTER TABLE books DROP CONSTRAINT IF EXISTS books_status_check;
ALTER TABLE books ADD CONSTRAINT books_status_check
    CHECK (status IN (
        'pending', 'converting', 'splitting', 'screening',
        'evaluating', 'annotating', 'indexing', 'ready', 'failed'
    ));

ALTER TABLE scenes DROP CONSTRAINT IF EXISTS scenes_reference_status_check;
ALTER TABLE scenes ADD CONSTRAINT scenes_reference_status_check
    CHECK (reference_status IN (
        'unevaluated', 'evaluating', 'selected', 'archived',
        'discarded', 'evaluation_failed'
    ));

CREATE TABLE IF NOT EXISTS scene_quality_cache (
    input_hash TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_text TEXT NOT NULL,
    output_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
