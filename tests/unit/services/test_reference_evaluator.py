from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services.reference_evaluator import ReferenceEvaluator
from app.services.schema import ReferenceDimensions, ReferenceEvaluation


def _evaluation(status: str) -> ReferenceEvaluation:
    return ReferenceEvaluation(
        reference_status=status,
        reference_score=4.5 if status == "selected" else 1.5,
        reference_reason=(
            "对白节奏和信息延迟具有明确可迁移价值。"
            if status == "selected"
            else "主要承担普通转场与事实交代，缺少独立范本价值。"
        ),
        dimensions=ReferenceDimensions(
            prose_quality=3.5,
            technique_value=4.5 if status == "selected" else 1.0,
            scene_completeness=4.0,
            context_independence=4.0,
            distinctiveness=4.0 if status == "selected" else 1.0,
            reference_value=4.8 if status == "selected" else 1.2,
        ),
    )


class FakeLLM:
    model = "fake-reference-model"

    def __init__(
        self,
        evaluation: ReferenceEvaluation | None = None,
        error: Exception | None = None,
    ) -> None:
        self.evaluation = evaluation
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def request_typed(self, **kwargs: Any) -> ReferenceEvaluation:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.evaluation is not None
        return self.evaluation


class FakeRepository:
    def __init__(self) -> None:
        self.updates: list[tuple[int, dict[str, Any]]] = []
        self.cache: dict[str, dict[str, Any]] = {}

    def reference_evaluation_cache_key(self, **kwargs: Any) -> str:
        return "reference-" + kwargs["input_text"]

    def update_scene(self, scene_id: int, **values: Any) -> None:
        self.updates.append((scene_id, values))

    def get_reference_evaluation_cache(
        self,
        input_hash: str,
    ) -> dict[str, Any] | None:
        return self.cache.get(input_hash)

    def put_reference_evaluation_cache(
        self,
        input_hash: str,
        **kwargs: Any,
    ) -> None:
        self.cache[input_hash] = {"output_json": kwargs["output_json"]}

    def list_scenes(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


def _scene(**overrides: Any) -> dict[str, Any]:
    return {
        "id": 1,
        "text": "他们在门口停了一瞬，谁也没有先开口。",
        "chapter_start_index": 1,
        "chapter_end_index": 1,
        "is_cross_chapter": False,
    } | overrides


def _evaluator(repository: FakeRepository, llm: FakeLLM) -> ReferenceEvaluator:
    return ReferenceEvaluator(
        repository=repository,  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
        settings=Settings(_env_file=None, llm_concurrency=1),
    )


def test_transition_scene_is_archived_without_deep_pipeline_state() -> None:
    repository = FakeRepository()
    evaluator = _evaluator(repository, FakeLLM(_evaluation("archived")))

    assert evaluator.evaluate_scene(_scene()) == "archived"

    final = repository.updates[-1][1]
    assert final["reference_status"] == "archived"
    assert final["annotate_status"] == "not_applicable"
    assert final["index_status"] == "not_applicable"
    assert final["error_message"] is None


def test_high_value_scene_is_selected_for_deep_annotation() -> None:
    repository = FakeRepository()
    evaluator = _evaluator(repository, FakeLLM(_evaluation("selected")))

    assert evaluator.evaluate_scene(_scene()) == "selected"

    final = repository.updates[-1][1]
    assert final["reference_status"] == "selected"
    assert final["annotate_status"] == "pending"
    assert final["index_status"] == "pending"


def test_cross_chapter_scene_is_evaluated_once_as_one_final_scene() -> None:
    repository = FakeRepository()
    llm = FakeLLM(_evaluation("selected"))
    evaluator = _evaluator(repository, llm)

    scene = _scene(
        chapter_start_index=7,
        chapter_end_index=8,
        is_cross_chapter=True,
        text="第七章末尾的对峙。\n\n第八章开头仍在同一房间继续。",
    )
    assert evaluator.evaluate_scene(scene) == "selected"

    assert len(llm.calls) == 1
    prompt = llm.calls[0]["user_prompt"]
    assert "第 7 章至第 8 章" in prompt
    assert "是否跨章：yes" in prompt
    assert "第七章末尾" in prompt
    assert "第八章开头" in prompt


def test_evaluation_failure_is_not_archived() -> None:
    repository = FakeRepository()
    evaluator = _evaluator(
        repository,
        FakeLLM(error=RuntimeError("service unavailable")),
    )

    assert evaluator.evaluate_scene(_scene()) == "evaluation_failed"

    final = repository.updates[-1][1]
    assert final["reference_status"] == "evaluation_failed"
    assert "service unavailable" in final["error_message"]
