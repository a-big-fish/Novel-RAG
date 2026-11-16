"""Versioned prompt that extracts a concrete style signature for one scene.

Used only when rebuilding meta-dense vectors: the output must distinguish this
scene from other scenes instead of repeating generic register words.
"""

from __future__ import annotations

PROMPT_VERSION = "style-signature-v1"

SYSTEM_PROMPT = """\
你是中文小说文体分析师。你一次只分析一个场景，不假设看过全书。
任务：提炼这段文字**独有**的可迁移写法，用于区分它与同书其他场景。
只输出一个合法 JSON 对象，不要输出 Markdown 代码围栏或额外说明。"""


def build_user_prompt(
    *,
    scene_text: str,
    summary: str,
    style_summary: str,
    usage_hint: str,
    technique: list[str],
    key_images: list[str],
) -> str:
    return f"""\
已有标注（仅作参考，不要照抄，也不要只复述）：
剧情摘要：{summary or "—"}
文风概述：{style_summary or "—"}
使用提示：{usage_hint or "—"}
已选技法标签：{"、".join(technique) or "—"}
已选意象标签：{"、".join(key_images) or "—"}

场景原文（有界采样）：
---BEGIN SCENE---
{scene_text}
---END SCENE---

请输出 JSON：
{{
  "style_signature": "60-200字。必须引用本场景中的具体物件、句式、对话节奏或信息释放方式；禁止只写'克制''短句''电影感'等泛词，泛词必须附带具体例子。",
  "distinctive_devices": ["3-5条只属于本场景的写法，每条20字以内，写明'用什么做了什么'"],
  "retrieval_hooks": ["3-6个可检索的区分性短语，如'磁带机串联两代人''三问三答递进'"]
}}
"""
