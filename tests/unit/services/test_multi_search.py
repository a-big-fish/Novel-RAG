import logging
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.multi_search import MultiBookSearcher, MultiBookSearchError
from app.services.query_parser import ParsedQuery
from app.services.retriever import Retriever
from app.utils.errors import StorageError


class FakeRepository:
    def get_book(self, book_id):
        return {"current_version": 2} if book_id in (1, 2) else None

    def list_versions(self, _book_id):
        return [{"version": 2}]

    def get_token_map(self, _book_id):
        return {"雨夜": 1}

    def get_scene(self, scene_id):
        book_id = scene_id // 100
        return {
            "book_id": book_id, "version": 2,
            "reference_status": "selected", "scene_index_in_book": 1,
            "chapter_start_index": 1, "chapter_end_index": 1,
            "text": f"书{book_id}的雨夜场景", "summary": "雨夜",
            "style_summary": "短句", "usage_hint": "节奏",
        }


class FakeParser:
    def __init__(self):
        self.calls = []

    def parse(self, query):
        self.calls.append(query)
        return ParsedQuery(raw_intent=query, summary_query="雨夜场景")


class FakeOllama:
    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[0.1, 0.2] for _ in texts]


class FakeQdrant:
    def __init__(self, fail_book=None):
        self.fail_book = fail_book
        self.calls = []

    def search(self, collection, *, vector_name, **_kwargs):
        book_id = int(collection.split("_")[2])
        self.calls.append((book_id, vector_name))
        if book_id == self.fail_book:
            raise StorageError("temporary Qdrant failure")
        scene_id = book_id * 100 + 1
        return [SimpleNamespace(id=scene_id, score=0.8, payload={
            "scene_id": scene_id, "book_id": book_id, "version": 2,
            "reference_status": "selected", "summary": "雨夜",
        })]


def make_searcher(*, fail_book=None, rerank_enabled=False, reranker=None):
    settings = Settings(
        multi_search_concurrency=2, rerank_enabled=rerank_enabled,
        rerank_max_document_chars=500,
    )
    repository = FakeRepository()
    parser = FakeParser()
    ollama = FakeOllama()
    qdrant = FakeQdrant(fail_book=fail_book)
    retriever = Retriever(repository, qdrant, ollama, parser, settings)
    return MultiBookSearcher(repository, retriever, settings, reranker), parser, ollama, qdrant


def test_multi_book_search_prepares_once_and_keeps_book_provenance(caplog):
    searcher, parser, ollama, qdrant = make_searcher()
    with caplog.at_level(logging.INFO, logger="app.services.multi_search"):
        result = searcher.search(
            query="雨夜", book_ids=[2, 1], route_top_n=5,
            per_book_limit=5, global_limit=5,
        )
    assert parser.calls == ["雨夜"]
    assert len(ollama.calls) == 1
    assert len(ollama.calls[0]) == 3
    assert len(qdrant.calls) == 8
    assert [(item["book_id"], item["version"], item["scene_id"])
            for item in result["aggregation"]["items"]] == [
                (2, 2, 201), (1, 2, 101),
            ]
    assert result["degraded_books"] == []
    assert {record.book_id for record in caplog.records
            if record.msg == "multi_book_result"} == {1, 2}


def test_multi_book_search_keeps_success_when_other_book_fails():
    searcher, parser, ollama, qdrant = make_searcher(fail_book=2)
    result = searcher.search(
        query="雨夜", book_ids=[1, 2], route_top_n=5,
        per_book_limit=5, global_limit=5,
    )
    assert result["degraded_books"] == [2]
    assert result["books"]["2"]["attempts"] == 2
    assert result["books"]["2"]["status"] == "failed"
    assert [item["scene_id"] for item in result["aggregation"]["items"]] == [101]
    assert len(parser.calls) == len(ollama.calls) == 1
    assert len([call for call in qdrant.calls if call[0] == 2]) == 8


def test_multi_book_search_rejects_all_unready_books_before_model_calls():
    searcher, parser, ollama, _qdrant = make_searcher()
    with pytest.raises(MultiBookSearchError):
        searcher.search(
            query="雨夜", book_ids=[3], route_top_n=5,
            per_book_limit=5, global_limit=5,
        )
    assert not parser.calls
    assert not ollama.calls


def test_enabled_rerank_reorders_global_candidates_only_after_aggregation():
    class FakeReranker:
        def __init__(self):
            self.calls = []

        def rerank(self, query, documents):
            self.calls.append((query, documents))
            return [{"index": 0, "score": 0.2}, {"index": 1, "score": 0.9}]

    reranker = FakeReranker()
    searcher, _parser, _ollama, _qdrant = make_searcher(
        rerank_enabled=True, reranker=reranker,
    )
    result = searcher.search(
        query="雨夜", book_ids=[1, 2], route_top_n=5,
        per_book_limit=5, global_limit=5,
    )
    assert [item["scene_id"] for item in result["aggregation"]["items"]] == [101, 201]
    assert [item["scene_id"] for item in result["final"]["items"]] == [201, 101]
    assert result["final"]["source"] == "rerank"
    assert result["rerank"]["status"] == "ok"
    assert len(reranker.calls) == 1
    assert reranker.calls[0][0] == "雨夜"
    assert all(len(document) <= 500 for document in reranker.calls[0][1])


def test_rerank_failure_falls_back_to_aggregation():
    class FailingReranker:
        def rerank(self, _query, _documents):
            raise StorageError("unavailable")

    searcher, _parser, _ollama, _qdrant = make_searcher(
        rerank_enabled=True, reranker=FailingReranker(),
    )
    result = searcher.search(
        query="雨夜", book_ids=[1, 2], route_top_n=5,
        per_book_limit=5, global_limit=5,
    )
    assert result["rerank"]["status"] == "failed"
    assert result["final"]["source"] == "aggregation"
    assert [item["scene_id"] for item in result["final"]["items"]] == [101, 201]
