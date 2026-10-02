from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from math import exp, isfinite

import httpx

from app.utils.errors import StorageError


_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements '
    'based on the Query and the Instruct provided. Note that the answer can '
    'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
_INSTRUCTION = 'Given a writing request, retrieve novel scenes useful as writing references'


class RerankClient:
    """Score query/scene pairs with Qwen3-Reranker through Ollama logprobs."""

    def __init__(
        self, *, base_url: str, model: str, timeout_seconds: float,
        concurrency: int = 1,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.concurrency = concurrency
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _score(self, query: str, document: str) -> tuple[float, bool]:
        prompt = (
            f"{_PREFIX}<Instruct>: {_INSTRUCTION}\n"
            f"<Query>: {query}\n<Document>: {document}{_SUFFIX}"
        )
        payload = {
            "model": self.model, "prompt": prompt, "raw": True,
            "stream": False, "logprobs": True, "top_logprobs": 20,
            "options": {"temperature": 0, "num_predict": 1},
        }
        for attempt in range(2):
            try:
                response = self.client.post("/api/generate", json=payload)
                response.raise_for_status()
                break
            except httpx.HTTPError as exc:
                retryable = isinstance(exc, httpx.TransportError) or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in {429, 500, 502, 503, 504}
                )
                if attempt or not retryable:
                    raise StorageError(
                        f"Ollama rerank failed ({type(exc).__name__}): {exc}"
                    ) from exc
        try:
            alternatives = response.json()["logprobs"][0]["top_logprobs"]
            logprobs = {
                item["token"]: float(item["logprob"])
                for item in alternatives if item["token"] in ("yes", "no")
            }
            if not logprobs or not all(
                isfinite(value) for value in logprobs.values()
            ):
                raise ValueError("Ollama did not return a yes/no token probability")
            # Ollama returns at most 20 alternatives. A missing answer is below
            # that cutoff, so give it a conservative boundary score and retain
            # the original candidate order when several scores are censored.
            if "yes" not in logprobs:
                return 0.0, False
            if "no" not in logprobs:
                return 1.0, False
            if logprobs["yes"] >= logprobs["no"]:
                return 1.0 / (1.0 + exp(logprobs["no"] - logprobs["yes"])), True
            ratio = exp(logprobs["yes"] - logprobs["no"])
            return ratio / (1.0 + ratio), True
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise StorageError(
                f"Ollama rerank failed ({type(exc).__name__}): {exc}"
            ) from exc

    def rerank(self, query: str, documents: list[str]) -> list[dict[str, float | int | bool]]:
        if not documents:
            return []
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(documents))) as pool:
            scores = list(pool.map(
                lambda document: self._score(query, document), documents,
            ))
        return [
            {"index": index, "score": score, "score_exact": exact}
            for index, (score, exact) in enumerate(scores)
        ]
