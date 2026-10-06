from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.services.llm_splitter import (
    CrossChapterDecision,
    LLMSceneSplitter,
    SceneBoundaryDecision,
)
from app.utils.text import ChapterSlice


def _chapter(text: str) -> ChapterSlice:
    return ChapterSlice(
        chapter_index=1, title="第一章", text=text,
        start_paragraph_index=0, end_paragraph_index=0,
        char_count=len(text),
    )


class FakeBoundaryLLM:
    model = "fake-boundary-model"

    def __init__(self, boundaries: list[int], *, same_scene: bool = False) -> None:
        self.boundaries = boundaries
        self.same_scene = same_scene
        self.calls: list[str] = []

    def request_typed(self, **kwargs: Any) -> SceneBoundaryDecision | CrossChapterDecision:
        self.calls.append(kwargs["user_prompt"])
        if kwargs["response_model"] is CrossChapterDecision:
            return CrossChapterDecision(same_scene=self.same_scene)
        assert kwargs["response_model"] is SceneBoundaryDecision
        return SceneBoundaryDecision(boundaries=self.boundaries)


def test_llm_boundaries_and_length_cap_preserve_all_text() -> None:
    paragraphs = [f"段落{i}。" + "内容" * 190 for i in range(1, 9)]
    text = "\n\n".join(paragraphs)
    llm = FakeBoundaryLLM([2])
    splitter = LLMSceneSplitter(
        llm, settings=Settings(_env_file=None, max_llm_input_chars=1500),
    )

    scenes = splitter.split([_chapter(text)])

    assert len(llm.calls) == 4
    assert len(scenes) == 8
    assert scenes[0].split_reason == "chapter_start"
    assert scenes[1].split_reason == "llm_boundary"
    assert scenes[2].split_reason == "length_limit"
    assert all(scene.char_count <= 1000 for scene in scenes)
    assert "\n\n".join(scene.text for scene in scenes) == text


def test_oversized_paragraph_is_bounded_without_losing_characters() -> None:
    text = "长" * 2700
    llm = FakeBoundaryLLM([])
    splitter = LLMSceneSplitter(
        llm, settings=Settings(_env_file=None, max_llm_input_chars=1500),
    )

    scenes = splitter.split([_chapter(text)])

    assert llm.calls == []
    assert "".join(scene.text for scene in scenes) == text
    assert all(scene.char_count <= 1000 for scene in scenes)


def test_invalid_llm_boundary_fails_instead_of_silently_corrupting_scenes() -> None:
    llm = FakeBoundaryLLM([99])
    splitter = LLMSceneSplitter(
        llm, settings=Settings(_env_file=None, max_llm_input_chars=1500),
    )

    with pytest.raises(ValueError, match="invalid scene boundary"):
        splitter.split([_chapter("第一段。\n\n第二段。")])


def test_many_short_paragraphs_keep_prompt_bounded() -> None:
    llm = FakeBoundaryLLM([])
    splitter = LLMSceneSplitter(
        llm, settings=Settings(_env_file=None, max_llm_input_chars=1500),
    )

    scenes = splitter.split([_chapter("\n\n".join(["字"] * 500))])

    assert len(llm.calls) > 1
    assert all(len(prompt) <= 1500 for prompt in llm.calls)
    assert sum(scene.char_count for scene in scenes) == 500


def test_llm_can_merge_continuous_scene_across_short_chapters() -> None:
    first = _chapter("第一章\n\n两人在雨夜交谈。")
    second = ChapterSlice(
        chapter_index=2, title="第二章",
        text="第二章\n\n他们继续刚才的对话。",
        start_paragraph_index=2, end_paragraph_index=3,
        char_count=20,
    )
    llm = FakeBoundaryLLM([], same_scene=True)
    splitter = LLMSceneSplitter(
        llm, settings=Settings(_env_file=None, max_llm_input_chars=1500),
    )

    scenes = splitter.split([first, second])

    assert len(scenes) == 1
    assert scenes[0].chapter_start_index == 1
    assert scenes[0].chapter_end_index == 2
    assert scenes[0].is_cross_chapter is True
    assert scenes[0].split_reason == "llm_cross_chapter_merge"
