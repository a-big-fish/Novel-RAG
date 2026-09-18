from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.epub import convert_with_cache
from app.utils.text import split_chapters
from tests.fixtures.build_synthetic_epub import write_synthetic_epub

pytestmark = pytest.mark.integration


def test_synthetic_epub_conversion_round_trip(tmp_path: Path) -> None:
    source = write_synthetic_epub(tmp_path / "fixture.epub")
    converted_dir = tmp_path / "converted"

    first_path, first_hit = convert_with_cache(source, converted_dir)
    second_path, second_hit = convert_with_cache(source, converted_dir)

    assert first_path == second_path
    assert first_hit is False
    assert second_hit is True
    text = first_path.read_text(encoding="utf-8")
    assert "this_must_not_survive" not in text
    chapters = split_chapters(text)
    assert len(chapters) == 3
    assert [chapter.title for chapter in chapters] == ["第1章", "第2章", "第3章"]
