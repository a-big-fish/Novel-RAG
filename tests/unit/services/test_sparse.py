from __future__ import annotations

from app.services.sparse import (
    build_sparse_vector,
    collect_doc_frequencies,
    tokenize,
)


def test_tokenize_filters_stopwords_and_punctuation() -> None:
    tokens = tokenize("雨夜，他的刀很快。")
    assert "雨" in tokens or "雨夜" in tokens
    assert "的" not in tokens
    assert "，" not in tokens


def test_sparse_vector_and_doc_frequencies() -> None:
    texts = ["雨夜刀光", "雨夜灯火"]
    frequencies = collect_doc_frequencies(texts)
    mapping = {token: index for index, token in enumerate(sorted(frequencies))}
    vector = build_sparse_vector(texts[0], mapping)
    assert vector["indices"]
    assert len(vector["indices"]) == len(vector["values"])
    assert all(value >= 1.0 for value in vector["values"])
