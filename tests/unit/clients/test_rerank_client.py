import httpx
import pytest

from app.clients.rerank_client import RerankClient
from app.utils.errors import StorageError


def test_rerank_client_sends_one_batch_and_validates_indexes():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.2},
        ]})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    reranker = RerankClient(
        url="http://reranker.test/v1/rerank", model="fake",
        timeout_seconds=3, client=client,
    )
    result = reranker.rerank("雨夜", ["场景甲", "场景乙"])
    assert result == [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}]
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/rerank"
    assert requests[0].read().decode("utf-8").count("场景") == 2
    client.close()


def test_rerank_client_rejects_missing_candidate_score():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"results": [
            {"index": 0, "relevance_score": 0.5},
        ]}),
    ))
    reranker = RerankClient(
        url="http://reranker.test/v1/rerank", model="fake",
        timeout_seconds=3, client=client,
    )
    with pytest.raises(StorageError, match="result count"):
        reranker.rerank("雨夜", ["场景甲", "场景乙"])
    client.close()
