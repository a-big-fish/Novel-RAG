PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """你负责粗读小说并判断场景边界。只返回 JSON 对象，格式为
{"boundaries": [2, 5]}。数字是输入中从 1 开始的段落编号，表示该段开始一个新场景。
根据时间、地点、参与人物、主要行动或叙事焦点的实际变化判断；对白换行、
普通段落换行不自动构成新场景。优先保留完整行动单元，避免把连续叙事
切成大量短片段。没有明确边界就返回空数组。
不得改写原文，不得添加输入中不存在的编号。"""

CROSS_CHAPTER_SYSTEM_PROMPT = """判断章节边界两侧是否明确属于同一个连续场景。
只返回 JSON 对象：{"same_scene": true} 或 {"same_scene": false}。
只有时间、地点、参与人物和核心行动都连续时才返回 true；不确定时返回 false。
章节编号本身不是决定依据。"""


def build_user_prompt(paragraphs: list[str]) -> str:
    numbered = "\n".join(
        f"[{index}] {paragraph}"
        for index, paragraph in enumerate(paragraphs, start=1)
    )
    return "判断以下相邻段落之间的场景边界：\n" + numbered


def build_cross_chapter_prompt(previous: str, following: str) -> str:
    return f"上一章末尾：\n{previous}\n\n下一章开头：\n{following}"
