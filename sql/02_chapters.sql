-- PRD 4.3 chapters
CREATE TABLE chapters (
    id                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id                BIGINT NOT NULL,
    chapter_index          INTEGER NOT NULL,
    title                  TEXT    NOT NULL,
    raw_text               TEXT    NOT NULL,
    char_count             INTEGER NOT NULL,
    start_paragraph_index  INTEGER NOT NULL,
    end_paragraph_index    INTEGER NOT NULL,
    CONSTRAINT uq_chapters_book_chapter UNIQUE (book_id, chapter_index)
);

CREATE INDEX idx_chapters_book ON chapters (book_id);
