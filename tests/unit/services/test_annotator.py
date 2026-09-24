from __future__ import annotations

from typing import Any

from app.services.annotator import Annotator
from app.services.schema import SceneAnnotation
from app.services.tagger import TagVocabulary


class FakeLLM:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def request_typed(self, **kwargs: Any) -> SceneAnnotation:
        self.calls += 1
        return SceneAnnotation(
            summary="摘要",
            style_summary="风格摘要",
            usage_hint="使用提示",
            scene_type=["对话冲突"],
            technique=["钩子"],
            style_tags=["短句"],
            emotion_tags=["紧张"],
            key_images=["雨"],
            narrative_func="推进剧情",
        )


class FakeRepository:
    def __init__(self) -> None:
        self.scene_updates: list[tuple[int, dict[str, Any]]] = []
        self.cache: dict[str, dict[str, Any]] = {}

    def annotation_cache_key(self, **kwargs: Any) -> str:
        return "key-" + kwargs["input_text"]

    def update_scene(self, scene_id: int, **values: Any) -> None:
        self.scene_updates.append((scene_id, values))

    def get_annotation_cache(self, input_hash: str) -> dict[str, Any] | None:
        return self.cache.get(input_hash)

    def put_annotation_cache(self, input_hash: str, **kwargs: Any) -> None:
        self.cache[input_hash] = {"output_json": kwargs["output_json"]}

    def list_scenes(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


def _vocabulary() -> TagVocabulary:
    return TagVocabulary(
        [
            {"namespace": "narrative_func", "canonical_key": "advance", "display_name": "推进剧情", "status": "active"},
            {"namespace": "scene_type", "canonical_key": "dialogue_conflict", "display_name": "对话冲突", "status": "active"},
            {"namespace": "technique", "canonical_key": "hook", "display_name": "钩子", "status": "active"},
            {"namespace": "style", "canonical_key": "short_sentence", "display_name": "短句", "status": "active"},
            {"namespace": "emotion", "canonical_key": "tension", "display_name": "紧张", "status": "active"},
            {"namespace": "image", "canonical_key": "rain", "display_name": "雨", "status": "active"},
        ]
    )


def test_annotate_scene_writes_mapped_fields() -> None:
    repository = FakeRepository()
    llm = FakeLLM()
    annotator = Annotator(
        repository=repository,
        llm_client=llm,
        vocabulary=_vocabulary(),
    )
    status = annotator.annotate_scene(
        {
            "id": 1,
            "text": "测试正文",
            "chapter_start_index": 1,
            "chapter_end_index": 1,
            "reference_status": "selected",
        }
    )
    assert status == "annotated"
    assert llm.calls == 1
    final = repository.scene_updates[-1][1]
    assert final["scene_type"] == ["dialogue_conflict"]
    assert final["annotate_status"] == "annotated"


def test_annotate_scene_uses_cache() -> None:
    repository = FakeRepository()
    llm = FakeLLM()
    annotator = Annotator(
        repository=repository,
        llm_client=llm,
        vocabulary=_vocabulary(),
    )
    scene = {
        "id": 1,
        "text": "测试正文",
        "chapter_start_index": 1,
        "chapter_end_index": 1,
        "reference_status": "selected",
    }
    scene_annotation = FakeLLM().request_typed()
    repository.cache["key-测试正文"] = {
        "output_json": scene_annotation.model_dump()
    }
    assert annotator.annotate_scene(scene) == "annotated"
    assert llm.calls == 0
