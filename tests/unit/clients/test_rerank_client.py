import json
import logging

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


def test_rerank_client_retries_ollama_500_for_one_candidate(caplog):
    attempts = 0

    def handle(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, text="temporary model worker failure")
        return response_with_logits(-0.1, -3.0)

    client = httpx.Client(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle),
    )
    reranker = RerankClient(
        base_url="http://ollama.test", model="my-ollama-reranker",
        timeout_seconds=3, client=client,
    )
    with caplog.at_level(logging.INFO, logger="app.clients.rerank_client"):
        assert reranker.rerank("雨夜", ["场景甲"])[0]["score"] > 0.9
    assert attempts == 2
    failures = [record for record in caplog.records if record.msg == "rerank_request_failed"]
    assert len(failures) == 1
    assert (failures[0].candidate_index, failures[0].attempt,
            failures[0].status_code, failures[0].will_retry) == (0, 1, 500, True)
    assert failures[0].response_body == "temporary model worker failure"
    assert failures[0].batch_id == reranker.batch_id
    assert any(record.msg == "rerank_candidate_scored" for record in caplog.records)
    client.close()


def test_rerank_client_logs_persistent_500_with_candidate_index(caplog):
    attempts = 0

    def handle(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return response_with_logits(-0.1, -3.0)
        return httpx.Response(500, text="model worker ran out of memory")

    client = httpx.Client(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(handle),
    )
    reranker = RerankClient(
        base_url="http://ollama.test", model="my-ollama-reranker",
        timeout_seconds=3, client=client,
    )
    with caplog.at_level(logging.INFO, logger="app.clients.rerank_client"):
        with pytest.raises(StorageError, match="candidate 2 failed"):
            reranker.rerank("雨夜", ["场景甲", "场景乙", "场景丙"])
    failures = [record for record in caplog.records if record.msg == "rerank_request_failed"]
    assert [(record.candidate_index, record.attempt, record.will_retry)
            for record in failures] == [(1, 1, True), (1, 2, False)]
    assert all(record.response_body == "model worker ran out of memory"
               for record in failures)
    assert attempts == 3  # The third candidate is not sent after a final failure.
    client.close()
