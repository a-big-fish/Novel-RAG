-- PRD 4.6 token_map
CREATE TABLE token_map (
    book_id  BIGINT  NOT NULL,
    token_id INTEGER NOT NULL,
    token    TEXT    NOT NULL,
    doc_freq INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT pk_token_map PRIMARY KEY (book_id, token_id),
    CONSTRAINT uq_token_map_book_token UNIQUE (book_id, token)
);
