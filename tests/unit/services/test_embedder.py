from __future__ import annotations

from typing import Any

from app.services.embedder import Embedder, build_meta_text


class FakeRepository:
    def __init__(self) -> None:
        self.cache: dict[str, list[float]] = {}

    def embedding_cache_key(self, **kwargs: Any) -> str:
        return kwargs["input_text"]

    def get_embedding_cache(self, input_hash: str) -> dict[str, Any] | None:
        if input_hash in self.cache:
            return {"vector": self.cache[input_hash]}
        return None

    def put_embedding_cache(self, input_hash: str, **kwargs: Any) -> None:
        self.cache[input_hash] = kwargs["vector"]


class FakeOllama:
    model = "fake-embed"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[float(len(text)), 1.0] for text in texts]


def test_build_meta_text_excludes_summary() -> None:
    text = build_meta_text(
        {
            "summary": "不应出现",
            "style_summary": "风格",
            "scene_type": ["dialogue_conflict"],
        }
    )
    assert "不应出现" not in text
    assert "风格" in text


def test_embed_scene_returns_three_dense_vectors() -> None:
    repository = FakeRepository()
    ollama = FakeOllama()
    embedder = Embedder(repository=repository, ollama_client=ollama)
    vectors = embedder.embed_scene(
        {
            "text": "正文",
            "summary": "摘要",
            "style_summary": "风格",
            "usage_hint": "用途",
            "scene_type": [],
            "technique": [],
            "style_tags": [],
            "emotion_tags": [],
            "key_images": [],
            "narrative_func": "advance",
        }
    )
    assert set(vectors) == {"text-dense", "meta-dense", "summary-dense"}
    assert ollama.calls == 3
