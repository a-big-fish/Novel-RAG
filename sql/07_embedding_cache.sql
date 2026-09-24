-- PRD 4.8 embedding_cache
CREATE TABLE embedding_cache (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    input_hash  TEXT        NOT NULL UNIQUE,
    model       TEXT        NOT NULL,
    input_text  TEXT        NOT NULL,
    vector      REAL[]      NOT NULL,
    dim         INTEGER     NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
