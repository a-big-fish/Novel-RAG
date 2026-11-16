"""Extract concrete, scene-specific style signatures for meta-vector rebuilds."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.clients.llm_client import JsonLLMClient
from app.config import Settings, get_settings
from app.db.postgres import PostgresRepository
from app.prompts.style_signature import v1 as signature_prompt
from app.services.schema import StyleSignature
from app.utils.text import bounded_sample

logger = logging.getLogger(__name__)


class StyleSigner:
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

    def _bounded_text(self, text: str) -> str:
        return bounded_sample(
            text,
            head_chars=self.settings.long_text_head_chars,
            middle_chars=self.settings.long_text_middle_chars,
            tail_chars=self.settings.long_text_tail_chars,
            max_chars=self.settings.max_llm_input_chars,
        )

    @staticmethod
    def _meta(scene: dict[str, Any]) -> dict[str, Any]:
        meta = scene.get("meta_json")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        return dict(meta) if isinstance(meta, dict) else {}

    def sign_scene(
        self, scene: dict[str, Any], *, force: bool = False,
    ) -> dict[str, Any] | None:
        """Return the style signature, calling the LLM only when missing."""

        scene_id = int(scene["id"])
        meta = self._meta(scene)
        existing = meta.get("style_v1")
        if existing and not force:
            return dict(existing)
        try:
            signature = self.llm_client.request_typed(
                system_prompt=signature_prompt.SYSTEM_PROMPT,
                user_prompt=signature_prompt.build_user_prompt(
                    scene_text=self._bounded_text(str(scene.get("text") or "")),
                    summary=str(scene.get("summary") or ""),
                    style_summary=str(scene.get("style_summary") or ""),
                    usage_hint=str(scene.get("usage_hint") or ""),
                    technique=list(scene.get("technique") or []),
                    key_images=list(scene.get("key_images") or []),
                ),
                response_model=StyleSignature,
            )
            value = signature.model_dump()
            meta["style_v1"] = value
            self.repository.update_scene(scene_id, meta_json=meta)
            return value
        except Exception as exc:
            logger.warning(
                "style signature failed",
                extra={"scene_id": scene_id, "error": str(exc)[:500]},
            )
            return None

    def sign_book(
        self,
        book_id: int,
        version: int,
        *,
        limit: int | None = None,
        workers: int | None = None,
        force: bool = False,
    ) -> dict[str, int]:
        rows = [
            dict(row) for row in self.repository.list_scenes(
                book_id, version,
                reference_status="selected", annotate_status="annotated",
            )
        ]
        if limit is not None:
            rows = rows[:limit]
        stats = {"total": len(rows), "signed": 0, "skipped": 0, "failed": 0}
        if not rows:
            return stats
        workers = workers or self.settings.llm_concurrency
        if workers <= 1:
            for scene in rows:
                if self._meta(scene).get("style_v1") and not force:
                    stats["skipped"] += 1
                    continue
                stats["signed" if self.sign_scene(scene, force=force) else "failed"] += 1
            return stats

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.sign_scene, scene, force=force): scene
                       for scene in rows}
            for future in as_completed(futures):
                scene = futures[future]
                if self._meta(scene).get("style_v1") and not force:
                    stats["skipped"] += 1
                    continue
                stats["signed" if future.result() else "failed"] += 1
        return stats
