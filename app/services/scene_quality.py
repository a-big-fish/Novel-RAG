from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.clients.llm_client import JsonLLMClient
from app.config import Settings, get_settings
from app.db.postgres import PostgresRepository
from app.prompts.scene_quality import v1 as quality_prompt
from app.utils.errors import NovelRagError
from app.utils.text import bounded_sample, is_chapter_title, split_paragraphs

logger = logging.getLogger(__name__)


class SceneQualityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["keep", "reject"]
    category: Literal["none", "duplicate", "garbage"]
    reason: str = Field(min_length=4, max_length=500)

    @model_validator(mode="after")
    def category_matches_decision(self) -> "SceneQualityDecision":
        if (self.decision == "keep") != (self.category == "none"):
            raise ValueError("quality decision/category mismatch")
        return self


def _paragraph_key(paragraph: str) -> str:
    return re.sub(r"\s+", "", paragraph)


def _shingles(text: str) -> set[str]:
    normalized = _paragraph_key(text[:2400])
    return {normalized[index:index + 10] for index in range(0, len(normalized) - 9, 5)}


def duplicate_evidence(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Find repeated substantive paragraphs across all final scenes in one book."""
    passages: list[dict[str, int]] = []
    owners: dict[str, set[int]] = defaultdict(set)
    shingles: list[set[str]] = []
    shingle_owners: dict[str, set[int]] = defaultdict(set)
    for position, row in enumerate(rows):
        fragments = {
            key: len(key)
            for paragraph in split_paragraphs(str(row["text"]))
            if not is_chapter_title(paragraph)
            if len(key := _paragraph_key(paragraph)) >= 20
        }
        passages.append(fragments)
        for key in fragments:
            owners[key].add(position)
        tokens = _shingles(str(row["text"]))
        shingles.append(tokens)
        for token in tokens:
            shingle_owners[token].add(position)

    evidence: dict[int, dict[str, Any]] = {}
    for position, row in enumerate(rows):
        fragments = passages[position]
        total_chars = max(1, sum(fragments.values()))
        repeated_chars = sum(
            length for key, length in fragments.items() if len(owners[key]) >= 3
        )
        shared: Counter[int] = Counter()
        for key, length in fragments.items():
            for other in owners[key] - {position}:
                shared[other] += length
        nearest = max(shared, key=lambda other: (shared[other], -other)) if shared else None
        overlap = (
            shared[nearest] / max(1, min(total_chars, sum(passages[nearest].values())))
            if nearest is not None else 0.0
        )
        method = "paragraph" if nearest is not None else "none"
        fuzzy_shared: Counter[int] = Counter()
        for token in shingles[position]:
            posting = shingle_owners[token]
            if len(rows) > 100 and len(posting) > 100:
                continue
            for other in posting - {position}:
                fuzzy_shared[other] += 1
        if fuzzy_shared:
            fuzzy_nearest = max(fuzzy_shared, key=lambda other: (fuzzy_shared[other], -other))
            fuzzy_overlap = fuzzy_shared[fuzzy_nearest] / max(
                1, min(len(shingles[position]), len(shingles[fuzzy_nearest]))
            )
            if fuzzy_overlap >= 0.45 and fuzzy_overlap > overlap:
                nearest, overlap, method = fuzzy_nearest, fuzzy_overlap, "character_shingle"
        evidence[int(row["id"])] = {
            "repeated_ratio": round(repeated_chars / total_chars, 3),
            "repeated_scene_count": len(shared),
            "similar_scene_index": (
                int(rows[nearest]["scene_index_in_book"]) if nearest is not None else None
            ),
            "overlap_ratio": round(overlap, 3),
            "similarity_method": method,
            "similar_excerpt": (
                str(rows[nearest]["text"])[:450] if nearest is not None else ""
            ),
        }
    return evidence


class SceneQualityScreener:
    def __init__(
        self, *, repository: PostgresRepository, llm_client: JsonLLMClient,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.llm_client = llm_client
        self.settings = settings or get_settings()

    def screen_scene(
        self, scene: dict[str, Any], evidence: dict[str, Any], scene_count: int,
    ) -> str:
        scene_id = int(scene["id"])
        scene_text = bounded_sample(
            str(scene["text"]), head_chars=800, middle_chars=800,
            tail_chars=800, max_chars=min(2400, self.settings.max_llm_input_chars),
        )
        prompt = quality_prompt.build_user_prompt(
            scene_text=scene_text,
            scene_index=int(scene["scene_index_in_book"]),
            scene_count=scene_count,
            **evidence,
        )
        input_hash = self.repository.scene_quality_cache_key(
            model=self.llm_client.model,
            prompt_version=quality_prompt.PROMPT_VERSION,
            input_text=prompt,
        )
        self.repository.update_scene(scene_id, quality_screen_status="screening")
        try:
            cached = self.repository.get_scene_quality_cache(input_hash)
            if cached is None:
                decision = self.llm_client.request_typed(
                    system_prompt=quality_prompt.SYSTEM_PROMPT,
                    user_prompt=prompt,
                    response_model=SceneQualityDecision,
                )
                self.repository.put_scene_quality_cache(
                    input_hash=input_hash,
                    model=self.llm_client.model,
                    prompt_version=quality_prompt.PROMPT_VERSION,
                    input_text=prompt,
                    output_json=decision.model_dump(),
                )
            else:
                decision = SceneQualityDecision.model_validate(cached["output_json"])
            high_confidence_duplicate = (
                evidence["repeated_ratio"] >= 0.85
                and evidence["overlap_ratio"] >= 0.80
                and evidence["repeated_scene_count"] >= 3
            )
            reject = decision.decision == "reject" or high_confidence_duplicate
            category = (
                "duplicate" if high_confidence_duplicate else decision.category
            )
            reason = (
                "同书至少三个其他场景大量复用相同正文段落，缺少独立内容。"
                if high_confidence_duplicate and decision.decision == "keep"
                else decision.reason
            )
            meta = {"decision": decision.model_dump(), "duplicate_evidence": {
                key: value for key, value in evidence.items() if key != "similar_excerpt"
            }, "high_confidence_duplicate": high_confidence_duplicate}
            values: dict[str, Any] = {
                "quality_screen_status": "rejected" if reject else "kept",
                "quality_screen_reason": reason,
                "quality_screen_prompt_version": quality_prompt.PROMPT_VERSION,
                "quality_screen_meta_json": meta,
                "error_message": None,
            }
            if reject:
                values.update(
                    reference_status="discarded",
                    reference_score=0.0,
                    reference_reason=f"质量全筛剔除（{category}）：{reason}",
                    reference_prompt_version=quality_prompt.PROMPT_VERSION,
                    reference_rule_version="quality_screen_v1",
                    reference_meta_json=meta,
                    annotate_status="not_applicable",
                    index_status="not_applicable",
                )
            self.repository.update_scene(scene_id, **values)
            return values["quality_screen_status"]
        except Exception as exc:
            self.repository.update_scene(
                scene_id, quality_screen_status="failed",
                quality_screen_reason=str(exc)[:1000],
                quality_screen_prompt_version=quality_prompt.PROMPT_VERSION,
                error_message=str(exc)[:2000],
            )
            logger.warning("scene quality screening failed", extra={"scene_id": scene_id, "error": str(exc)})
            return "failed"

    def screen_book(self, book_id: int, version: int) -> dict[str, int]:
        started = time.perf_counter()
        rows = [dict(row) for row in self.repository.list_scenes(book_id, version)]
        evidence = duplicate_evidence(rows)
        stats = {"total": len(rows), "kept": 0, "rejected": 0, "failed": 0}
        with ThreadPoolExecutor(max_workers=max(1, self.settings.llm_concurrency)) as executor:
            futures = [
                executor.submit(self.screen_scene, row, evidence[int(row["id"])], len(rows))
                for row in rows
            ]
            for future in as_completed(futures):
                stats[future.result()] += 1
        logger.info(
            "scene_quality_screen_completed",
            extra={"book_id": book_id, "version": version, **stats,
                   "latency_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
        if stats["failed"]:
            raise NovelRagError(
                f"scene quality screening failed for {stats['failed']} scene(s)"
            )
        return stats
