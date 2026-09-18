from __future__ import annotations

import re
from dataclasses import dataclass

from app.utils.text import ChapterSlice, normalized_char_count, split_paragraphs

_TIME_BOUNDARY_RE = re.compile(
    r"^(?:"
    r"第二天|次日|翌日|当天晚上|当晚|夜里|深夜|清晨|早上|中午|下午|傍晚|"
    r"半小时后|一小时后|几天后|数日后|一个月后|多年后"
    r")"
)
_SEPARATOR_RE = re.compile(r"^\s*(?:\*{3,}|-{3,}|—{3,}|={3,}|◆{2,})\s*$")


@dataclass(frozen=True, slots=True)
class SceneDraft:
    scene_index_in_book: int
    chapter_start_index: int
    chapter_end_index: int
    text: str
    char_count: int
    split_reason: str
    is_cross_chapter: bool = False


def _is_boundary(paragraph: str) -> bool:
    return bool(_SEPARATOR_RE.fullmatch(paragraph) or _TIME_BOUNDARY_RE.match(paragraph))


def split_chapters_into_scenes(
    chapters: list[ChapterSlice],
) -> list[SceneDraft]:
    """Split chapters into rule-based scenes.

    Chapter boundaries are conservative scene boundaries. Cross-chapter scene
    merging is intentionally left to an explicit later LLM judgement step; the
    default never merges chapters.
    """

    scenes: list[SceneDraft] = []
    for chapter in chapters:
        paragraphs = split_paragraphs(chapter.text)
        if not paragraphs:
            continue

        current: list[str] = []
        current_reason = "chapter_start"

        def flush() -> None:
            nonlocal current, current_reason
            if not current:
                return
            text = "\n\n".join(current).strip()
            if text:
                scenes.append(
                    SceneDraft(
                        scene_index_in_book=len(scenes) + 1,
                        chapter_start_index=chapter.chapter_index,
                        chapter_end_index=chapter.chapter_index,
                        text=text,
                        char_count=normalized_char_count(text),
                        split_reason=current_reason,
                    )
                )
            current = []
            current_reason = "rule"

        for paragraph in paragraphs:
            if _SEPARATOR_RE.fullmatch(paragraph):
                flush()
                continue
            if current and _is_boundary(paragraph):
                flush()
                current_reason = "time_or_place_boundary"
            current.append(paragraph)
        flush()

    return scenes
