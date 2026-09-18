from __future__ import annotations

from pathlib import Path

CHAPTER_TITLES = (
    "第一章 雨夜来客",
    "第二章 旧城试探",
    "第三章 灯火交锋",
    "第四章 真相边缘",
    "第五章 风停之后",
)

PARAGRAPHS = (
    "雨落在旧城的屋檐上，像一层薄薄的铁皮被反复敲响。",
    "她把伞收起来，站在门槛外，没有立刻敲门。",
    "屋里的灯亮着，窗纸上映出一个来回踱步的影子。",
    "门开时，他没有问她为什么来，只侧身让出一条路。",
    "空气里有潮湿木头和冷茶的味道，两个人都没有先开口。",
    "桌角压着一封没有署名的信，火漆已经被人剥开。",
    "她看了那封信一眼，指尖却停在袖口，像是忘了下一步。",
    "他说，事情比你想的更麻烦。她笑了一下，说我从来不怕麻烦。",
    "远处传来钟声，城里的灯一盏接一盏暗下去。",
    "刀放在桌下，谁都没有碰，可谁都记得它在那里。",
)


def build_micro_novel() -> str:
    """Build a deterministic 4-5 chapter Chinese miniature novel."""

    parts: list[str] = []
    target_chars = 4000
    while sum(len(part) for part in parts) < target_chars:
        chapter_index = len(parts) % len(CHAPTER_TITLES)
        chapter_number = len(parts) + 1
        title = (
            CHAPTER_TITLES[chapter_index]
            if chapter_number <= len(CHAPTER_TITLES)
            else f"第{chapter_number}章 回声"
        )
        body: list[str] = []
        for index in range(16):
            paragraph = PARAGRAPHS[(chapter_number + index) % len(PARAGRAPHS)]
            body.append(paragraph)
            if index in {5, 10}:
                body.append("第二天，新的线索又把两个人推向同一条窄路。")
        parts.append(f"{title}\n\n" + "\n\n".join(body))
    return "\n\n".join(parts)


def write_micro_novel(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_micro_novel() + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_micro_novel(args.output)
