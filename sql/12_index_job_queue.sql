-- PRD 4.10 index_jobs (queue extension; see migrations/007_index_job_queue.sql)
CREATE TABLE IF NOT EXISTS index_jobs (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id          BIGINT      NOT NULL,
    stage            TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'queued',
    total_items      INTEGER     NOT NULL DEFAULT 0,
    done_items       INTEGER     NOT NULL DEFAULT 0,
    error_message    TEXT,
    job_type         TEXT        NOT NULL DEFAULT 'stage',
    target_version   INTEGER,
    attempts         INTEGER     NOT NULL DEFAULT 0,
    max_attempts     INTEGER     NOT NULL DEFAULT 3,
    next_retry_at    TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at     TIMESTAMPTZ,
    worker_id        TEXT,
    checkpoint       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    canceled_at      TIMESTAMPTZ,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_index_jobs_book ON index_jobs (book_id);
CREATE INDEX IF NOT EXISTS idx_index_jobs_queue ON index_jobs (status, next_retry_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_index_jobs_active_book
    ON index_jobs (book_id)
    WHERE job_type = 'index' AND status IN ('queued', 'leased', 'running', 'retrying');
