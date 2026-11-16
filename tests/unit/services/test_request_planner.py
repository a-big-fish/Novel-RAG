from __future__ import annotations

import pytest

from app.services.request_planner import plan_writing_query


def test_plan_keeps_goal_and_folds_outline_and_constraints():
    plan = plan_writing_query(
        writing_goal="写一段雨夜追杀的紧张场景",
        outline_context="主角刚发现盟友背叛，追兵封锁街道",
        constraints={"情绪": "紧张", "句式": "短句"},
        max_chars=2000,
    )
    assert plan["query"].startswith("写一段雨夜追杀的紧张场景")
    assert "情节上下文：" in plan["query"]
    assert "写作约束：" in plan["query"]
    assert plan["truncated"] is False


def test_plan_truncates_outline_before_goal():
    plan = plan_writing_query(
        writing_goal="写一段对话",
        outline_context="细纲" * 2000,
        max_chars=200,
    )
    assert len(plan["query"]) <= 200
    assert plan["query"].startswith("写一段对话")
    assert plan["truncated"] is True


def test_plan_rejects_empty_goal():
    with pytest.raises(ValueError):
        plan_writing_query(writing_goal="   ")
