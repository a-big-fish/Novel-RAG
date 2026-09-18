from __future__ import annotations

import hashlib
import os
import tempfile
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import ITEM_DOCUMENT, epub

from app.utils.errors import EpubConversionError
from app.utils.text import clean_text

CONVERTER_VERSION = "epub-v1"
_SKIP_TAGS = ("script", "style", "nav", "noscript")


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def conversion_cache_path(
    source_path: Path,
    converted_dir: Path,
    *,
    converter_version: str = CONVERTER_VERSION,
) -> Path:
    return converted_dir / f"{sha256_file(source_path)}.{converter_version}.txt"


def _html_to_text(content: bytes) -> tuple[str, str]:
    # XHTML is well-formed XML, while some EPUB producers still emit
    # HTML-like fragments. Keep the tolerant HTML parser but suppress the
    # expected parser warning because both input shapes are intentional.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(content, "lxml")
    for tag in soup(_SKIP_TAGS):
        tag.decompose()

    heading = soup.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    title = heading.get_text(" ", strip=True) if heading else ""
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)

    blocks: list[str] = []
    for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p"]):
        value = element.get_text(" ", strip=True)
        if value:
            blocks.append(value)

    if not blocks:
        value = soup.get_text("\n", strip=True)
        blocks = [line.strip() for line in value.splitlines() if line.strip()]

    return title.strip(), "\n\n".join(blocks)


def convert_epub_to_txt(epub_path: Path, output_path: Path) -> Path:
    """Convert an EPUB to UTF-8 TXT following the EPUB spine order."""

    epub_path = Path(epub_path)
    output_path = Path(output_path)
    if not epub_path.is_file():
        raise EpubConversionError(f"EPUB not found: {epub_path}")

    try:
        book = epub.read_epub(str(epub_path))
    except Exception as exc:  # ebooklib exposes several parser-specific errors
        raise EpubConversionError(f"cannot parse EPUB: {exc}") from exc

    documents: list[tuple[str, str]] = []
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue
        title, body = _html_to_text(item.get_content())
        if body:
            documents.append((title or f"章节 {len(documents) + 1}", body))

    # Some malformed EPUBs have a broken spine but readable document items.
    if not documents:
        for item in book.get_items_of_type(ITEM_DOCUMENT):
            title, body = _html_to_text(item.get_content())
            if body:
                documents.append((title or f"章节 {len(documents) + 1}", body))

    if not documents:
        raise EpubConversionError("EPUB contains no readable document text")

    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for title, body in documents:
        key = (title, body)
        if key in seen:
            continue
        seen.add(key)
        if body.lstrip().startswith(title):
            parts.append(body)
        else:
            parts.append(f"{title}\n\n{body}")

    final_text = clean_text("\n\n".join(parts))
    if not final_text:
        raise EpubConversionError("EPUB conversion produced empty text")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(final_text)
            stream.write("\n")
        Path(temp_name).replace(output_path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return output_path


def convert_with_cache(
    epub_path: Path,
    converted_dir: Path,
    *,
    converter_version: str = CONVERTER_VERSION,
) -> tuple[Path, bool]:
    """Convert with content-addressed caching.

    Returns ``(cache_path, cache_hit)``.
    """

    converted_dir.mkdir(parents=True, exist_ok=True)
    cache_path = conversion_cache_path(
        epub_path,
        converted_dir,
        converter_version=converter_version,
    )
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path, True
    convert_epub_to_txt(epub_path, cache_path)
    return cache_path, False
