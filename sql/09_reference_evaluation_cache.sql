-- PRD 4.7 reference_evaluation_cache
CREATE TABLE reference_evaluation_cache (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    input_hash     TEXT        NOT NULL UNIQUE,
    model          TEXT        NOT NULL,
    prompt_version TEXT        NOT NULL,
    rule_version   TEXT        NOT NULL,
    input_text     TEXT        NOT NULL,
    output_json    JSONB       NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
