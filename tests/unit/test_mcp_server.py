from __future__ import annotations

from pathlib import Path

import app.mcp_server as mcp_server
from app.mcp_guide import USAGE_GUIDE


async def test_mcp_registers_core_tools():
    tools = {tool.name for tool in await mcp_server.mcp.list_tools()}
    assert {
        "search_writing_references", "plan_writing_query",
        "build_reference_prompt", "get_scene", "list_books", "get_usage_guide",
    } <= tools


def test_usage_guide_is_exposed_as_tool_and_matches_skill():
    assert mcp_server.get_usage_guide() == USAGE_GUIDE
    skill = Path("skills/novel-rag-writing/SKILL.md").read_text(encoding="utf-8")
    assert "search_writing_references" in skill
    assert "不要自己拼标签" in skill
    assert "search_writing_references" in USAGE_GUIDE
    assert "不要自己拼标签" in USAGE_GUIDE


def test_plan_tool_only_builds_query():
    plan = mcp_server.plan_writing_query(
        writing_goal="写一段雨夜追杀",
        constraints={"情绪": "紧张"},
    )
    assert plan["query"].startswith("写一段雨夜追杀")
    assert "写作约束：" in plan["query"]
