CREATE TABLE IF NOT EXISTS query_parsing_cache (
    input_hash TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    tag_vocab_version TEXT NOT NULL,
    query_text TEXT NOT NULL,
    output_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
