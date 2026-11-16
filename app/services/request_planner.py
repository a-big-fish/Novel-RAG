"""Turn a writing brief into a retrieval query.

The consuming agent usually knows the scene it wants to write, not the retrieval
vocabulary. This planner keeps the caller's own words as the anchor and folds
in a bounded outline and explicit constraints; downstream query parsing turns
the result into canonical tags.
"""

from __future__ import annotations

from typing import Any

OUTLINE_LIMIT_CHARS = 900


def _format_constraints(constraints: dict[str, Any] | None) -> str:
    if not constraints:
        return ""
    parts: list[str] = []
    for key, value in constraints.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple, set)):
            text = "、".join(str(item) for item in value)
        else:
            text = str(value)
        parts.append(f"{key}：{text}")
    return "；".join(parts)


def plan_writing_query(
    *,
    writing_goal: str,
    outline_context: str = "",
    constraints: dict[str, Any] | None = None,
    max_chars: int = 2000,
) -> dict[str, Any]:
    """Compose a bounded natural-language query from a writing brief.

    The writing goal is never dropped. Outline and constraints fill the
    remaining budget and report whether they were truncated.
    """

    goal = writing_goal.strip()
    if not goal:
        raise ValueError("writing_goal must not be empty")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    query = goal[:max_chars]
    truncated = len(goal) > max_chars
    outline = " ".join(outline_context.split())
    outline_used = 0
    if outline and len(query) < max_chars:
        room = max_chars - len(query) - len("\n情节上下文：")
        if room > 40:
            outline_part = outline[: min(len(outline), OUTLINE_LIMIT_CHARS, room)]
            query = f"{query}\n情节上下文：{outline_part}"
            outline_used = len(outline_part)
            truncated = truncated or len(outline_part) < len(outline)

    constraint_text = _format_constraints(constraints)
    if constraint_text and len(query) < max_chars:
        room = max_chars - len(query) - len("\n写作约束：")
        if room > 20:
            constraint_part = constraint_text[:room]
            query = f"{query}\n写作约束：{constraint_part}"
            truncated = truncated or len(constraint_part) < len(constraint_text)

    return {
        "query": query,
        "char_count": len(query),
        "truncated": truncated,
        "outline_chars": outline_used,
        "has_constraints": bool(constraint_text),
    }
