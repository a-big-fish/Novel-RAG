from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SceneAnnotation(BaseModel):
    """Validated, model-generated annotation for exactly one scene."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    style_summary: str = Field(min_length=1, max_length=500)
    usage_hint: str = Field(min_length=1, max_length=300)
    scene_type: list[str] = Field(default_factory=list, max_length=5)
    technique: list[str] = Field(default_factory=list, max_length=8)
    style_tags: list[str] = Field(default_factory=list, max_length=10)
    emotion_tags: list[str] = Field(default_factory=list, max_length=8)
    key_images: list[str] = Field(default_factory=list, max_length=8)
    narrative_func: str = Field(min_length=1, max_length=64)


class MappedAnnotation(BaseModel):
    summary: str
    style_summary: str
    usage_hint: str
    scene_type: list[str]
    scene_type_display: list[str]
    technique: list[str]
    technique_display: list[str]
    style_tags: list[str]
    style_tags_display: list[str]
    emotion_tags: list[str]
    emotion_tags_display: list[str]
    key_images: list[str]
    key_images_display: list[str]
    narrative_func: str
    meta_json: dict[str, Any]
