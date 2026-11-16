from __future__ import annotations

import pytest

from app.config import Settings
from app.services.writing_reference import WritingReferenceError, WritingReferenceService


class FakeRepository:
    def __init__(self):
        self.books = {
            10: {"id": 10, "title": "马之途", "status": "ready", "current_version": 3,
                 "total_scenes": 9, "selected_scenes": 8},
            55: {"id": 55, "title": "未完成", "status": "failed", "current_version": 0,
                 "total_scenes": 0, "selected_scenes": 0},
        }
        self.scenes = {
            1: {"id": 1, "book_id": 10, "version": 3, "scene_index_in_book": 4,
                "reference_status": "selected", "text": "原文" * 400,
                "summary": "对峙", "style_summary": "短句", "usage_hint": "参考节奏"},
        }

    def list_books(self):
        return list(self.books.values())

    def get_book(self, book_id):
        return self.books.get(book_id)

    def get_scene(self, scene_id):
        return self.scenes.get(scene_id)


class FakeSearcher:
    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "raw_intent": kwargs["query"],
            "parsed_query": {"fallback": False, "summary_query": "对峙"},
            "book_ids": kwargs["book_ids"],
            "books": {
                "10": {"status": "ok", "version": 3, "attempts": 1,
                       "latency_ms": 12.0, "degraded_routes": [],
                       "rrf": {"items": [{"scene_id": 1}]}},
            },
            "degraded_books": [],
            "aggregation": {"strategy": "round_robin_book_rrf", "items": []},
            "rerank": {"status": "disabled"},
            "final": {"source": "aggregation", "items": [
                {"scene_id": 1, "book_id": 10, "version": 3,
                 "book_rrf_rank": 1, "route_ranks": {"text_dense": 1},
                 "text_preview": "预览", "summary": "对峙",
                 "style_summary": "短句", "usage_hint": "提示"},
            ]},
            "latency_ms": 34.0,
        }


def test_service_plans_query_and_assembles_prompt():
    repository = FakeRepository()
    searcher = FakeSearcher()
    service = WritingReferenceService(repository, searcher, Settings())
    result = service.search(
        writing_goal="写一段持枪对峙",
        outline_context="主角被堵在店里",
        constraints={"情绪": "紧张"},
    )
    assert searcher.calls[0]["book_ids"] == [10]
    assert "写一段持枪对峙" in searcher.calls[0]["query"]
    assert result["query_plan"]["has_constraints"] is True
    assert result["prompt"]["text"]
    assert "情节上下文：主角被堵在店里" in result["prompt"]["text"]
    assert result["references"][0]["scene_id"] == 1
    assert result["fragments"] is None


def test_service_fragments_mode_skips_assembly():
    service = WritingReferenceService(FakeRepository(), FakeSearcher(), Settings())
    result = service.search(writing_goal="写一段对峙", output="fragments")
    assert result["prompt"] is None
    assert result["fragments"][0]["scene_id"] == 1


def test_service_rejects_when_no_ready_book():
    repository = FakeRepository()
    repository.books[10]["status"] = "failed"
    service = WritingReferenceService(repository, FakeSearcher(), Settings())
    with pytest.raises(WritingReferenceError):
        service.search(writing_goal="写一段对峙")
