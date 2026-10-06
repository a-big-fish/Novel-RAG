from __future__ import annotations

import re
from dataclasses import replace

from pydantic import BaseModel, Field

from app.clients.llm_client import JsonLLMClient
from app.config import Settings, get_settings
from app.prompts.scene_splitting import v1 as scene_prompt
from app.services.splitter import SceneDraft
from app.utils.text import ChapterSlice, normalized_char_count, split_paragraphs

_SENTENCE_END_RE = re.compile(r"[。！？；.!?;]")


class SceneBoundaryDecision(BaseModel):
    boundaries: list[int] = Field(default_factory=list)


class CrossChapterDecision(BaseModel):
    same_scene: bool = False


def _split_long_paragraph(paragraph: str, limit: int) -> list[str]:
    """Keep every character when a source paragraph exceeds the model budget."""
    result: list[str] = []
    remaining = paragraph
    while len(remaining) > limit:
        candidate = max(
            (match.end() for match in _SENTENCE_END_RE.finditer(remaining[:limit])),
            default=0,
        )
        cut = candidate if candidate >= limit // 2 else limit
        result.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        result.append(remaining)
    return result


class LLMSceneSplitter:
    """Ask an LLM to mark paragraph boundaries within bounded reading windows."""

    def __init__(
        self,
        llm_client: JsonLLMClient,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.settings = settings or get_settings()

    def split(self, chapters: list[ChapterSlice]) -> list[SceneDraft]:
        budget = self.settings.max_llm_input_chars - 500
        if budget < 500:
            raise ValueError("MAX_LLM_INPUT_CHARS must be at least 1000 for scene splitting")
        scenes: list[SceneDraft] = []
        for chapter in chapters:
            chapter_first_scene = len(scenes)
            paragraphs = [
                part
                for paragraph in split_paragraphs(chapter.text)
                for part in _split_long_paragraph(paragraph, budget - 20)
            ]
            window: list[str] = []
            window_chars = 0
            first_window = True

            def flush() -> None:
                nonlocal window, window_chars, first_window
                if not window:
                    return
                boundaries = []
                if len(window) > 1:
                    decision = self.llm_client.request_typed(
                        system_prompt=scene_prompt.SYSTEM_PROMPT,
                        user_prompt=scene_prompt.build_user_prompt(window),
                        response_model=SceneBoundaryDecision,
                    )
                    boundaries = decision.boundaries
                if (
                    len(set(boundaries)) != len(boundaries)
                    or any(index < 2 or index > len(window) for index in boundaries)
                ):
                    raise ValueError("LLM returned invalid scene boundary indices")
                starts = [1, *sorted(boundaries), len(window) + 1]
                for offset, (start, end) in enumerate(zip(starts, starts[1:])):
                    scene_text = "\n\n".join(window[start - 1 : end - 1]).strip()
                    if not scene_text:
                        continue
                    scenes.append(
                        SceneDraft(
                            scene_index_in_book=len(scenes) + 1,
                            chapter_start_index=chapter.chapter_index,
                            chapter_end_index=chapter.chapter_index,
                            text=scene_text,
                            char_count=normalized_char_count(scene_text),
                            split_reason=(
                                "chapter_start" if first_window and offset == 0
                                else "length_limit" if offset == 0
                                else "llm_boundary"
                            ),
                        )
                    )
                first_window = False
                window = []
                window_chars = 0

            for paragraph in paragraphs:
                # Account for paragraph labels in the actual LLM prompt.
                added = len(paragraph) + len(f"[{len(window) + 1}] ") + 1
                if window and window_chars + added > budget:
                    flush()
                    added = len(paragraph) + len("[1] ") + 1
                window.append(paragraph)
                window_chars += added
            flush()
            if chapter_first_scene and len(scenes) > chapter_first_scene:
                previous = scenes[chapter_first_scene - 1]
                following = scenes[chapter_first_scene]
                combined = f"{previous.text}\n\n{following.text}"
                if (
                    previous.chapter_end_index == chapter.chapter_index - 1
                    and len(combined) <= budget
                ):
                    decision = self.llm_client.request_typed(
                        system_prompt=scene_prompt.CROSS_CHAPTER_SYSTEM_PROMPT,
                        user_prompt=scene_prompt.build_cross_chapter_prompt(
                            previous.text, following.text,
                        ),
                        response_model=CrossChapterDecision,
                    )
                    if decision.same_scene:
                        scenes[chapter_first_scene - 1] = replace(
                            previous,
                            chapter_end_index=following.chapter_end_index,
                            text=combined,
                            char_count=normalized_char_count(combined),
                            split_reason="llm_cross_chapter_merge",
                            is_cross_chapter=True,
                        )
                        del scenes[chapter_first_scene]
        return [
            replace(scene, scene_index_in_book=index)
            for index, scene in enumerate(scenes, start=1)
        ]
