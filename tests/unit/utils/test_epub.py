from __future__ import annotations

from pathlib import Path

import pytest
from ebooklib import epub

from app.utils.epub import convert_epub_to_txt, convert_with_cache
from app.utils.errors import EpubConversionError


def _write_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("fixture-001")
    book.set_title("Fixture")
    book.set_language("zh")

    chapter = epub.EpubHtml(title="第一章", file_name="chapter-1.xhtml", lang="zh")
    chapter.content = "<h1>第一章</h1><p>第一段</p><script>bad()</script><p>第二段</p>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)


def test_convert_epub_to_txt(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    output = tmp_path / "book.txt"
    _write_epub(source)

    convert_epub_to_txt(source, output)

    text = output.read_text(encoding="utf-8")
    assert "第一章" in text
    assert "第一段" in text
    assert "第二段" in text
    assert "bad()" not in text


def test_convert_with_cache(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    converted = tmp_path / "converted"
    _write_epub(source)

    first_path, first_hit = convert_with_cache(source, converted)
    second_path, second_hit = convert_with_cache(source, converted)

    assert first_path == second_path
    assert first_hit is False
    assert second_hit is True


def test_missing_epub_raises(tmp_path: Path) -> None:
    with pytest.raises(EpubConversionError):
        convert_epub_to_txt(tmp_path / "missing.epub", tmp_path / "out.txt")
