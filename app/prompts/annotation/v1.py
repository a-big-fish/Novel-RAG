from __future__ import annotations

from app.services.tagger import TagVocabulary

PROMPT_VERSION = "v1.0"

SYSTEM_PROMPT = """\
你是资深中文网文编辑与写作技法分析师。
你一次只分析一个小说场景，不得假设看过全书，也不得要求补充其他章节。
你的任务是提炼剧情摘要、可复用写法、使用建议和标签。
只输出一个合法 JSON 对象，不要输出 Markdown 代码围栏或额外说明。
"""


def build_user_prompt(
    *,
    scene_text: str,
    chapter_start_index: int,
    chapter_end_index: int,
    vocabulary: TagVocabulary,
) -> str:
    """Build a bounded prompt for exactly one scene."""

    scene_type_options = "、".join(vocabulary.prompts_for("scene_type"))
    technique_options = "、".join(vocabulary.prompts_for("technique"))
    style_options = "、".join(vocabulary.prompts_for("style"))
    emotion_options = "、".join(vocabulary.prompts_for("emotion"))
    image_options = "、".join(vocabulary.prompts_for("image"))
    narrative_options = "、".join(vocabulary.prompts_for("narrative_func"))

    return f"""\
章节范围：第 {chapter_start_index} 章至第 {chapter_end_index} 章

场景原文（已按输入预算做有界采样）：
---BEGIN SCENE---
{scene_text}
---END SCENE---

请输出以下 JSON 字段：
{{
  "summary": "50-150字剧情摘要",
  "style_summary": "30-120字，说明这段为什么可作为写作范本",
  "usage_hint": "20-80字，说明适合参考什么、避免什么",
  "scene_type": ["从场景类型词表选择1-3项"],
  "technique": ["从技法词表选择1-4项"],
  "style_tags": ["从文风词表选择3-6项"],
  "emotion_tags": ["从情绪词表选择2-4项"],
  "key_images": ["从意象词表选择1-5项"],
  "narrative_func": "从叙事功能词表严格选择1项"
}}

场景类型词表：{scene_type_options or "无"}
写作技法词表：{technique_options or "无"}
文风词表：{style_options or "无"}
情绪词表：{emotion_options or "无"}
意象词表：{image_options or "无"}
叙事功能词表：{narrative_options or "无"}
"""
