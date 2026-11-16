"""Versioned prompt template for assembling retrieved scenes into a prompt.

The assembled text is handed to the caller's writing model. It contains the
writing task, the reference scenes and a short usage policy, but never generates
new prose on its own.
"""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "assembly-v1"

USE_POLICY = (
    "以下场景是写作范本，不是剧情素材：不要照抄人物、事件、设定或原句。",
    "优先学习节奏、视角、对话推进、信息释放、情绪转折与收束方式。",
    "若参考场景与写作任务冲突，以写作任务为准。",
    "不要在正文里提及“参考场景”编号或检索过程。",
)

OUTPUT_POLICY = "请基于以上参考完成写作任务，只输出正文。"


def _constraints_block(constraints: dict[str, Any] | None) -> str:
    if not constraints:
        return ""
    lines: list[str] = []
    for key, value in constraints.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple, set)):
            text = "、".join(str(item) for item in value)
        else:
            text = str(value)
        lines.append(f"- {key}：{text}")
    if not lines:
        return ""
    return "【额外约束】\n" + "\n".join(lines)


def render_reference(
    *,
    index: int,
    title: str,
    version: int,
    scene_index: int,
    rank: int,
    summary: str,
    style_summary: str,
    usage_hint: str,
    excerpt: str,
    truncated: bool,
) -> str:
    parts = [
        f"【参考场景 {index}｜{title} v{version}｜场景 {scene_index}｜书内名次 #{rank}】",
        f"剧情摘要：{summary or '—'}",
        f"文风与手法：{style_summary or '—'}",
        f"使用提示：{usage_hint or '—'}",
    ]
    if excerpt:
        suffix = "（有界采样）" if truncated else ""
        parts.append(f"原文节选{suffix}：\n{excerpt}")
    return "\n".join(parts)


def render_prompt(
    *,
    task: str,
    references: list[dict[str, Any]],
    constraints: dict[str, Any] | None = None,
    empty_reason: str | None = None,
) -> str:
    sections = ["【写作任务】", task.strip() or "（未提供写作任务）", "", "【参考使用原则】"]
    sections.extend(f"{index}. {rule}" for index, rule in enumerate(USE_POLICY, start=1))

    constraint_text = _constraints_block(constraints)
    if constraint_text:
        sections.extend(["", constraint_text])

    if references:
        sections.extend(["", "【参考场景】"])
        rendered = [
            render_reference(**reference) for reference in references
        ]
        sections.append("\n\n".join(rendered))
    else:
        reason = empty_reason or "没有检索到足够相关的写作范本"
        sections.extend([
            "",
            "【参考场景】",
            f"（{reason}。请仅根据写作任务与已有大纲写作，不要虚构参考。）",
        ])

    sections.extend(["", "【输出要求】", OUTPUT_POLICY])
    return "\n".join(sections)


def wrapper_chars(
    *, task: str, constraints: dict[str, Any] | None = None,
) -> int:
    """Length of the prompt without any reference block."""

    return len(render_prompt(task=task, references=[], constraints=constraints))
