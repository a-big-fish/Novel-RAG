from __future__ import annotations

from math import isfinite

import httpx

from app.utils.errors import StorageError


class RerankClient:
    """Client for a Jina/Cohere-compatible /v1/rerank scoring endpoint."""

    def __init__(
        self, *, url: str, model: str, timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url
        self.model = model
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def rerank(self, query: str, documents: list[str]) -> list[dict[str, float | int]]:
        if not documents:
            return []
        try:
            response = self.client.post(
                self.url,
                json={
                    "model": self.model, "query": query,
                    "documents": documents, "top_n": len(documents),
                },
            )
            response.raise_for_status()
            results = response.json()["results"]
            if not isinstance(results, list) or len(results) != len(documents):
                raise ValueError("rerank result count does not match candidates")
            normalized: list[dict[str, float | int]] = []
            seen: set[int] = set()
            for item in results:
                index = int(item["index"])
                score = float(item["relevance_score"])
                if (index < 0 or index >= len(documents)
                        or index in seen or not isfinite(score)):
                    raise ValueError("rerank returned an invalid index or score")
                seen.add(index)
                normalized.append({"index": index, "score": score})
            return normalized
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise StorageError(f"rerank service failed: {exc}") from exc
