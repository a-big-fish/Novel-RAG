from __future__ import annotations

from typing import Any

from app.clients.ollama_client import OllamaClient
from app.config import Settings, get_settings
from app.db.postgres import PostgresRepository
from app.utils.text import bounded_sample


def build_meta_text(scene: dict[str, Any]) -> str:
    def joined(name: str) -> str:
        value = scene.get(name) or []
        return "、".join(str(item) for item in value)

    return "\n".join(
        (
            f"风格摘要：{scene.get('style_summary', '')}",
            f"使用提示：{scene.get('usage_hint', '')}",
            f"场景类型：{joined('scene_type')}",
            f"写作技法：{joined('technique')}",
            f"文风：{joined('style_tags')}",
            f"情绪：{joined('emotion_tags')}",
            f"意象：{joined('key_images')}",
            f"叙事功能：{scene.get('narrative_func', '')}",
        )
    )


class Embedder:
    def __init__(
        self,
        *,
        repository: PostgresRepository,
        ollama_client: OllamaClient,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.ollama_client = ollama_client
        self.settings = settings or get_settings()

    def _sample_text(self, text: str) -> str:
        return bounded_sample(
            text,
            head_chars=self.settings.long_text_head_chars,
            middle_chars=self.settings.long_text_middle_chars,
            tail_chars=self.settings.long_text_tail_chars,
            max_chars=self.settings.max_embed_input_chars,
        )

    def embed_cached(self, text: str) -> list[float]:
        input_hash = self.repository.embedding_cache_key(
            model=self.ollama_client.model,
            input_text=text,
        )
        cached = self.repository.get_embedding_cache(input_hash)
        if cached is not None:
            return [float(value) for value in cached["vector"]]

        vector = self.ollama_client.embed([text])[0]
        self.repository.put_embedding_cache(
            input_hash=input_hash,
            model=self.ollama_client.model,
            input_text=text,
            vector=vector,
        )
        return vector

    def embed_scene(self, scene: dict[str, Any]) -> dict[str, list[float]]:
        sample_text = self._sample_text(str(scene["text"]))
        meta_text = build_meta_text(scene)
        summary = str(scene.get("summary") or "").strip()
        if not summary:
            summary = sample_text[:200]

        return {
            "text-dense": self.embed_cached(sample_text),
            "meta-dense": self.embed_cached(meta_text),
            "summary-dense": self.embed_cached(summary),
        }
