from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.query_parser import ParsedQuery
from app.services.retriever import Retriever, RetrievalError


class FakeRepository:
    def get_token_map(self, _book_id):
        return {"雨夜": 1}

    def get_scene(self, scene_id):
        return {
            "id": scene_id, "book_id": 7, "version": 2,
            "reference_status": "selected", "scene_index_in_book": 1,
            "chapter_start_index": 1, "chapter_end_index": 1,
            "text": "甲" * 500, "summary": "追逐", "style_summary": "短句",
            "usage_hint": "参考节奏",
        }


class FakeParser:
    def parse(self, query):
        return ParsedQuery(raw_intent=query)


class FakeOllama:
    def embed(self, texts):
        return [[0.1, 0.2] for _ in texts]


class FakeQdrant:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def search(self, _, *, vector_name, **kwargs):
        self.calls.append(vector_name)
        if vector_name == self.fail:
            from app.utils.errors import StorageError
            raise StorageError("unavailable")
        return [SimpleNamespace(id=10, score=0.8, payload={
            "scene_id": 10, "book_id": 7, "version": 2,
            "reference_status": "selected", "summary": "追逐",
        })]


def test_retriever_keeps_four_routes_and_only_scene_preview():
    qdrant = FakeQdrant()
    result = Retriever(FakeRepository(), qdrant, FakeOllama(), FakeParser(), Settings()).search(
        book_id=7, version=2, query="雨夜", route_top_n=5, rrf_top_n=5,
    )
    assert len(qdrant.calls) == 4
    assert result["routes"]["text_sparse"]["status"] == "ok"
    assert result["rrf"]["items"][0]["scene_id"] == 10
    assert len(result["rrf"]["items"][0]["text_preview"]) == 240
    assert "text_full" not in result["rrf"]["items"][0]


def test_empty_sparse_query_is_skipped_without_qdrant_request():
    qdrant = FakeQdrant()
    result = Retriever(FakeRepository(), qdrant, FakeOllama(), FakeParser(), Settings()).search(
        book_id=7, version=2, query="不存在的未知词", route_top_n=5, rrf_top_n=5,
    )
    assert len(qdrant.calls) == 3
    assert result["routes"]["text_sparse"]["status"] == "skipped"
    assert result["routes"]["text_sparse"]["reason"] == "no_known_tokens"


def test_one_route_failure_keeps_other_routes():
    result = Retriever(FakeRepository(), FakeQdrant("meta-dense"), FakeOllama(), FakeParser(), Settings()).search(
        book_id=7, version=2, query="雨夜", route_top_n=5, rrf_top_n=5,
    )
    assert result["degraded_routes"] == ["meta_dense"]
    assert result["rrf"]["items"]


def test_out_of_scope_hit_fails_closed():
    class WrongQdrant(FakeQdrant):
        def search(self, *args, **kwargs):
            point = super().search(*args, **kwargs)[0]
            point.payload["book_id"] = 8
            return [point]

    with pytest.raises(RetrievalError):
        Retriever(FakeRepository(), WrongQdrant(), FakeOllama(), FakeParser(), Settings()).search(
            book_id=7, version=2, query="雨夜", route_top_n=5, rrf_top_n=5,
        )
