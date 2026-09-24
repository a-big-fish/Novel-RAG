from __future__ import annotations

PROMPT_VERSION = "v1.0"

SYSTEM_PROMPT = """\
你是中文小说 Writing Reference Library 的准入评估员。
你一次只评估一个已完成场景边界判定的 Final Scene。
你的任务不是评判作品整体好坏，而是判断该 Scene 是否具有明确、独立、可迁移的写作参考价值。
普通转场、时间跳跃、事实交代或剧情连接可以是完整有效的 Scene，但仍可归为 archived。
语言不华丽但在对白节奏、冲突、悬念、信息揭露、动作调度、视角、氛围或情绪控制上可迁移的 Scene 可以 selected。
只输出一个合法 JSON 对象，不要输出 Markdown 代码围栏或额外说明。
你的判断应该审慎，克制，严谨。
不要尝试进行任何读图操作。
"""


def build_user_prompt(
    *,
    scene_text: str,
    chapter_start_index: int,
    chapter_end_index: int,
    is_cross_chapter: bool,
) -> str:
    """Build the bounded prompt for exactly one final scene."""

    return f"""\
章节范围：第 {chapter_start_index} 章至第 {chapter_end_index} 章
是否跨章：{"yes" if is_cross_chapter else "no"}

Final Scene 原文（已按输入预算做有界采样）：
---BEGIN FINAL SCENE---
{scene_text}
---END FINAL SCENE---

请从以下维度判断它是否值得进入 Writing Reference Library：
- prose_quality
- technique_value
- scene_completeness
- context_independence
- distinctiveness
- reference_value（最重要：对另一位作者是否有值得学习和迁移的写法）

输出严格 JSON：
{{
  "reference_status": "selected 或 archived",
  "reference_score": 0.0到5.0,
  "reference_reason": "清晰说明准入或归档原因",
  "dimensions": {{
    "prose_quality": 0.0到5.0,
    "technique_value": 0.0到5.0,
    "scene_completeness": 0.0到5.0,
    "context_independence": 0.0到5.0,
    "distinctiveness": 0.0到5.0,
    "reference_value": 0.0到5.0
  }}
}}

不要因为“文字没有问题”就选中；也不要因为“语言不华丽”就归档。
"""
