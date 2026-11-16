from __future__ import annotations

from app.config import Settings
from app.services.assembler import AssemblyOptions, assemble_search_result


class FakeRepository:
    def __init__(self, scenes: dict[int, dict], books: dict[int, dict] | None = None):
        self.scenes = scenes
        self.books = books or {}

    def get_scene(self, scene_id: int):
        return self.scenes.get(scene_id)

    def get_book(self, book_id: int):
        return self.books.get(book_id, {"id": book_id, "title": f"书籍 {book_id}"})


def scene(scene_id: int, book_id: int, index: int, version: int = 1,
          text: str | None = None) -> dict:
    return {
        "id": scene_id, "book_id": book_id, "version": version,
        "scene_index_in_book": index, "reference_status": "selected",
        "text": text or (f"原文内容{scene_id}" * 200), "summary": f"摘要 {scene_id}",
        "style_summary": "短句、克制", "usage_hint": "参考节奏",
    }


def result_with(*items: dict) -> dict:
    return {"final": {"source": "aggregation", "items": list(items)}}


def item(scene_id: int, book_id: int, version: int = 1, rank: int = 1) -> dict:
    return {
        "scene_id": scene_id, "book_id": book_id, "version": version,
        "book_rrf_rank": rank, "route_ranks": {"text_dense": rank},
        "text_preview": "预览", "summary": "摘要", "style_summary": "风格",
        "usage_hint": "提示",
    }


def test_assembly_renders_prompt_with_references_and_budget():
    repository = FakeRepository({1: scene(1, 10, 4), 2: scene(2, 43, 6)})
    assembly = assemble_search_result(
        result_with(item(1, 10), item(2, 43)), repository, Settings(),
        task="写一段雨夜追杀",
        options=AssemblyOptions(budget_chars=6000, max_scenes=4, per_scene_chars=400),
    )
    assert assembly["status"] == "ok"
    assert len(assembly["references"]) == 2
    assert assembly["prompt"]["char_count"] <= 6000
    assert "写一段雨夜追杀" in assembly["prompt"]["text"]
    assert "参考场景 1" in assembly["prompt"]["text"]


def test_assembly_deduplicates_repeated_scenes():
    repository = FakeRepository({1: scene(1, 10, 4)})
    assembly = assemble_search_result(
        result_with(item(1, 10), item(1, 10, rank=2)), repository, Settings(),
        task="写一段雨夜追杀",
        options=AssemblyOptions(budget_chars=6000, max_scenes=4, per_scene_chars=400),
    )
    assert len(assembly["references"]) == 1


def test_assembly_respects_book_quota_before_filling():
    repository = FakeRepository({
        1: scene(1, 10, 1), 2: scene(2, 10, 2),
        3: scene(3, 43, 1), 4: scene(4, 43, 2),
    })
    assembly = assemble_search_result(
        result_with(item(1, 10, rank=1), item(2, 10, rank=2),
                    item(3, 43, rank=1), item(4, 43, rank=2)),
        repository, Settings(),
        task="写一段对峙",
        options=AssemblyOptions(
            budget_chars=6000, max_scenes=2, per_scene_chars=400, book_quota=1,
        ),
    )
    books = {reference["book_id"] for reference in assembly["references"]}
    assert books == {10, 43}


def test_assembly_reports_budget_drops_and_never_exceeds_budget():
    repository = FakeRepository({
        1: scene(1, 10, 1, text="甲" * 3000),
        2: scene(2, 43, 1, text="乙" * 3000),
    })
    options = AssemblyOptions(budget_chars=900, max_scenes=4, per_scene_chars=2000)
    assembly = assemble_search_result(
        result_with(item(1, 10), item(2, 43)), repository, Settings(),
        task="写一段紧张场景", options=options,
    )
    assert len(assembly["prompt"]["text"]) <= options.budget_chars
    assert assembly["prompt"]["budget"]["dropped"] >= 1
    assert assembly["dropped"]


def test_assembly_without_candidates_returns_empty_prompt():
    repository = FakeRepository({})
    assembly = assemble_search_result(
        result_with(), repository, Settings(), task="写一段对话",
        options=AssemblyOptions(budget_chars=2000, max_scenes=4, per_scene_chars=400),
    )
    assert assembly["status"] == "empty"
    assert assembly["references"] == []
    assert "没有检索到" in assembly["prompt"]["text"]
