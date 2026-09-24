from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.clients.llm_client import JsonLLMClient
from app.config import Settings, get_settings
from app.db.postgres import PostgresRepository
from app.prompts.reference_evaluation import v1 as reference_prompt
from app.services.schema import ReferenceEvaluation
from app.utils.text import bounded_sample

logger = logging.getLogger(__name__)


class ReferenceEvaluator:
    """Lightweight admission gate between final scenes and deep annotation."""

    def __init__(
        self,
        *,
        repository: PostgresRepository,
        llm_client: JsonLLMClient,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.llm_client = llm_client
        self.settings = settings or get_settings()

    def _bounded_scene_text(self, text: str) -> str:
        return bounded_sample(
            text,
            head_chars=self.settings.long_text_head_chars,
            middle_chars=self.settings.long_text_middle_chars,
            tail_chars=self.settings.long_text_tail_chars,
            max_chars=self.settings.max_llm_input_chars,
        )

    def evaluate_scene(self, scene: dict[str, Any]) -> str:
        scene_id = int(scene["id"])
        final_text = self._bounded_scene_text(str(scene["text"]))
        input_hash = self.repository.reference_evaluation_cache_key(
            model=self.llm_client.model,
            prompt_version=reference_prompt.PROMPT_VERSION,
            rule_version=self.settings.reference_rule_version,
            input_text=final_text,
        )
        self.repository.update_scene(
            scene_id,
            reference_status="evaluating",
            error_message=None,
        )

        try:
            cached = self.repository.get_reference_evaluation_cache(input_hash)
            if cached is not None:
                evaluation = ReferenceEvaluation.model_validate(
                    cached["output_json"]
                )
            else:
                user_prompt = reference_prompt.build_user_prompt(
                    scene_text=final_text,
                    chapter_start_index=int(scene["chapter_start_index"]),
                    chapter_end_index=int(scene["chapter_end_index"]),
                    is_cross_chapter=bool(scene.get("is_cross_chapter", False)),
                )
                evaluation = self.llm_client.request_typed(
                    system_prompt=reference_prompt.SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    response_model=ReferenceEvaluation,
                )
                self.repository.put_reference_evaluation_cache(
                    input_hash=input_hash,
                    model=self.llm_client.model,
                    prompt_version=reference_prompt.PROMPT_VERSION,
                    rule_version=self.settings.reference_rule_version,
                    input_text=final_text,
                    output_json=evaluation.model_dump(),
                )

            values: dict[str, Any] = {
                "reference_status": evaluation.reference_status,
                "reference_score": evaluation.reference_score,
                "reference_reason": evaluation.reference_reason,
                "reference_prompt_version": reference_prompt.PROMPT_VERSION,
                "reference_rule_version": self.settings.reference_rule_version,
                "reference_meta_json": evaluation.model_dump(),
                "error_message": None,
            }
            if evaluation.reference_status == "selected":
                values.update(
                    annotate_status="pending",
                    index_status="pending",
                )
            else:
                values.update(
                    summary="",
                    style_summary="",
                    usage_hint="",
                    scene_type=[],
                    scene_type_display=[],
                    technique=[],
                    technique_display=[],
                    style_tags=[],
                    style_tags_display=[],
                    emotion_tags=[],
                    emotion_tags_display=[],
                    key_images=[],
                    key_images_display=[],
                    narrative_func="",
                    meta_json={},
                    annotate_status="not_applicable",
                    index_status="not_applicable",
                )
            self.repository.update_scene(scene_id, **values)
            logger.debug(
                "reference scene evaluated",
                extra={
                    "scene_id": scene_id,
                    "reference_status": evaluation.reference_status,
                    "reference_score": evaluation.reference_score,
                },
            )
            return evaluation.reference_status
        except Exception as exc:
            self.repository.update_scene(
                scene_id,
                reference_status="evaluation_failed",
                reference_prompt_version=reference_prompt.PROMPT_VERSION,
                reference_rule_version=self.settings.reference_rule_version,
                error_message=str(exc)[:2000],
            )
            logger.warning(
                "reference evaluation failed",
                extra={"scene_id": scene_id, "error": str(exc)},
            )
            return "evaluation_failed"

    def evaluate_book(
        self,
        book_id: int,
        version: int,
        *,
        max_workers: int | None = None,
        limit: int | None = None,
    ) -> dict[str, int]:
        started = time.perf_counter()
        rows = self.repository.list_scenes(
            book_id,
            version,
            reference_status="unevaluated",
        )
        if limit is not None:
            rows = rows[:limit]
        scenes_input = [dict(row) for row in rows]
        stats = {
            "total": len(scenes_input),
            "selected": 0,
            "archived": 0,
            "evaluation_failed": 0,
        }
        logger.info(
            "reference_evaluation_started",
            extra={
                "book_id": book_id,
                "version": version,
                "total_scenes": len(scenes_input),
                "model": self.llm_client.model,
                "reference_rule_version": self.settings.reference_rule_version,
            },
        )

        workers = max_workers or self.settings.llm_concurrency
        if workers <= 1:
            for scene in scenes_input:
                stats[self.evaluate_scene(scene)] += 1
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(self.evaluate_scene, scene)
                    for scene in scenes_input
                ]
                for future in as_completed(futures):
                    stats[future.result()] += 1

        selection_rate = (
            stats["selected"] / stats["total"] if stats["total"] else 0.0
        )
        logger.info(
            "reference_evaluation_completed",
            extra={
                "book_id": book_id,
                "version": version,
                "total_scenes": stats["total"],
                "selected": stats["selected"],
                "archived": stats["archived"],
                "failed": stats["evaluation_failed"],
                "selection_rate": round(selection_rate, 4),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "model": self.llm_client.model,
                "reference_rule_version": self.settings.reference_rule_version,
            },
        )
        return stats
