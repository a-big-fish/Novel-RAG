from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest

from app.config import Settings
from app.services.scene_quality import (
    SceneQualityDecision,
    SceneQualityScreener,
    duplicate_evidence,
)
from app.utils.errors import NovelRagError


REPEATED = [
    "屋檐下的雨丝落在青石板上，两人站在门外始终没有开口。",
    "旧信封被塞进衣袖，信纸上的墨迹早已被雨水泡得模糊。",
    "远处的钟声响起，他抬头看向巷尾依旧没有点亮的灯。",
]


def _rows() -> list[dict[str, Any]]:
    return [
        {"id": index, "scene_index_in_book": index,
         "text": "\n\n".join(REPEATED + [f"他第{index}次把钥匙收回口袋，仍旧没有决定是否离开。"])}
        for index in (1, 2, 3)
    ] + [{
        "id": 4, "scene_index_in_book": 4,
        "text": "她打开日记，发现最后一页写着不同的地名和日期。\n\n"
                "当天夜里，她乘火车离城，决定独自查清这条线索。",
    }]


class FakeRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.updates: dict[int, dict[str, Any]] = {}
        self.cache: dict[str, dict[str, Any]] = {}

    def list_scenes(self, *_args: Any) -> list[dict[str, Any]]:
        return self.rows

    def update_scene(self, scene_id: int, **values: Any) -> None:
        self.updates.setdefault(scene_id, {}).update(values)

    def scene_quality_cache_key(self, **values: Any) -> str:
        return hashlib.sha256(values["input_text"].encode()).hexdigest()

    def get_scene_quality_cache(self, input_hash: str) -> dict[str, Any] | None:
        return self.cache.get(input_hash)

    def put_scene_quality_cache(self, **values: Any) -> None:
        self.cache[values["input_hash"]] = {"output_json": values["output_json"]}


class FakeLLM:
    model = "quality-test-model"

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def request_typed(self, **values: Any) -> SceneQualityDecision:
        self.calls += 1
        if self.fail:
            raise RuntimeError("LLM unavailable")
        prompt = values["user_prompt"]
        ratio = float(re.search(r"逐段复用的正文比例：([0-9.]+)", prompt).group(1))
        if ratio > 0.7:
            return SceneQualityDecision(
                decision="reject", category="duplicate",
                reason="多段正文在同书多处原样复用，仅改变一次动作。",
            )
        return SceneQualityDecision(
            decision="keep", category="none",
            reason="场景内容具体，有独立行动和信息推进。",
        )


def test_duplicate_evidence_finds_template_reuse_across_whole_book() -> None:
    evidence = duplicate_evidence(_rows())
    assert evidence[1]["repeated_ratio"] > 0.7
    assert evidence[1]["repeated_scene_count"] == 2
    assert evidence[1]["overlap_ratio"] > 0.7
    assert evidence[4]["repeated_ratio"] == 0
    assert evidence[4]["similar_scene_index"] is None


def test_duplicate_evidence_finds_near_copy_with_small_action_changes() -> None:
    first = "她在雨夜沿着旧街走到钟楼，发现门边留着一封没有署名的信。"
    second = "她在雨夜沿着旧街跑到钟楼，发现门边留着一封没有署名的信。"
    rows = [
        {"id": 1, "scene_index_in_book": 1, "text": first},
        {"id": 2, "scene_index_in_book": 2, "text": second},
    ]
    evidence = duplicate_evidence(rows)
    assert evidence[1]["repeated_scene_count"] == 0
    assert evidence[1]["similar_scene_index"] == 2
    assert evidence[1]["similarity_method"] == "character_shingle"
    assert evidence[1]["overlap_ratio"] >= 0.5


def test_quality_screen_discards_repeats_but_keeps_original_text() -> None:
    repository = FakeRepository(_rows())
    llm = FakeLLM()
    screener = SceneQualityScreener(
        repository=repository, llm_client=llm,
        settings=Settings(_env_file=None, llm_concurrency=1),
    )
    stats = screener.screen_book(1, 1)
    assert stats == {"total": 4, "kept": 1, "rejected": 3, "failed": 0}
    assert repository.updates[1]["reference_status"] == "discarded"
    assert repository.updates[1]["annotate_status"] == "not_applicable"
    assert repository.updates[1]["index_status"] == "not_applicable"
    assert repository.updates[4]["quality_screen_status"] == "kept"
    assert "reference_status" not in repository.updates[4]
    assert len(repository.rows) == 4
    assert llm.calls == 4

    assert screener.screen_book(1, 1) == stats
    assert llm.calls == 4  # persistent decisions reused on retry


def test_quality_screen_failure_is_explicit() -> None:
    repository = FakeRepository(_rows()[:1])
    screener = SceneQualityScreener(
        repository=repository, llm_client=FakeLLM(fail=True),
        settings=Settings(_env_file=None, llm_concurrency=1),
    )
    with pytest.raises(NovelRagError, match="failed for 1 scene"):
        screener.screen_book(1, 1)
    assert repository.updates[1]["quality_screen_status"] == "failed"
    assert "reference_status" not in repository.updates[1]


def test_extreme_bookwide_repetition_overrides_llm_keep() -> None:
    repeated_rows = [
        {"id": index, "scene_index_in_book": index,
         "text": "\n\n".join(REPEATED + [f"第{index}次退后。"])}
        for index in range(1, 5)
    ]
    repository = FakeRepository(repeated_rows)

    class AlwaysKeepLLM(FakeLLM):
        def request_typed(self, **_values: Any) -> SceneQualityDecision:
            self.calls += 1
            return SceneQualityDecision(
                decision="keep", category="none",
                reason="模型误以为这段文字具有独立内容。",
            )

    screener = SceneQualityScreener(
        repository=repository, llm_client=AlwaysKeepLLM(),
        settings=Settings(_env_file=None, llm_concurrency=1),
    )
    stats = screener.screen_book(1, 1)
    assert stats["rejected"] == 4
    assert all(row["reference_status"] == "discarded" for row in repository.updates.values())
    assert repository.updates[1]["quality_screen_meta_json"]["high_confidence_duplicate"]
