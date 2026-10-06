from __future__ import annotations

import httpx
import pytest
import respx

from app.clients.ollama_client import OllamaClient
from app.utils.errors import ModelInputTooLargeError, StorageError


@respx.mock
def test_ollama_embed_batch() -> None:
    route = respx.post("http://ollama.test/api/embed").mock(
        return_value=httpx.Response(
            200,
            json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]},
        )
    )
    client = httpx.Client(base_url="http://ollama.test")
    adapter = OllamaClient(
        base_url="http://ollama.test",
        client=client,
        expected_dimension=2,
    )

    result = adapter.embed(["a", "b"])

    assert route.called
    assert result == [[0.1, 0.2], [0.3, 0.4]]


def test_ollama_rejects_oversized_input() -> None:
    client = httpx.Client(base_url="http://ollama.test")
    adapter = OllamaClient(
        base_url="http://ollama.test",
        client=client,
        expected_dimension=2,
    )
    with pytest.raises(ModelInputTooLargeError):
        adapter.embed(["12345"], max_input_chars=4)


@respx.mock
def test_ollama_rejects_wrong_dimension() -> None:
    respx.post("http://ollama.test/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1]]})
    )
    adapter = OllamaClient(
        base_url="http://ollama.test",
        client=httpx.Client(base_url="http://ollama.test"),
        expected_dimension=2,
    )
    with pytest.raises(StorageError):
        adapter.embed(["a"])


@respx.mock
def test_ollama_retries_context_overflow_with_shorter_sample() -> None:
    lengths: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        length = len(json.loads(request.content)["input"][0])
        lengths.append(length)
        if length > 200:
            return httpx.Response(
                400, json={"error": "the input length exceeds the context length"}
            )
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    respx.post("http://ollama.test/api/embed").mock(side_effect=respond)
    adapter = OllamaClient(
        base_url="http://ollama.test",
        client=httpx.Client(base_url="http://ollama.test"),
        expected_dimension=2,
    )

    assert adapter.embed(["中文测试" * 100]) == [[0.1, 0.2]]
    assert lengths[0] == 400
    assert 128 <= lengths[-1] <= 200


@respx.mock
def test_ollama_includes_non_context_error_detail() -> None:
    respx.post("http://ollama.test/api/embed").mock(
        return_value=httpx.Response(400, json={"error": "model not found"})
    )
    adapter = OllamaClient(
        base_url="http://ollama.test",
        client=httpx.Client(base_url="http://ollama.test"),
        expected_dimension=2,
    )

    with pytest.raises(StorageError, match="model not found"):
        adapter.embed(["short input"])
