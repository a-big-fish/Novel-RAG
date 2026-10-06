from __future__ import annotations

import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Callable

from pydantic import BaseModel, Field

from app.clients.llm_client import JsonLLMClient
from app.config import Settings, get_settings
from app.prompts.scene_splitting import v1 as scene_prompt
from app.services.splitter import SceneDraft
from app.utils.text import ChapterSlice, normalized_char_count, split_paragraphs

_SENTENCE_END_RE = re.compile(r"[。！？；.!?;]")
logger = logging.getLogger(__name__)


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
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.settings = settings or get_settings()
        self.progress = progress

    @staticmethod
    def _windows(chapter: ChapterSlice, budget: int) -> list[list[str]]:
        paragraphs = [
            part
            for paragraph in split_paragraphs(chapter.text)
            for part in _split_long_paragraph(paragraph, budget - 20)
        ]
        windows: list[list[str]] = []
        window: list[str] = []
        window_chars = 0
        for paragraph in paragraphs:
            added = len(paragraph) + len(f"[{len(window) + 1}] ") + 1
            if window and window_chars + added > budget:
                windows.append(window)
                window = []
                window_chars = 0
                added = len(paragraph) + len("[1] ") + 1
            window.append(paragraph)
            window_chars += added
        if window:
            windows.append(window)
        return windows

    def _judge_window(self, window: list[str]) -> list[int]:
        if len(window) == 1:
            return []
        decision = self.llm_client.request_typed(
            system_prompt=scene_prompt.SYSTEM_PROMPT,
            user_prompt=scene_prompt.build_user_prompt(window),
            response_model=SceneBoundaryDecision,
        )
        boundaries = decision.boundaries
        valid_boundaries = sorted({
            index for index in boundaries if 2 <= index <= len(window)
        })
        if len(valid_boundaries) != len(boundaries):
            logger.warning(
                "ignored invalid LLM scene boundary indices",
                extra={
                    "paragraph_count": len(window),
                    "proposed_count": len(boundaries),
                    "valid_count": len(valid_boundaries),
                    "model": self.llm_client.model,
                },
            )
        minimum = min(self.settings.scene_min_chars, max(1, (self.settings.max_llm_input_chars - 500) // 2))
        accepted: list[int] = []
        start = 1
        for index in valid_boundaries:
            left = normalized_char_count("".join(window[start - 1 : index - 1]))
            right = normalized_char_count("".join(window[index - 1 :]))
            if left >= minimum and right >= minimum:
                accepted.append(index)
                start = index
        return accepted

    def split(self, chapters: list[ChapterSlice]) -> list[SceneDraft]:
        budget = self.settings.max_llm_input_chars - 500
        if budget < 500:
            raise ValueError("MAX_LLM_INPUT_CHARS must be at least 1000 for scene splitting")
        chapter_windows = [self._windows(chapter, budget) for chapter in chapters]
        decisions: dict[tuple[int, int], list[int]] = {}
        total = sum(map(len, chapter_windows))
        if total:
            if self.progress is not None:
                self.progress(0, total)
            workers = max(1, min(self.settings.llm_concurrency, total))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._judge_window, window): (chapter_index, window_index)
                    for chapter_index, windows in enumerate(chapter_windows)
                    for window_index, window in enumerate(windows)
                }
                for done, future in enumerate(as_completed(futures), start=1):
                    try:
                        decisions[futures[future]] = future.result()
                    except Exception:
                        for pending in futures:
                            pending.cancel()
                        raise
                    if self.progress is not None:
                        self.progress(done, total)

        scenes: list[SceneDraft] = []
        for chapter_index, chapter in enumerate(chapters):
            chapter_first_scene = len(scenes)
            for window_index, window in enumerate(chapter_windows[chapter_index]):
                boundaries = decisions[chapter_index, window_index]
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
                                "chapter_start" if window_index == 0 and offset == 0
                                else "length_limit" if offset == 0
                                else "llm_boundary"
                            ),
                        )
                    )
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
