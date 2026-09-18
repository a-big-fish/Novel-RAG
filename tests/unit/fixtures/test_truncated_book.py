from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.text import split_chapters
from tests.fixtures.build_synthetic_micro_novel import write_micro_novel
from tests.fixtures.build_truncated_book import (
    build_truncated_book_from_path,
    select_head_middle_tail,
)


def test_truncated_book_keeps_head_middle_and_tail(tmp_path: Path) -> None:
    source = write_micro_novel(tmp_path / "micro.txt")
    output = build_truncated_book_from_path(
        source,
        tmp_path / "truncated.txt",
        chapters_per_section=1,
    )

    chapters = split_chapters(output.read_text(encoding="utf-8"))
    all_chapters = split_chapters(source.read_text(encoding="utf-8"))
    assert len(chapters) == 3
    expected = select_head_middle_tail(all_chapters, chapters_per_section=1)
    assert [chapter.title for chapter in chapters] == [
        chapter.title for chapter in expected
    ]
    assert sum(chapter.char_count for chapter in chapters) < sum(
        chapter.char_count for chapter in all_chapters
    )


def test_truncated_book_can_take_two_chapters_per_section(tmp_path: Path) -> None:
    source = write_micro_novel(tmp_path / "micro.txt")
    output = build_truncated_book_from_path(
        source,
        tmp_path / "truncated-2.txt",
        chapters_per_section=2,
    )

    chapters = split_chapters(output.read_text(encoding="utf-8"))
    all_chapters = split_chapters(source.read_text(encoding="utf-8"))
    expected = select_head_middle_tail(all_chapters, chapters_per_section=2)
    assert 4 <= len(chapters) <= 6
    assert [chapter.title for chapter in chapters] == [
        chapter.title for chapter in expected
    ]


def test_truncated_book_rejects_invalid_section_size(tmp_path: Path) -> None:
    source = write_micro_novel(tmp_path / "micro.txt")
    with pytest.raises(ValueError):
        build_truncated_book_from_path(
            source,
            tmp_path / "invalid.txt",
            chapters_per_section=3,
        )
