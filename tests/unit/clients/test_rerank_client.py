import json

import httpx
import pytest

from app.clients.rerank_client import RerankClient
from app.utils.errors import StorageError


def response_with_logits(yes: float, no: float) -> httpx.Response:
    return httpx.Response(200, json={"response": "yes" if yes > no else "no", "logprobs": [{
        "top_logprobs": [
            {"token": "yes", "logprob": yes},
            {"token": "no", "logprob": no},
        ],
    }]})


def test_rerank_client_uses_ollama_model_and_yes_no_probabilities():
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        return response_with_logits(-0.1, -3.0) if "场景甲" in payload["prompt"] else response_with_logits(-3.0, -0.1)

    client = httpx.Client(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle),
    )
    reranker = RerankClient(
        base_url="http://ollama.test", model="my-ollama-reranker",
        timeout_seconds=3, client=client,
    )
    result = reranker.rerank("雨夜", ["场景甲", "场景乙"])
    assert [item["index"] for item in result] == [0, 1]
    assert result[0]["score"] > result[1]["score"]
    assert len(requests) == 2
    assert all(request.url.path == "/api/generate" for request, _ in requests)
    assert all(payload["model"] == "my-ollama-reranker" for _, payload in requests)
    assert all(payload["raw"] and payload["logprobs"] for _, payload in requests)
    assert all("雨夜" in payload["prompt"] for _, payload in requests)
    client.close()


def test_rerank_client_censors_a_missing_no_probability():
    client = httpx.Client(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(
            200, json={"logprobs": [{"top_logprobs": [
                {"token": "yes", "logprob": -0.1},
            ]}]},
        )),
    )
    reranker = RerankClient(
        base_url="http://ollama.test", model="my-ollama-reranker",
        timeout_seconds=3, client=client,
    )
    assert reranker.rerank("雨夜", ["场景甲"]) == [
        {"index": 0, "score": 1.0, "score_exact": False},
    ]
    client.close()


def test_rerank_client_rejects_response_without_yes_or_no():
    client = httpx.Client(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(
            200, json={"logprobs": [{"top_logprobs": [
                {"token": "maybe", "logprob": -0.1},
            ]}]},
        )),
    )
    reranker = RerankClient(
        base_url="http://ollama.test", model="my-ollama-reranker",
        timeout_seconds=3, client=client,
    )
    with pytest.raises(StorageError, match="yes/no token probability"):
        reranker.rerank("雨夜", ["场景甲"])
    client.close()


def test_rerank_client_retries_transient_timeout_once():
    attempts = 0

    def handle(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("model loading")
        return response_with_logits(-0.1, -3.0)

    client = httpx.Client(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle),
    )
    reranker = RerankClient(
        base_url="http://ollama.test", model="my-ollama-reranker",
        timeout_seconds=3, client=client,
    )
    assert reranker.rerank("雨夜", ["场景甲"])[0]["score"] > 0.9
    assert attempts == 2
    client.close()


def test_rerank_client_reports_persistent_timeout_type():
    attempts = 0

    def handle(_request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("model loading")

    client = httpx.Client(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle),
    )
    reranker = RerankClient(
        base_url="http://ollama.test", model="my-ollama-reranker",
        timeout_seconds=3, client=client,
    )
    with pytest.raises(StorageError, match="ReadTimeout.*model loading"):
        reranker.rerank("雨夜", ["场景甲"])
    assert attempts == 2
    client.close()
