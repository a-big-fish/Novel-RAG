from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from math import exp, isfinite
from time import perf_counter
from uuid import uuid4

import httpx

from app.utils.errors import StorageError

logger = logging.getLogger(__name__)

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
        self.batch_id = uuid4().hex[:12]
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _score(self, query: str, document: str, index: int) -> tuple[float, bool]:
        prompt = (
            f"{_PREFIX}<Instruct>: {_INSTRUCTION}\n"
            f"<Query>: {query}\n<Document>: {document}{_SUFFIX}"
        )
        payload = {
            "model": self.model, "prompt": prompt, "raw": True,
            "stream": False, "logprobs": True, "top_logprobs": 20,
            "options": {"temperature": 0, "num_predict": 1},
        }
        logger.info("rerank_candidate_started", extra={
            "batch_id": self.batch_id, "candidate_index": index, "model": self.model,
            "document_chars": len(document), "prompt_chars": len(prompt),
        })
        for attempt in range(2):
            request_started = perf_counter()
            try:
                response = self.client.post("/api/generate", json=payload)
                response.raise_for_status()
                logger.info("rerank_request_succeeded", extra={
                    "batch_id": self.batch_id, "candidate_index": index,
                    "attempt": attempt + 1,
                    "status_code": response.status_code,
                    "latency_ms": round((perf_counter() - request_started) * 1000, 2),
                })
                break
            except httpx.HTTPError as exc:
                retryable = isinstance(exc, httpx.TransportError) or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in {429, 500, 502, 503, 504}
                )
                error_fields = {
                    "batch_id": self.batch_id, "candidate_index": index,
                    "attempt": attempt + 1,
                    "latency_ms": round((perf_counter() - request_started) * 1000, 2),
                    "error_type": type(exc).__name__, "error": str(exc),
                    "status_code": exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
                    "response_body": exc.response.text[:500] if isinstance(exc, httpx.HTTPStatusError) else None,
                    "will_retry": retryable and attempt == 0,
                }
                logger.warning("rerank_request_failed", extra=error_fields)
                if attempt or not retryable:
                    raise StorageError(
                        f"Ollama rerank candidate {index + 1} failed "
                        f"({type(exc).__name__}): {exc}"
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
                score, exact = 0.0, False
            elif "no" not in logprobs:
                score, exact = 1.0, False
            elif logprobs["yes"] >= logprobs["no"]:
                score, exact = 1.0 / (1.0 + exp(logprobs["no"] - logprobs["yes"])), True
            else:
                ratio = exp(logprobs["yes"] - logprobs["no"])
                score, exact = ratio / (1.0 + ratio), True
            logger.info("rerank_candidate_scored", extra={
                "batch_id": self.batch_id, "candidate_index": index,
                "attempts": attempt + 1,
                "score": round(score, 6), "score_exact": exact,
                "returned_tokens": [item.get("token") for item in alternatives[:20]],
            })
            return score, exact
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("rerank_response_invalid", extra={
                "batch_id": self.batch_id, "candidate_index": index,
                "attempts": attempt + 1,
                "error_type": type(exc).__name__, "error": str(exc),
                "response_body": response.text[:500],
            })
            raise StorageError(
                f"Ollama rerank candidate {index + 1} failed "
                f"({type(exc).__name__}): {exc}"
            ) from exc

    def rerank(self, query: str, documents: list[str]) -> list[dict[str, float | int | bool]]:
        if not documents:
            return []
        logger.info("rerank_batch_started", extra={
            "batch_id": self.batch_id, "model": self.model,
            "candidate_count": len(documents),
            "concurrency": self.concurrency,
        })
        if self.concurrency == 1:
            scores = [
                self._score(query, document, index)
                for index, document in enumerate(documents)
            ]
        else:
            with ThreadPoolExecutor(max_workers=min(self.concurrency, len(documents))) as pool:
                scores = list(pool.map(
                    lambda pair: self._score(query, pair[1], pair[0]), enumerate(documents),
                ))
        logger.info("rerank_batch_completed", extra={
            "batch_id": self.batch_id, "model": self.model,
            "candidate_count": len(scores),
            "censored_count": sum(not exact for _, exact in scores),
        })
        return [
            {"index": index, "score": score, "score_exact": exact}
            for index, (score, exact) in enumerate(scores)
        ]
