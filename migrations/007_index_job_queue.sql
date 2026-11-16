-- Durable index-job queue for the in-process worker.
-- No external middleware: leases, retries and checkpoints live in PostgreSQL.

ALTER TABLE index_jobs
    ADD COLUMN IF NOT EXISTS job_type TEXT NOT NULL DEFAULT 'stage',
    ADD COLUMN IF NOT EXISTS target_version INTEGER,
    ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3,
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS worker_id TEXT,
    ADD COLUMN IF NOT EXISTS checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS canceled_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_index_jobs_queue
    ON index_jobs (status, next_retry_at);

-- At most one active index request per book; stage-progress rows are excluded.
CREATE UNIQUE INDEX IF NOT EXISTS uq_index_jobs_active_book
    ON index_jobs (book_id)
    WHERE job_type = 'index' AND status IN ('queued', 'leased', 'running', 'retrying');
