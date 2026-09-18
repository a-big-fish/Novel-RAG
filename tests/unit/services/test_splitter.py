from __future__ import annotations

from app.services.splitter import split_chapters_into_scenes
from app.utils.text import ChapterSlice


def _chapter(index: int, text: str) -> ChapterSlice:
    return ChapterSlice(
        chapter_index=index,
        title=f"第{index}章",
        text=text,
        start_paragraph_index=0,
        end_paragraph_index=1,
        char_count=len(text),
    )


def test_split_scenes_on_chapter_and_separator() -> None:
    chapters = [
        _chapter(1, "第一章\n\n开头。\n\n***\n\n新的场景。"),
        _chapter(2, "第二章\n\n第二天，新的冲突。\n\n继续。"),
    ]
    scenes = split_chapters_into_scenes(chapters)
    assert [scene.scene_index_in_book for scene in scenes] == [1, 2, 3, 4]
    assert scenes[0].chapter_start_index == 1
    assert scenes[2].chapter_start_index == 2
    assert all(not scene.is_cross_chapter for scene in scenes)
