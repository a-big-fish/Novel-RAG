from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services.schema import StyleSignature
from app.services.style_signature import StyleSigner


class FakeLLM:
    model = "fake-llm"

    def __init__(self) -> None:
        self.calls = 0

    def request_typed(self, **kwargs: Any) -> StyleSignature:
        self.calls += 1
        return StyleSignature(
            style_signature=(
                "用磁带机与烤红薯串联两代人的记忆，短句突转暴力，结尾留白不解释。"
                "信息按时间线跳跃释放，靠物件而非抒情完成情绪转场。"
            ),
            distinctive_devices=["磁带机作为线索物件", "短句突转暴力"],
            retrieval_hooks=["磁带机串联", "短句突转"],
        )


class FakeRepository:
    def __init__(self) -> None:
        self.updated: dict[str, Any] = {}

    def update_scene(self, scene_id: int, **values: Any) -> None:
        self.updated = {"scene_id": scene_id, **values}


def scene(meta_json: dict | None = None) -> dict[str, Any]:
    return {
        "id": 7, "text": "正文" * 100, "summary": "摘要",
        "style_summary": "风格", "usage_hint": "提示",
        "technique": ["foreshadow"], "key_images": ["磁带"],
        "meta_json": meta_json or {},
    }


def test_sign_scene_stores_signature_and_reuses_it():
    repository = FakeRepository()
    llm = FakeLLM()
    signer = StyleSigner(repository=repository, llm_client=llm, settings=Settings())
    value = signer.sign_scene(scene())
    assert value["style_signature"].startswith("用磁带机")
    assert repository.updated["meta_json"]["style_v1"]["retrieval_hooks"] == [
        "磁带机串联", "短句突转",
    ]
    signer.sign_scene(scene({"style_v1": value}))
    assert llm.calls == 1
