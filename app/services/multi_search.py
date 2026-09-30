from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any

from app.config import Settings
from app.db.postgres import PostgresRepository
from app.services.retriever import PreparedQuery, Retriever, RetrievalError

logger = logging.getLogger(__name__)


class MultiBookSearchError(RetrievalError):
    pass


def interleave_candidates(
    book_results: dict[int, dict[str, Any]], book_ids: list[int],
    *, per_book_limit: int, global_limit: int,
) -> list[dict[str, Any]]:
    """Build a bounded, deterministic candidate pool without comparing book scores."""
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for local_rank in range(per_book_limit):
        for book_id in book_ids:
            result = book_results[book_id]
            if result["status"] != "ok":
                continue
            items = result["rrf"]["items"]
            if local_rank >= len(items):
                continue
            item = items[local_rank]
            key = (book_id, result["version"], item["scene_id"])
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                **item, "book_rrf_rank": local_rank + 1,
                "candidate_rank": len(candidates) + 1,
            })
            if len(candidates) >= global_limit:
                return candidates
    return candidates


class MultiBookSearcher:
    def __init__(
        self, repository: PostgresRepository, retriever: Retriever,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.retriever = retriever
        self.settings = settings

    def _search_book(
        self, book_id: int, version: int, prepared: PreparedQuery,
        route_top_n: int, per_book_limit: int,
    ) -> dict[str, Any]:
        started = perf_counter()
        last_error = "book search failed"
        for attempt in range(1, self.settings.multi_search_attempts + 1):
            try:
                result = self.retriever.search_prepared(
                    book_id=book_id, version=version, prepared=prepared,
                    route_top_n=route_top_n, rrf_top_n=per_book_limit,
                )
                return {
                    **result,
                    "status": "ok", "version": version, "attempts": attempt,
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                }
            except RetrievalError as exc:
                last_error = str(exc)
                # A wrong-book hit is an integrity error; repeating it cannot help.
                if "outside requested" in str(exc) or "does not match" in str(exc):
                    break
                if attempt == self.settings.multi_search_attempts:
                    break
        return {
            "status": "failed", "version": version, "attempts": attempt,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "error": last_error,
        }

    def search(
        self, *, query: str, book_ids: list[int],
        versions: dict[int, int] | None = None,
        route_top_n: int, per_book_limit: int, global_limit: int,
    ) -> dict[str, Any]:
        started = perf_counter()
        versions = versions or {}
        book_results: dict[int, dict[str, Any]] = {}
        targets: list[tuple[int, int]] = []
        for book_id in book_ids:
            book = self.repository.get_book(book_id)
            if book is None:
                book_results[book_id] = {"status": "failed", "error": "book not found"}
                continue
            version = versions.get(book_id) or int(book["current_version"] or 0)
            ready_versions = {
                int(row["version"]) for row in self.repository.list_versions(book_id)
            }
            if (version <= 0 or version > int(book["current_version"] or 0)
                    or version not in ready_versions):
                book_results[book_id] = {
                    "status": "failed", "version": version,
                    "error": "ready index version not found",
                }
                continue
            targets.append((book_id, version))

        if not targets:
            raise MultiBookSearchError("no requested book has a ready index version")

        prepared = self.retriever.prepare(query)
        with ThreadPoolExecutor(
            max_workers=min(len(targets), self.settings.multi_search_concurrency),
            thread_name_prefix="multi-book-search",
        ) as pool:
            futures = {
                pool.submit(
                    self._search_book, book_id, version, prepared,
                    route_top_n, per_book_limit,
                ): book_id
                for book_id, version in targets
            }
            for future in as_completed(futures):
                book_id = futures[future]
                try:
                    book_results[book_id] = future.result()
                except Exception:
                    logger.exception("multi-book search worker failed: book_id=%s", book_id)
                    book_results[book_id] = {
                        "status": "failed", "error": "book search worker failed",
                    }

        for book_id in book_ids:
            result = book_results[book_id]
            logger.info(
                "multi-book search result: book_id=%s version=%s status=%s "
                "attempts=%s candidates=%s latency_ms=%s degraded_routes=%s",
                book_id, result.get("version"), result["status"],
                result.get("attempts", 0),
                len(result.get("rrf", {}).get("items", [])),
                result.get("latency_ms"), result.get("degraded_routes", []),
            )

        if all(result["status"] != "ok" for result in book_results.values()):
            raise MultiBookSearchError("all requested book searches failed")

        candidates = interleave_candidates(
            book_results, book_ids,
            per_book_limit=per_book_limit, global_limit=global_limit,
        )
        return {
            "raw_intent": prepared.parsed.raw_intent,
            "parsed_query": prepared.parsed.model_dump(),
            "book_ids": book_ids,
            "books": {str(book_id): book_results[book_id] for book_id in book_ids},
            "degraded_books": [
                book_id for book_id in book_ids
                if book_results[book_id]["status"] != "ok"
            ],
            "aggregation": {
                "strategy": "round_robin_book_rrf",
                "items": candidates,
            },
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }
