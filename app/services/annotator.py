from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.clients.llm_client import JsonLLMClient
from app.config import Settings, get_settings
from app.db.postgres import PostgresRepository
from app.prompts.annotation import v1 as annotation_prompt
from app.services.schema import SceneAnnotation
from app.services.tagger import TagVocabulary
from app.utils.text import bounded_sample

logger = logging.getLogger(__name__)


class Annotator:
    def __init__(
        self,
        *,
        repository: PostgresRepository,
        llm_client: JsonLLMClient,
        vocabulary: TagVocabulary,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.llm_client = llm_client
        self.vocabulary = vocabulary
        self.settings = settings or get_settings()

    def _bounded_scene_text(self, text: str) -> str:
        return bounded_sample(
            text,
            head_chars=self.settings.long_text_head_chars,
            middle_chars=self.settings.long_text_middle_chars,
            tail_chars=self.settings.long_text_tail_chars,
            max_chars=self.settings.max_llm_input_chars,
        )

    def annotate_scene(self, scene: dict[str, Any]) -> str:
        """Annotate one scene and return its final status."""

        scene_id = int(scene["id"])
        if scene.get("reference_status") != "selected":
            raise ValueError(
                "deep annotation requires reference_status=selected: "
                f"scene_id={scene_id}"
            )
        final_text = self._bounded_scene_text(str(scene["text"]))
        input_hash = self.repository.annotation_cache_key(
            model=self.llm_client.model,
            prompt_version=annotation_prompt.PROMPT_VERSION,
            tag_vocab_version=self.settings.tag_vocab_version,
            input_text=final_text,
        )
        self.repository.update_scene(scene_id, annotate_status="running")

        try:
            cached = self.repository.get_annotation_cache(input_hash)
            if cached is not None:
                annotation = SceneAnnotation.model_validate(cached["output_json"])
            else:
                user_prompt = annotation_prompt.build_user_prompt(
                    scene_text=final_text,
                    chapter_start_index=int(scene["chapter_start_index"]),
                    chapter_end_index=int(scene["chapter_end_index"]),
                    vocabulary=self.vocabulary,
                )
                annotation = self.llm_client.request_typed(
                    system_prompt=annotation_prompt.SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    response_model=SceneAnnotation,
                )
                self.repository.put_annotation_cache(
                    input_hash=input_hash,
                    model=self.llm_client.model,
                    prompt_version=annotation_prompt.PROMPT_VERSION,
                    tag_vocab_version=self.settings.tag_vocab_version,
                    input_text=final_text,
                    output_json=annotation.model_dump(),
                )

            mapped = self.vocabulary.map_annotation(annotation)
            self.repository.update_scene(
                scene_id,
                summary=mapped.summary,
                style_summary=mapped.style_summary,
                usage_hint=mapped.usage_hint,
                scene_type=mapped.scene_type,
                scene_type_display=mapped.scene_type_display,
                technique=mapped.technique,
                technique_display=mapped.technique_display,
                style_tags=mapped.style_tags,
                style_tags_display=mapped.style_tags_display,
                emotion_tags=mapped.emotion_tags,
                emotion_tags_display=mapped.emotion_tags_display,
                key_images=mapped.key_images,
                key_images_display=mapped.key_images_display,
                narrative_func=mapped.narrative_func,
                meta_json=mapped.meta_json,
                annotate_status="annotated",
                error_message=None,
            )
            return "annotated"
        except Exception as exc:
            self.repository.update_scene(
                scene_id,
                annotate_status="failed_permanent",
                error_message=str(exc)[:2000],
            )
            logger.warning(
                "scene annotation failed",
                extra={"scene_id": scene_id, "error": str(exc)},
            )
            return "failed_permanent"

    def annotate_book(
        self,
        book_id: int,
        version: int,
        *,
        max_workers: int | None = None,
        limit: int | None = None,
    ) -> dict[str, int]:
        rows = self.repository.list_scenes(
            book_id,
            version,
            annotate_status="pending",
            reference_status="selected",
        )
        if limit is not None:
            rows = rows[:limit]
        scenes_input = [dict(row) for row in rows]
        stats = {"total": len(scenes_input), "annotated": 0, "failed_permanent": 0}
        if not scenes_input:
            return stats

        workers = max_workers or self.settings.llm_concurrency
        if workers <= 1:
            for scene in scenes_input:
                stats[self.annotate_scene(scene)] += 1
            return stats

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.annotate_scene, scene): int(scene["id"])
                for scene in scenes_input
            }
            for future in as_completed(futures):
                stats[future.result()] += 1
        return stats
