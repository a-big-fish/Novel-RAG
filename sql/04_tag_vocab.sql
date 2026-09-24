-- PRD 4.5 tag_vocab
-- NOTE: PRD lists PRIMARY KEY (namespace, canonical_key), but its own example
-- maps multiple display names to one canonical_key (e.g. style/short_sentence
-- -> 短句 / 冷硬短句). A composite key on (namespace, canonical_key) would
-- reject that data, so the primary key falls back to a surrogate id while the
-- PRD's intended uniqueness rule is kept on (namespace, display_name, status).
CREATE TABLE tag_vocab (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace     TEXT        NOT NULL,
    canonical_key TEXT        NOT NULL,
    display_name  TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'deprecated')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_tag_vocab_display UNIQUE (namespace, display_name, status)
);

CREATE INDEX idx_tag_vocab_canonical
    ON tag_vocab (namespace, canonical_key);
