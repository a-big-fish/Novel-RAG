"""High-level writing-reference flow: brief -> query -> multi-book search -> assembly.

This is the surface the MCP tools and `/assemble` expose. Callers describe the
scene they want to write; they never assemble retrieval queries themselves.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from app.config import Settings
from app.db.postgres import PostgresRepository
from app.services.assembler import AssemblyOptions, assemble_search_result
from app.services.multi_search import MultiBookSearchError, MultiBookSearcher
from app.services.request_planner import plan_writing_query
from app.services.retriever import ProgressCallback

OUTPUT_FORMATS = ("prompt", "fragments", "both")


class WritingReferenceError(RuntimeError):
    pass


def ready_book_ids(repository: PostgresRepository) -> list[int]:
    """Return every book that currently has an activated index version."""

    book_ids: list[int] = []
    for book in repository.list_books():
        if str(book.get("status")) != "ready":
            continue
        if int(book.get("current_version") or 0) <= 0:
            continue
        book_ids.append(int(book["id"]))
    return book_ids


class WritingReferenceService:
    def __init__(
        self,
        repository: PostgresRepository,
        searcher: MultiBookSearcher,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.searcher = searcher
        self.settings = settings

    def search(
        self,
        *,
        writing_goal: str,
        outline_context: str = "",
        constraints: dict[str, Any] | None = None,
        book_ids: list[int] | None = None,
        versions: dict[int, int] | None = None,
        output: str = "prompt",
        budget_chars: int | None = None,
        max_scenes: int | None = None,
        per_scene_chars: int | None = None,
        book_quota: int | None = None,
        query: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if output not in OUTPUT_FORMATS:
            raise WritingReferenceError(
                f"output must be one of {', '.join(OUTPUT_FORMATS)}"
            )
        targets = [int(book_id) for book_id in (book_ids or [])]
        if not targets:
            targets = ready_book_ids(self.repository)
        if not targets:
            raise WritingReferenceError("no ready book is available for retrieval")
        if len(targets) > self.settings.multi_search_max_books:
            raise WritingReferenceError("too many book_ids in one request")

        if query is not None:
            raw_query = query.strip()
            if not raw_query:
                raise WritingReferenceError("query must not be empty")
            plan = {
                "query": raw_query,
                "char_count": len(raw_query),
                "truncated": False,
                "outline_chars": 0,
                "has_constraints": bool(constraints),
            }
        else:
            plan = plan_writing_query(
                writing_goal=writing_goal,
                outline_context=outline_context,
                constraints=constraints,
                max_chars=self.settings.query_max_chars,
            )
        try:
            result = self.searcher.search(
                query=plan["query"],
                book_ids=targets,
                versions=versions or {},
                route_top_n=self.settings.query_route_top_n,
                per_book_limit=self.settings.multi_search_per_book_limit,
                global_limit=self.settings.multi_search_global_limit,
                on_progress=on_progress,
            )
        except MultiBookSearchError as exc:
            raise WritingReferenceError(str(exc)) from exc

        response: dict[str, Any] = {
            "query_plan": plan,
            "book_ids": targets,
            "search": _search_summary(result),
            "prompt": None,
            "fragments": None,
            "references": [],
            "budget": None,
        }
        if output in ("fragments", "both"):
            response["fragments"] = _fragments(result)
        if output in ("prompt", "both"):
            task = writing_goal.strip()
            if outline_context.strip():
                task = f"{task}\n\n情节上下文：{outline_context.strip()}"
            if on_progress:
                on_progress("assembling", {
                    "candidate_count": len(
                        (result.get("final") or {}).get("items") or []
                    ),
                    "budget_chars": budget_chars or self.settings.assembly_budget_chars,
                })
            assembly_started = perf_counter()
            assembly = assemble_search_result(
                result, self.repository, self.settings,
                task=task,
                constraints=constraints,
                options=AssemblyOptions(
                    budget_chars=budget_chars or self.settings.assembly_budget_chars,
                    max_scenes=max_scenes or self.settings.assembly_max_scenes,
                    per_scene_chars=(
                        per_scene_chars or self.settings.assembly_per_scene_chars
                    ),
                    book_quota=(
                        self.settings.assembly_book_quota
                        if book_quota is None else book_quota
                    ),
                    template_version=self.settings.assembly_template_version,
                ),
            )
            response["prompt"] = assembly["prompt"]
            response["references"] = assembly["references"]
            response["budget"] = assembly["prompt"]["budget"]
            response["assembly_status"] = assembly["status"]
            response["dropped"] = assembly["dropped"]
            if on_progress:
                on_progress("assembling_complete", {
                    "status": assembly["status"],
                    "included": assembly["prompt"]["budget"]["included"],
                    "dropped": assembly["prompt"]["budget"]["dropped"],
                    "prompt_chars": assembly["prompt"]["char_count"],
                    "latency_ms": round((perf_counter() - assembly_started) * 1000, 2),
                })
        return response


def _search_summary(result: dict[str, Any]) -> dict[str, Any]:
    books = result.get("books") or {}
    return {
        "parsed_query": result.get("parsed_query"),
        "degraded_books": result.get("degraded_books") or [],
        "books": {
            str(book_id): {
                "status": data.get("status"),
                "version": data.get("version"),
                "attempts": data.get("attempts"),
                "latency_ms": data.get("latency_ms"),
                "degraded_routes": data.get("degraded_routes") or [],
                "candidate_count": len((data.get("rrf") or {}).get("items") or []),
            }
            for book_id, data in books.items()
        },
        "rerank": result.get("rerank"),
        "aggregation_strategy": (result.get("aggregation") or {}).get("strategy"),
        "final_source": (result.get("final") or {}).get("source"),
        "latency_ms": result.get("latency_ms"),
    }


def _fragments(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = (result.get("final") or {}).get("items") or []
    return [
        {
            "book_id": int(item["book_id"]),
            "version": int(item["version"]),
            "scene_id": int(item["scene_id"]),
            "scene_index_in_book": item.get("scene_index_in_book"),
            "summary": item.get("summary"),
            "style_summary": item.get("style_summary"),
            "usage_hint": item.get("usage_hint"),
            "text_preview": item.get("text_preview"),
            "route_ranks": item.get("route_ranks"),
            "book_rrf_rank": item.get("book_rrf_rank"),
            "rerank_score": item.get("rerank_score"),
        }
        for item in items
    ]
