from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_TOOLS = {
    "search_writing_references", "plan_writing_query", "build_reference_prompt",
    "get_scene", "list_books", "get_service_status", "get_usage_guide",
}


@pytest.mark.asyncio
async def test_mcp_stdio_chain() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str(REPO_ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert CORE_TOOLS <= tools

            status = await session.call_tool("get_service_status", {})
            assert status.is_error is False
            state = json.loads(status.content[0].text)
            assert state["postgres"] == "ok"
            assert state["ready_books"] >= 1

            books = await session.call_tool("list_books", {})
            assert books.is_error is False
            payload = json.loads(books.content[0].text)
            assert payload["count"] == len(payload["books"])
            assert payload["books"]

            plan = await session.call_tool("plan_writing_query", {
                "writing_goal": "写一段雨夜追杀的紧张场景",
                "constraints": {"情绪": "紧张"},
            })
            assert plan.is_error is False
            assert json.loads(plan.content[0].text)["query"].startswith(
                "写一段雨夜追杀"
            )

            missing = await session.call_tool("get_scene", {
                "book_id": 10, "version": 3, "scene_id": 99999999,
            })
            assert missing.is_error is True
            assert "scene not found" in missing.content[0].text

            search = await session.call_tool("search_writing_references", {
                "writing_goal": "写一段穷困之下铤而走险、雪夜抢劫的开场",
                "book_ids": [10],
                "budget_chars": 1200,
                "max_scenes": 1,
            })
            assert search.is_error is False
            result = json.loads(search.content[0].text)
            assert result["prompt"]["text"]
            assert result["references"]
