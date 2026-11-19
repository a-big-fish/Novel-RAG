from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.clients.llm_client import JsonLLMClient, LLMRequestOptions
from app.config import Settings
from app.db.postgres import PostgresRepository
from app.services.tagger import TagVocabulary

logger = logging.getLogger(__name__)


class ParsedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_intent: str
    summary_query: str = ""
    scene_type: list[str] = Field(default_factory=list)
    technique: list[str] = Field(default_factory=list)
    style_tags: list[str] = Field(default_factory=list)
    emotion_tags: list[str] = Field(default_factory=list)
    key_images: list[str] = Field(default_factory=list)
    fallback: bool = False
    cache_hit: bool = False


_TAG_FIELDS = {
    "scene_type": "scene_type",
    "technique": "technique",
    "style_tags": "style",
    "emotion_tags": "emotion",
    "key_images": "image",
}


class QueryParser:
    def __init__(
        self, repository: PostgresRepository, llm: JsonLLMClient,
        settings: Settings, vocabulary: TagVocabulary | None = None,
    ) -> None:
        self.repository = repository
        self.llm = llm
        self.settings = settings
        self.vocabulary = vocabulary or TagVocabulary.from_json(
            Path(__file__).resolve().parents[1] / "data" / "tag_vocab"
            / f"{settings.tag_vocab_version}.json"
        )

    def _request_options(self) -> LLMRequestOptions:
        """Latency-first options: bounded output, short timeout, no retries."""

        return LLMRequestOptions(
            model=self.settings.query_llm_model or None,
            temperature=self.settings.query_llm_temperature,
            max_tokens=self.settings.query_llm_max_tokens,
            reasoning_effort=self.settings.query_llm_reasoning_effort.strip() or None,
            timeout_seconds=self.settings.query_llm_timeout_seconds,
            max_retries=self.settings.query_llm_max_retries,
            json_response_format=self.settings.query_llm_json_response_format,
        )

    def _call_llm(self, options: LLMRequestOptions, *, system_prompt: str,
                  user_prompt: str) -> Any:
        caller = getattr(self.llm, "request_json_with_options", None)
        if caller is not None:
            return caller(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                options=options,
            )
        return self.llm.request_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def _fresh(self, created_at: Any) -> bool:
        ttl = self.settings.query_fallback_cache_seconds
        if ttl <= 0 or created_at is None:
            return False
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                return False
        if not isinstance(created_at, datetime):
            return False
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return datetime.now(UTC) - created_at <= timedelta(seconds=ttl)

    def _store_fallback(
        self, fallback_key: str, raw_intent: str, prompt_version: str,
    ) -> None:
        if self.settings.query_fallback_cache_seconds <= 0:
            return
        try:
            self.repository.put_query_cache(
                input_hash=fallback_key,
                model=self.llm.model,
                prompt_version=f"{prompt_version}-fallback",
                tag_vocab_version=self.settings.tag_vocab_version,
                query_text=raw_intent,
                output_json={
                    "raw_intent": raw_intent,
                    "summary_query": "",
                    "scene_type": [],
                    "technique": [],
                    "style_tags": [],
                    "emotion_tags": [],
                    "key_images": [],
                    "fallback": True,
                },
            )
        except Exception:  # noqa: BLE001 - fallback caching is best effort
            logger.warning("query_fallback_cache_failed", exc_info=True)

    def parse(self, query: str) -> ParsedQuery:
        if self.settings.query_prompt_version != "v1":
            raise ValueError("unsupported query prompt version")
        from app.prompts.query_parsing.v1 import PROMPT_VERSION, SYSTEM_PROMPT

        raw_intent = query.strip()
        if not raw_intent or len(raw_intent) > self.settings.query_max_chars:
            raise ValueError("query length is outside configured bounds")
        key_material = json.dumps(
            [self.llm.model, PROMPT_VERSION, self.settings.tag_vocab_version, raw_intent],
            ensure_ascii=False, separators=(",", ":"),
        )
        cache_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        cached = self.repository.get_query_cache(cache_key)
        if cached is not None:
            return ParsedQuery.model_validate({**cached, "cache_hit": True})

        fallback_key = hashlib.sha256(
            f"{key_material}|fallback".encode("utf-8")
        ).hexdigest()
        if self.settings.query_fallback_cache_seconds > 0:
            fallback_row = self.repository.get_query_cache_row(fallback_key)
            if fallback_row is not None and self._fresh(fallback_row.get("created_at")):
                return ParsedQuery.model_validate({
                    **fallback_row["output_json"],
                    "fallback": True,
                    "cache_hit": True,
                })

        vocabulary_text = "\n".join(
            f"{field}: {', '.join(self.vocabulary.prompts_for(namespace))}"
            for field, namespace in _TAG_FIELDS.items()
        )
        user_prompt = f"受控词表：\n{vocabulary_text}\n\n用户需求：\n{raw_intent}"
        started = perf_counter()
        options = self._request_options()
        try:
            try:
                output = self._call_llm(
                    options, system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt,
                )
            except Exception as exc:  # noqa: BLE001 - retry only for empty output
                if (options.reasoning_effort
                        and "non-text response" in str(exc).lower()):
                    logger.warning(
                        "query_parse_retry_without_reasoning",
                        extra={"model": self.llm.model},
                    )
                    output = self._call_llm(
                        LLMRequestOptions(
                            model=options.model,
                            temperature=options.temperature,
                            max_tokens=max(options.max_tokens or 400, 600),
                            reasoning_effort=None,
                            timeout_seconds=options.timeout_seconds,
                            max_retries=options.max_retries,
                            json_response_format=options.json_response_format,
                        ),
                        system_prompt=SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                    )
                else:
                    raise
            if not isinstance(output, dict):
                raise ValueError("query parser output is not an object")
            normalized: dict[str, Any] = {
                "raw_intent": raw_intent,
                "summary_query": str(output.get("summary_query") or "").strip(),
            }
            for field, namespace in _TAG_FIELDS.items():
                values = output.get(field) or []
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError(f"invalid {field}")
                normalized[field] = self.vocabulary.map_many(namespace, values)
            result = ParsedQuery.model_validate(normalized)
        except Exception as exc:  # noqa: BLE001 - parsing must never fail the search
            logger.warning(
                "query_parse_fallback",
                extra={
                    "model": self.llm.model,
                    "latency_ms": round((perf_counter() - started) * 1000, 1),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                },
            )
            self._store_fallback(fallback_key, raw_intent, PROMPT_VERSION)
            return ParsedQuery(raw_intent=raw_intent, fallback=True)

        logger.info(
            "query_parse_completed",
            extra={
                "model": self.llm.model,
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "fallback": False,
            },
        )
        self.repository.put_query_cache(
            input_hash=cache_key, model=self.llm.model,
            prompt_version=PROMPT_VERSION,
            tag_vocab_version=self.settings.tag_vocab_version,
            query_text=raw_intent,
            output_json=result.model_dump(exclude={"cache_hit"}),
        )
        return result
