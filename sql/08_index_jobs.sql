-- PRD 4.10 index_jobs (optional)
CREATE TABLE index_jobs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id       BIGINT      NOT NULL,
    stage         TEXT        NOT NULL
                  CHECK (stage IN (
                      'convert', 'prepare_text',
                      'split', 'annotate', 'embed', 'sync'
                  )),
    status        TEXT        NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'completed', 'failed')),
    total_items   INTEGER     NOT NULL DEFAULT 0,
    done_items    INTEGER     NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

CREATE INDEX idx_index_jobs_book ON index_jobs (book_id);
