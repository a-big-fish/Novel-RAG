from __future__ import annotations

from subprocess import CompletedProcess

import httpx
import pytest
import respx
from pydantic import BaseModel

from app.clients.llm_client import (
    CodexExecClient,
    OpenAICompatibleClient,
    extract_json_object,
)
from app.utils.errors import StorageError


class DemoResponse(BaseModel):
    summary: str


def test_extract_json_from_fence() -> None:
    assert extract_json_object('```json\n{"summary":"ok"}\n```') == {
        "summary": "ok"
    }


@respx.mock
def test_openai_compatible_request() -> None:
    route = respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"summary":"ok"}'}}]},
        )
    )
    adapter = OpenAICompatibleClient(
        base_url="http://llm.test/v1",
        api_key="secret",
        model="demo",
        client=httpx.Client(),
    )
    result = adapter.request_typed(
        system_prompt="system",
        user_prompt="user",
        response_model=DemoResponse,
    )
    assert result.summary == "ok"
    assert route.called


def test_codex_exec_request(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        assert args[0][0] == "codex"
        assert kwargs["input"]
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"summary":"from-codex"}',
            stderr="",
        )

    monkeypatch.setattr("app.clients.llm_client.subprocess.run", fake_run)
    adapter = CodexExecClient(executable="codex", model="demo")
    result = adapter.request_typed(
        system_prompt="system",
        user_prompt="user",
        response_model=DemoResponse,
    )
    assert result.summary == "from-codex"


@respx.mock
def test_openai_compatible_http_error() -> None:
    respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="boom")
    )
    adapter = OpenAICompatibleClient(
        base_url="http://llm.test/v1",
        api_key="secret",
        model="demo",
        client=httpx.Client(),
    )
    with pytest.raises(StorageError):
        adapter.request_json(system_prompt="s", user_prompt="u")
