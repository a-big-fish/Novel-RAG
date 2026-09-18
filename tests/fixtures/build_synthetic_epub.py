from __future__ import annotations

from pathlib import Path

from ebooklib import epub


def write_synthetic_epub(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    book = epub.EpubBook()
    book.set_identifier("synthetic-micro-epub")
    book.set_title("合成微型 EPUB")
    book.set_language("zh")

    chapters: list[epub.EpubHtml] = []
    for index in range(1, 4):
        chapter = epub.EpubHtml(
            title=f"第{index}章",
            file_name=f"chapter-{index}.xhtml",
            lang="zh",
        )
        chapter.content = (
            f"<h1>第{index}章</h1>"
            f"<p>这是第{index}章的第一段。</p>"
            f"<script>this_must_not_survive()</script>"
            f"<p>这是第{index}章的第二段。</p>"
        )
        chapters.append(chapter)
        book.add_item(chapter)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]
    epub.write_epub(str(path), book)
    return path
