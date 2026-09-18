from __future__ import annotations

from collections.abc import Sequence

import httpx

from app.config import Settings, get_settings
from app.utils.errors import ModelInputTooLargeError, StorageError


class OllamaClient:
    """Synchronous Ollama embedding client."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.Client | None = None,
        expected_dimension: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = (base_url or self.settings.ollama_url).rstrip("/")
        self.model = model or self.settings.ollama_embed_model
        self.expected_dimension = (
            expected_dimension or self.settings.embedding_dimension
        )
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds or self.settings.ollama_timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def ping(self) -> None:
        try:
            response = self.client.get("/api/tags")
            response.raise_for_status()
        except Exception as exc:
            raise StorageError(f"Ollama unavailable: {exc}") from exc

    def embed(
        self,
        texts: Sequence[str],
        *,
        max_input_chars: int | None = None,
    ) -> list[list[float]]:
        normalized = [str(item) for item in texts]
        if not normalized:
            return []
        limit = max_input_chars or self.settings.max_embed_input_chars
        oversized = [len(item) for item in normalized if len(item) > limit]
        if oversized:
            raise ModelInputTooLargeError(
                f"embedding input exceeds {limit} chars: {max(oversized)}"
            )

        try:
            response = self.client.post(
                "/api/embed",
                json={"model": self.model, "input": normalized},
            )
            response.raise_for_status()
            payload = response.json()
        except ModelInputTooLargeError:
            raise
        except Exception as exc:
            raise StorageError(f"Ollama embedding failed: {exc}") from exc

        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(normalized):
            raise StorageError("Ollama returned an invalid embeddings payload")

        result: list[list[float]] = []
        for embedding in embeddings:
            if not isinstance(embedding, list) or not embedding:
                raise StorageError("Ollama returned an empty embedding")
            if len(embedding) != self.expected_dimension:
                raise StorageError(
                    "embedding dimension mismatch: "
                    f"expected={self.expected_dimension}, got={len(embedding)}"
                )
            result.append([float(value) for value in embedding])
        return result
