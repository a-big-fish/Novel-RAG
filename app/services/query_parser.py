from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.clients.llm_client import JsonLLMClient
from app.config import Settings
from app.db.postgres import PostgresRepository
from app.services.tagger import TagVocabulary
from app.utils.errors import NovelRagError


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

        vocabulary_text = "\n".join(
            f"{field}: {', '.join(self.vocabulary.prompts_for(namespace))}"
            for field, namespace in _TAG_FIELDS.items()
        )
        try:
            output = self.llm.request_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=f"受控词表：\n{vocabulary_text}\n\n用户需求：\n{raw_intent}",
            )
            if not isinstance(output, dict):
                raise ValueError("query parser output is not an object")
            normalized: dict[str, Any] = {
                "raw_intent": raw_intent,
                "summary_query": str(output.get("summary_query") or "").strip(),
            }
            for field, namespace in _TAG_FIELDS.items():
                values = output.get(field) or []
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError(f"invalid {field}")
                normalized[field] = self.vocabulary.map_many(namespace, values)
            result = ParsedQuery.model_validate(normalized)
        except (NovelRagError, ValueError, TypeError, ValidationError):
            return ParsedQuery(raw_intent=raw_intent, fallback=True)

        self.repository.put_query_cache(
            input_hash=cache_key, model=self.llm.model,
            prompt_version=PROMPT_VERSION,
            tag_vocab_version=self.settings.tag_vocab_version,
            query_text=raw_intent,
            output_json=result.model_dump(exclude={"cache_hit"}),
        )
        return result
