BEGIN;

-- tag_vocab supports multiple display names for one canonical key, so the
-- stable key cannot be the composite primary key.
ALTER TABLE tag_vocab DROP CONSTRAINT IF EXISTS pk_tag_vocab;

DO $$
DECLARE
    existing_primary_key TEXT;
BEGIN
    SELECT conname
    INTO existing_primary_key
    FROM pg_constraint
    WHERE conrelid = 'tag_vocab'::regclass
      AND contype = 'p'
      AND conname <> 'pk_tag_vocab_id';

    IF existing_primary_key IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE tag_vocab DROP CONSTRAINT %%I',
            existing_primary_key
        );
    END IF;
END
$$;

ALTER TABLE tag_vocab
    ADD COLUMN IF NOT EXISTS id BIGINT GENERATED ALWAYS AS IDENTITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'pk_tag_vocab_id'
          AND conrelid = 'tag_vocab'::regclass
    ) THEN
        ALTER TABLE tag_vocab
            ADD CONSTRAINT pk_tag_vocab_id PRIMARY KEY (id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_tag_vocab_canonical
    ON tag_vocab (namespace, canonical_key);

COMMIT;
