from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.utils.errors import ModelInputTooLargeError


_CHAPTER_PATTERNS = (
    re.compile(
        r"^\s*(第[零〇一二三四五六七八九十百千万两0-9]+[章节回卷部篇]"
        r"(?:\s*[-—:：、.]?\s*.*)?)\s*$"
    ),
    re.compile(
        r"^\s*((?:Chapter|CHAPTER|chapter)\s+\d+(?:\s*[-:：]\s*.*)?)\s*$"
    ),
    re.compile(r"^\s*((?:序章|楔子|尾声|后记)(?:\s+.*)?)\s*$"),
)
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class ChapterSlice:
    chapter_index: int
    title: str
    text: str
    start_paragraph_index: int
    end_paragraph_index: int
    char_count: int


def clean_text(text: str) -> str:
    """Normalize text without changing its semantic content.

    The function removes BOM and control characters, normalizes newlines and
    full-width spaces, and collapses excessive blank lines.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ")
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL_RE.sub("", text)
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return _MULTI_BLANK_RE.sub("\n\n", text)


def normalized_char_count(text: str) -> int:
    """Count Unicode characters after removing all whitespace."""

    return sum(1 for char in text if not char.isspace())


def is_chapter_title(line: str) -> bool:
    stripped = _MARKDOWN_HEADING_RE.sub("", line.strip()).strip()
    if not stripped or len(stripped) > 80:
        return False
    return any(pattern.match(stripped) for pattern in _CHAPTER_PATTERNS)


def _normalized_chapter_title(line: str) -> str:
    return _MARKDOWN_HEADING_RE.sub("", line.strip()).strip()


def _markdown_headings_only(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and all(_MARKDOWN_HEADING_RE.match(line) for line in lines)


def split_chapters(text: str) -> list[ChapterSlice]:
    """Split cleaned text into 1-based chapters.

    Content before the first recognizable chapter title is kept as a
    provisional first chapter. If no titles are found, the whole text is one
    chapter.
    """

    cleaned = clean_text(text)
    if not cleaned:
        return []

    lines = cleaned.split("\n")
    title_positions = [
        index for index, line in enumerate(lines) if is_chapter_title(line)
    ]

    chapters: list[ChapterSlice] = []
    if not title_positions:
        paragraphs = split_paragraphs(cleaned)
        return [
            ChapterSlice(
                chapter_index=1,
                title="正文",
                text=cleaned,
                start_paragraph_index=0,
                end_paragraph_index=max(0, len(paragraphs) - 1),
                char_count=normalized_char_count(cleaned),
            )
        ]

    if title_positions[0] > 0:
        prefix = "\n".join(lines[: title_positions[0]]).strip()
        if prefix and not _markdown_headings_only(prefix):
            paragraphs = split_paragraphs(prefix)
            chapters.append(
                ChapterSlice(
                    chapter_index=1,
                    title="序章",
                    text=prefix,
                    start_paragraph_index=0,
                    end_paragraph_index=max(0, len(paragraphs) - 1),
                    char_count=normalized_char_count(prefix),
                )
            )

    paragraph_cursor = 0
    for position_index, start_line in enumerate(title_positions):
        end_line = (
            title_positions[position_index + 1]
            if position_index + 1 < len(title_positions)
            else len(lines)
        )
        title = _normalized_chapter_title(lines[start_line])
        body = "\n".join(lines[start_line + 1 : end_line]).strip()
        chapter_text = f"{title}\n\n{body}".strip()
        paragraph_count = len(split_paragraphs(chapter_text))
        chapters.append(
            ChapterSlice(
                chapter_index=len(chapters) + 1,
                title=title,
                text=chapter_text,
                start_paragraph_index=paragraph_cursor,
                end_paragraph_index=paragraph_cursor + max(0, paragraph_count - 1),
                char_count=normalized_char_count(chapter_text),
            )
        )
        paragraph_cursor += paragraph_count

    return chapters


def split_paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs on blank lines."""

    cleaned = clean_text(text)
    if not cleaned:
        return []
    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", cleaned)
        if paragraph.strip()
    ]


def bounded_sample(
    text: str,
    *,
    head_chars: int,
    middle_chars: int,
    tail_chars: int,
    max_chars: int,
) -> str:
    """Return a deterministic head/middle/tail sample bounded by max_chars.

    Configuration is validated instead of silently truncating at an
    unpredictable location.
    """

    if max_chars <= 0:
        raise ModelInputTooLargeError("max_chars must be positive")
    normalized = clean_text(text)
    if normalized_char_count(normalized) <= max_chars:
        return normalized

    budgets = [max(0, head_chars), max(0, middle_chars), max(0, tail_chars)]

    def build() -> str:
        parts: list[str] = []
        if budgets[0]:
            parts.append(normalized[: budgets[0]])
        if budgets[1]:
            middle_start = max(0, (len(normalized) - budgets[1]) // 2)
            parts.append(normalized[middle_start : middle_start + budgets[1]])
        if budgets[2]:
            parts.append(normalized[-budgets[2] :])
        return "\n...\n".join(part for part in parts if part)

    # Separators also count toward the model-input budget. Reduce the largest
    # section one character at a time; the configured maxima remain upper
    # bounds rather than minimums.
    sample = build()
    while normalized_char_count(sample) > max_chars and any(budgets):
        index = max(range(3), key=lambda item: budgets[item])
        budgets[index] -= 1
        sample = build()

    if normalized_char_count(sample) > max_chars:
        raise ModelInputTooLargeError(
            "sampling configuration cannot satisfy max_chars"
        )
    return sample
