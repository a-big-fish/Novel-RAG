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
