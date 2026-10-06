from __future__ import annotations

from pathlib import Path

import pytest
from ebooklib import epub

from app.utils.epub import convert_epub_to_txt, convert_with_cache
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


def test_long_epub_uses_spine_body_boundaries_not_back_matter_toc(
    tmp_path: Path,
) -> None:
    book = epub.EpubBook()
    book.set_identifier("long-body-with-back-matter-toc")
    book.set_title("合成长篇")
    book.set_language("zh")

    def page(name: str, html: str) -> epub.EpubHtml:
        item = epub.EpubHtml(title=name, file_name=f"{name}.xhtml", lang="zh")
        item.content = html
        book.add_item(item)
        return item

    cover = page("cover", "<p>封面</p>")
    copyright_page = page("copyright", "<p>版权说明</p>")
    body = [
        page(f"body-{index}", "<p>" + f"第{index}部分的正文。" * 600 + "</p>")
        for index in range(1, 5)
    ]
    back = page("back", "<p>附录</p>")
    toc = page(
        "toc-end",
        "".join(f'<p><a href="body-1.xhtml">第{index}章</a></p>' for index in range(1, 21)),
    )
    book.toc = tuple(body)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", cover, copyright_page, *body, back, toc]
    source = tmp_path / "long.epub"
    epub.write_epub(str(source), book)

    converted = tmp_path / "converted.txt"
    convert_epub_to_txt(source, converted)
    chapters = split_chapters(converted.read_text(encoding="utf-8"))

    assert len(chapters) == 4
    assert [chapter.title for chapter in chapters] == [
        "第1章", "第2章", "第3章", "第4章",
    ]
    assert all(chapter.char_count > 3000 for chapter in chapters)
    assert "版权说明" not in converted.read_text(encoding="utf-8")
    assert "附录" not in converted.read_text(encoding="utf-8")
