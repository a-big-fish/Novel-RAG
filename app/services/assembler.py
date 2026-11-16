"""Assemble ranked scenes into a budgeted, cited prompt or fragment payload.

Assembly never retrieves, re-ranks or generates text. It only selects from the
already ordered search result, bounds each scene, respects a character budget,
and renders the versioned prompt template.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db.postgres import PostgresRepository
from app.prompts.assembly import v1 as assembly_prompt
from app.utils.text import bounded_sample, clean_text


@dataclass(frozen=True)
class AssemblyOptions:
    budget_chars: int
    max_scenes: int
    per_scene_chars: int
    book_quota: int = 0
    include_excerpt: bool = True
    template_version: str = assembly_prompt.PROMPT_VERSION


class AssemblyError(ValueError):
    pass


def _scene_excerpt(text: str, *, per_scene_chars: int) -> str:
    if not text:
        return ""
    third = max(1, per_scene_chars // 3)
    return bounded_sample(
        text,
        head_chars=third,
        middle_chars=third,
        tail_chars=third,
        max_chars=per_scene_chars,
    )


def _quota_order(
    candidates: list[dict[str, Any]], *, quota: int,
) -> list[dict[str, Any]]:
    if quota <= 0:
        return candidates
    first: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    counts: dict[int, int] = {}
    for candidate in candidates:
        book_id = int(candidate["book_id"])
        if counts.get(book_id, 0) < quota:
            first.append(candidate)
            counts[book_id] = counts.get(book_id, 0) + 1
        else:
            rest.append(candidate)
    return first + rest


def _build_candidates(
    result: dict[str, Any],
    repository: PostgresRepository,
    *,
    options: AssemblyOptions,
) -> list[dict[str, Any]]:
    final = result.get("final") or {}
    items = final.get("items") or []
    titles: dict[int, str] = {}
    seen_scenes: set[int] = set()
    seen_text: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for item in items:
        if len(candidates) >= options.max_scenes * 4:
            break
        scene_id = int(item["scene_id"])
        if scene_id in seen_scenes:
            continue
        scene = repository.get_scene(scene_id)
        if scene is None:
            continue
        book_id = int(item["book_id"])
        version = int(item["version"])
        if (int(scene["book_id"]) != book_id
                or int(scene["version"]) != version
                or scene.get("reference_status") != "selected"):
            continue
        text = str(scene.get("text") or "")
        digest = hashlib.sha256(clean_text(text).encode("utf-8")).hexdigest()
        if digest in seen_text:
            continue
        seen_scenes.add(scene_id)
        seen_text.add(digest)
        if book_id not in titles:
            book = repository.get_book(book_id)
            titles[book_id] = str(book["title"]) if book else f"书籍 {book_id}"
        excerpt = _scene_excerpt(text, per_scene_chars=options.per_scene_chars) \
            if options.include_excerpt else ""
        truncated = bool(excerpt) and len(clean_text(text)) > len(excerpt)
        rank = int(item.get("rerank_rank") or item.get("book_rrf_rank")
                   or len(candidates) + 1)
        candidates.append({
            "book_id": book_id,
            "version": version,
            "scene_id": scene_id,
            "scene_index_in_book": int(scene["scene_index_in_book"]),
            "rank": rank,
            "title": titles[book_id],
            "summary": str(scene.get("summary") or ""),
            "style_summary": str(scene.get("style_summary") or ""),
            "usage_hint": str(scene.get("usage_hint") or ""),
            "text": text,
            "excerpt": excerpt,
            "truncated": truncated,
            "route_ranks": item.get("route_ranks") or {},
            "book_rrf_rank": item.get("book_rrf_rank"),
            "rerank_score": item.get("rerank_score"),
        })
    return candidates


def _render_block(candidate: dict[str, Any], *, index: int, excerpt: str) -> str:
    return assembly_prompt.render_reference(
        index=index,
        title=candidate["title"],
        version=candidate["version"],
        scene_index=candidate["scene_index_in_book"],
        rank=candidate["rank"],
        summary=candidate["summary"],
        style_summary=candidate["style_summary"],
        usage_hint=candidate["usage_hint"],
        excerpt=excerpt,
        truncated=candidate["truncated"] and excerpt == candidate["excerpt"],
    )


def assemble_search_result(
    result: dict[str, Any],
    repository: PostgresRepository,
    settings: Settings,
    *,
    task: str,
    constraints: dict[str, Any] | None = None,
    options: AssemblyOptions | None = None,
) -> dict[str, Any]:
    """Select, bound and render the final candidates under a character budget."""

    options = options or AssemblyOptions(
        budget_chars=settings.assembly_budget_chars,
        max_scenes=settings.assembly_max_scenes,
        per_scene_chars=settings.assembly_per_scene_chars,
        book_quota=settings.assembly_book_quota,
        template_version=settings.assembly_template_version,
    )
    if options.budget_chars <= 0 or options.max_scenes <= 0:
        raise AssemblyError("budget_chars and max_scenes must be positive")

    candidates = _build_candidates(result, repository, options=options)
    ordered = _quota_order(candidates, quota=options.book_quota)

    wrapper = assembly_prompt.wrapper_chars(task=task, constraints=constraints)
    used = wrapper
    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for candidate in ordered:
        if len(selected) >= options.max_scenes:
            dropped.append({**_drop_ref(candidate), "reason": "max_scenes"})
            continue
        block = _render_block(candidate, index=len(selected) + 1,
                             excerpt=candidate["excerpt"])
        cost = len(block) + 2
        if used + cost <= options.budget_chars:
            selected.append(candidate)
            used += cost
            continue

        # Try shrinking the excerpt before dropping the reference.
        fixed = _render_block(candidate, index=len(selected) + 1, excerpt="")
        remaining = options.budget_chars - used - 2 - len(fixed) - 24
        excerpt = ""
        if options.include_excerpt and remaining >= 160:
            third = max(1, remaining // 3)
            excerpt = bounded_sample(
                candidate["text"],
                head_chars=third, middle_chars=third, tail_chars=third,
                max_chars=remaining,
            )
            shrunk = _render_block(candidate, index=len(selected) + 1, excerpt=excerpt)
            if used + len(shrunk) + 2 <= options.budget_chars:
                selected.append({**candidate, "excerpt": excerpt, "truncated": True})
                used += len(shrunk) + 2
                continue
        dropped.append({**_drop_ref(candidate), "reason": "budget"})

    text = assembly_prompt.render_prompt(
        task=task, references=_template_references(selected), constraints=constraints,
    )
    while len(text) > options.budget_chars and selected:
        dropped.append({**_drop_ref(selected.pop()), "reason": "budget"})
        text = assembly_prompt.render_prompt(
            task=task, references=_template_references(selected),
            constraints=constraints,
        )

    references = [
        {
            "book_id": item["book_id"],
            "version": item["version"],
            "scene_id": item["scene_id"],
            "scene_index_in_book": item["scene_index_in_book"],
            "rank": item["rank"],
            "title": item["title"],
            "truncated": item["truncated"],
            "route_ranks": item["route_ranks"],
        }
        for item in selected
    ]
    return {
        "status": "ok" if selected else "empty",
        "prompt": {
            "template_version": options.template_version,
            "text": text,
            "char_count": len(text),
            "budget": {
                "limit": options.budget_chars,
                "used": len(text),
                "included": len(selected),
                "dropped": len(dropped),
                "dropped_reason": (
                    "budget" if any(item["reason"] == "budget" for item in dropped)
                    else "max_scenes" if dropped else None
                ),
            },
        },
        "references": references,
        "dropped": dropped,
    }


def _template_references(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "title": item["title"],
            "version": item["version"],
            "scene_index": item["scene_index_in_book"],
            "rank": item["rank"],
            "summary": item["summary"],
            "style_summary": item["style_summary"],
            "usage_hint": item["usage_hint"],
            "excerpt": item["excerpt"],
            "truncated": item["truncated"],
        }
        for index, item in enumerate(selected, start=1)
    ]


def _drop_ref(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "book_id": candidate["book_id"],
        "version": candidate["version"],
        "scene_id": candidate["scene_id"],
        "scene_index_in_book": candidate["scene_index_in_book"],
    }
