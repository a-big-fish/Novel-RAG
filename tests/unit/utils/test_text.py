from __future__ import annotations

import pytest

from app.utils.errors import ModelInputTooLargeError
from app.utils.text import (
    bounded_sample,
    clean_text,
    normalized_char_count,
    split_chapters,
    split_paragraphs,
)


def test_clean_text_and_count() -> None:
    text = "\ufeff第一段\r\n\r\n\r\n第二段\u3000 内容"
    cleaned = clean_text(text)
    assert cleaned == "第一段\n\n第二段  内容"
    assert normalized_char_count("第 一\n段") == 3


def test_split_chapters_is_one_based() -> None:
    text = "序章内容\n\n第一章 开端\n\n第一段\n\n第二段\n\n第二章 继续\n\n第三段"
    chapters = split_chapters(text)
    assert [chapter.chapter_index for chapter in chapters] == [1, 2, 3]
    assert chapters[0].title == "序章"
    assert chapters[1].title == "第一章 开端"
    assert chapters[2].start_paragraph_index >= chapters[1].end_paragraph_index


def test_split_chapters_accepts_markdown_headings_and_epilogue() -> None:
    text = """# 书名

## 上部

### 第一章 雪夜

正文一。

### 第二章 重逢

正文二。

### 尾声 月光

正文三。
"""
    chapters = split_chapters(text)

    assert [chapter.title for chapter in chapters] == [
        "第一章 雪夜",
        "第二章 重逢",
        "尾声 月光",
    ]


def test_split_paragraphs() -> None:
    assert split_paragraphs("a\n\n b \n\n\n c") == ["a", "b", "c"]


def test_bounded_sample_keeps_all_sections() -> None:
    text = "A" * 100 + "B" * 100 + "C" * 100
    sample = bounded_sample(
        text,
        head_chars=20,
        middle_chars=20,
        tail_chars=20,
        max_chars=60,
    )
    assert sample.startswith("A")
    assert "B" in sample
    assert sample.endswith("C")
    assert len(sample) <= 70


def test_bounded_sample_rejects_invalid_limit() -> None:
    with pytest.raises(ModelInputTooLargeError):
        bounded_sample(
            "abc",
            head_chars=1,
            middle_chars=1,
            tail_chars=1,
            max_chars=0,
        )
