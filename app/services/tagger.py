from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.services.schema import MappedAnnotation, SceneAnnotation
from app.utils.errors import NovelRagError


class TagMappingError(NovelRagError):
    pass


class TagVocabulary:
    """Maps display expressions to canonical tags by namespace."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self._mapping: dict[str, dict[str, str]] = defaultdict(dict)
        self.canonical: dict[str, set[str]] = defaultdict(set)
        self.rows = [dict(row) for row in rows]
        for row in self.rows:
            namespace = str(row["namespace"])
            canonical = str(row["canonical_key"])
            display = str(row["display_name"])
            if row.get("status", "active") != "active":
                continue
            self._mapping[namespace][display] = canonical
            self._mapping[namespace][canonical] = canonical
            self.canonical[namespace].add(canonical)

    @classmethod
    def from_json(cls, path: Path) -> "TagVocabulary":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise TagMappingError("tag vocabulary JSON must be a list")
        return cls(payload)

    def map_name(self, namespace: str, value: str) -> str | None:
        return self._mapping.get(namespace, {}).get(value.strip())

    def map_many(self, namespace: str, values: Iterable[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            canonical = self.map_name(namespace, value)
            if canonical and canonical not in result:
                result.append(canonical)
        return result

    def display_values(self, values: Iterable[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = str(value).strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def prompts_for(self, namespace: str) -> list[str]:
        values = [
            str(row["canonical_key"])
            for row in self.rows
            if row["namespace"] == namespace and row.get("status", "active") == "active"
        ]
        return sorted(set(values))

    def map_annotation(self, annotation: SceneAnnotation) -> MappedAnnotation:
        narrative_func = self.map_name("narrative_func", annotation.narrative_func)
        if not narrative_func:
            raise TagMappingError(
                f"invalid narrative_func: {annotation.narrative_func}"
            )

        return MappedAnnotation(
            summary=annotation.summary.strip(),
            style_summary=annotation.style_summary.strip(),
            usage_hint=annotation.usage_hint.strip(),
            scene_type=self.map_many("scene_type", annotation.scene_type),
            scene_type_display=self.display_values(annotation.scene_type),
            technique=self.map_many("technique", annotation.technique),
            technique_display=self.display_values(annotation.technique),
            style_tags=self.map_many("style", annotation.style_tags),
            style_tags_display=self.display_values(annotation.style_tags),
            emotion_tags=self.map_many("emotion", annotation.emotion_tags),
            emotion_tags_display=self.display_values(annotation.emotion_tags),
            key_images=self.map_many("image", annotation.key_images),
            key_images_display=self.display_values(annotation.key_images),
            narrative_func=narrative_func,
            meta_json=annotation.model_dump(),
        )
