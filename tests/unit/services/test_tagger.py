from __future__ import annotations

import pytest

from app.services.schema import SceneAnnotation
from app.services.tagger import TagMappingError, TagVocabulary


def _vocabulary() -> TagVocabulary:
    return TagVocabulary(
        [
            {
                "namespace": "narrative_func",
                "canonical_key": "advance",
                "display_name": "推进剧情",
                "status": "active",
            },
            {
                "namespace": "style",
                "canonical_key": "short_sentence",
                "display_name": "冷硬短句",
                "status": "active",
            },
        ]
    )


def _annotation() -> SceneAnnotation:
    return SceneAnnotation(
        summary="摘要",
        style_summary="风格摘要",
        usage_hint="使用提示",
        style_tags=["冷硬短句", "未知标签"],
        narrative_func="推进剧情",
    )


def test_map_annotation_keeps_display_and_canonical() -> None:
    mapped = _vocabulary().map_annotation(_annotation())
    assert mapped.style_tags == ["short_sentence"]
    assert mapped.style_tags_display == ["冷硬短句", "未知标签"]
    assert mapped.narrative_func == "advance"


def test_invalid_narrative_func_is_rejected() -> None:
    annotation = _annotation().model_copy(update={"narrative_func": "不存在"})
    with pytest.raises(TagMappingError):
        _vocabulary().map_annotation(annotation)
