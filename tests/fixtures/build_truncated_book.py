from __future__ import annotations

from pathlib import Path

from app.utils.text import ChapterSlice, split_chapters


def select_head_middle_tail(
    chapters: list[ChapterSlice],
    *,
    chapters_per_section: int = 1,
) -> list[ChapterSlice]:
    """Select bounded head, middle, and tail chapters without duplicates."""

    if chapters_per_section not in {1, 2}:
        raise ValueError("chapters_per_section must be 1 or 2")
    if not chapters:
        return []

    middle_start = max(
        0,
        len(chapters) // 2 - chapters_per_section // 2,
    )
    candidates = [
        *chapters[:chapters_per_section],
        *chapters[middle_start : middle_start + chapters_per_section],
        *chapters[-chapters_per_section:],
    ]
    unique = {chapter.chapter_index: chapter for chapter in candidates}
    return [unique[index] for index in sorted(unique)]


def build_truncated_book(
    text: str,
    *,
    chapters_per_section: int = 1,
) -> str:
    selected = select_head_middle_tail(
        split_chapters(text),
        chapters_per_section=chapters_per_section,
    )
    if not selected:
        raise ValueError("source text contains no readable chapters")
    return "\n\n".join(chapter.text for chapter in selected).strip() + "\n"


def build_truncated_book_from_path(
    source_path: Path,
    output_path: Path,
    *,
    chapters_per_section: int = 1,
) -> Path:
    source_path = Path(source_path)
    output_path = Path(output_path)
    truncated = build_truncated_book(
        source_path.read_text(encoding="utf-8-sig"),
        chapters_per_section=chapters_per_section,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(truncated, encoding="utf-8", newline="\n")
    return output_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--chapters-per-section",
        type=int,
        choices=(1, 2),
        default=1,
    )
    args = parser.parse_args()
    build_truncated_book_from_path(
        args.source,
        args.output,
        chapters_per_section=args.chapters_per_section,
    )
