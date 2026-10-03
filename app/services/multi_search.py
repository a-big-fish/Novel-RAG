from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any

from app.clients.rerank_client import RerankClient
from app.config import Settings
from app.db.postgres import PostgresRepository
from app.services.retriever import PreparedQuery, ProgressCallback, Retriever, RetrievalError
from app.utils.errors import StorageError
from app.utils.text import bounded_sample

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
        settings: Settings, reranker: RerankClient | None = None,
    ) -> None:
        self.repository = repository
        self.retriever = retriever
        self.settings = settings
        self.reranker = reranker

    @staticmethod
    def _log_book_results(
        book_ids: list[int], book_results: dict[int, dict[str, Any]],
    ) -> None:
        for book_id in book_ids:
            result = book_results[book_id]
            logger.info(
                "multi_book_result",
                extra={
                    "book_id": book_id,
                    "version": result.get("version"),
                    "status": result["status"],
                    "attempts": result.get("attempts", 0),
                    "candidate_count": len(result.get("rrf", {}).get("items", [])),
                    "latency_ms": result.get("latency_ms"),
                    "degraded_routes": result.get("degraded_routes", []),
                },
            )

    def _rerank(
        self, query: str, candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not self.settings.rerank_enabled:
            return {"status": "disabled"}, candidates
        if not candidates:
            return {"status": "skipped", "reason": "no_candidates"}, candidates
        if self.reranker is None:
            return {"status": "failed", "reason": "Ollama reranker is not configured"}, candidates

        started = perf_counter()
        evaluated = candidates[:self.settings.rerank_top_n]
        batch_id = getattr(self.reranker, "batch_id", None)
        logger.info("multi_book_rerank_started", extra={
            "batch_id": batch_id,
            "model": self.settings.ollama_rerank_model,
            "candidate_count": len(evaluated),
            "available_candidate_count": len(candidates),
            "concurrency": self.settings.rerank_concurrency,
            "document_char_limit": self.settings.rerank_max_document_chars,
        })
        documents: list[str] = []
        for index, item in enumerate(evaluated):
            scene = self.repository.get_scene(item["scene_id"])
            if (scene is None or int(scene["book_id"]) != item["book_id"]
                    or int(scene["version"]) != item["version"]
                    or scene["reference_status"] != "selected"):
                raise MultiBookSearchError("rerank scene lookup does not match candidate")
            header = (
                f"剧情摘要：{str(scene['summary'])[:300]}\n"
                f"文风：{str(scene['style_summary'])[:200]}\n"
                f"使用提示：{str(scene['usage_hint'])[:200]}\n原文：\n"
            )
            budget = self.settings.rerank_max_document_chars
            excerpt = bounded_sample(
                str(scene["text"]),
                head_chars=self.settings.long_text_head_chars,
                middle_chars=self.settings.long_text_middle_chars,
                tail_chars=self.settings.long_text_tail_chars,
                max_chars=max(1, budget - len(header)),
            )
            documents.append((header + excerpt)[:budget])
            logger.info("multi_book_rerank_candidate_prepared", extra={
                "batch_id": batch_id,
                "candidate_index": index, "scene_id": item["scene_id"],
                "book_id": item["book_id"], "version": item["version"],
                "document_chars": len(documents[-1]),
            })

        try:
            scores = self.reranker.rerank(query, documents)
            by_index = {int(item["index"]): float(item["score"]) for item in scores}
            exact_by_index = {
                int(item["index"]): bool(item.get("score_exact", True))
                for item in scores
            }
            if len(by_index) != len(evaluated) or set(by_index) != set(range(len(evaluated))):
                raise StorageError("rerank scores do not cover all candidates")
            for index, item in enumerate(evaluated):
                logger.info("multi_book_rerank_candidate_result", extra={
                    "batch_id": batch_id,
                    "candidate_index": index, "scene_id": item["scene_id"],
                    "book_id": item["book_id"], "score": by_index[index],
                    "score_exact": exact_by_index[index],
                })
            ordered = sorted(
                (dict(
                    item, rerank_score=by_index[index],
                    rerank_score_exact=exact_by_index[index],
                )
                 for index, item in enumerate(evaluated)),
                key=lambda item: (-item["rerank_score"], item["candidate_rank"]),
            )
            ordered.extend(candidates[len(evaluated):])
            for rank, item in enumerate(ordered, start=1):
                item["rerank_rank"] = rank
            latency_ms = round((perf_counter() - started) * 1000, 2)
            logger.info("multi_book_rerank_completed", extra={
                "batch_id": batch_id,
                "candidate_count": len(evaluated), "latency_ms": latency_ms,
                "censored_count": sum(not exact for exact in exact_by_index.values()),
            })
            return {
                "status": "ok", "model": self.settings.ollama_rerank_model,
                "evaluated_count": len(evaluated),
                "censored_count": sum(not exact for exact in exact_by_index.values()),
                "latency_ms": latency_ms,
            }, ordered
        except StorageError as exc:
            logger.exception("multi_book_rerank_failed", extra={
                "batch_id": batch_id,
                "candidate_count": len(evaluated),
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            })
            return {
                "status": "failed", "reason": str(exc),
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            }, candidates

    def _search_book(
        self, book_id: int, version: int, prepared: PreparedQuery,
        route_top_n: int, per_book_limit: int,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        last_error = "book search failed"
        for attempt in range(1, self.settings.multi_search_attempts + 1):
            try:
                result = self.retriever.search_prepared(
                    book_id=book_id, version=version, prepared=prepared,
                    route_top_n=route_top_n, rrf_top_n=per_book_limit,
                    on_progress=on_progress,
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
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        check_started = perf_counter()
        if on_progress:
            on_progress("checking_books", {"book_ids": book_ids})
        versions = versions or {}
        book_results: dict[int, dict[str, Any]] = {}
        targets: list[tuple[int, int]] = []
        for book_id in book_ids:
            try:
                book = self.repository.get_book(book_id)
                ready_versions = (
                    {int(row["version"]) for row in self.repository.list_versions(book_id)}
                    if book is not None else set()
                )
            except StorageError as exc:
                book_results[book_id] = {"status": "failed", "error": str(exc)}
                continue
            if book is None:
                book_results[book_id] = {"status": "failed", "error": "book not found"}
                continue
            version = versions.get(book_id) or int(book["current_version"] or 0)
            if (version <= 0 or version > int(book["current_version"] or 0)
                    or version not in ready_versions):
                book_results[book_id] = {
                    "status": "failed", "version": version,
                    "error": "ready index version not found",
                }
                continue
            targets.append((book_id, version))

        if on_progress:
            on_progress("checking_books_complete", {
                "versions": {book_id: version for book_id, version in targets},
                "failed_books": {
                    book_id: result["error"] for book_id, result in book_results.items()
                },
                "latency_ms": round((perf_counter() - check_started) * 1000, 2),
            })

        if not targets:
            self._log_book_results(book_ids, book_results)
            raise MultiBookSearchError("no requested book has a ready index version")

        prepared = self.retriever.prepare(query, on_progress=on_progress)
        with ThreadPoolExecutor(
            max_workers=min(len(targets), self.settings.multi_search_concurrency),
            thread_name_prefix="multi-book-search",
        ) as pool:
            futures = {
                pool.submit(
                    self._search_book, book_id, version, prepared,
                    route_top_n, per_book_limit, on_progress,
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
                if on_progress:
                    on_progress("book_complete", {
                        "book_id": book_id,
                        "status": book_results[book_id]["status"],
                        "version": book_results[book_id].get("version"),
                        "attempts": book_results[book_id].get("attempts"),
                        "latency_ms": book_results[book_id].get("latency_ms"),
                        "error": book_results[book_id].get("error"),
                    })

        self._log_book_results(book_ids, book_results)

        if all(result["status"] != "ok" for result in book_results.values()):
            raise MultiBookSearchError("all requested book searches failed")

        if on_progress:
            on_progress("aggregating", {"book_ids": book_ids})
        aggregate_started = perf_counter()
        candidates = interleave_candidates(
            book_results, book_ids,
            per_book_limit=per_book_limit, global_limit=global_limit,
        )
        if on_progress:
            on_progress("aggregating_complete", {
                "count": len(candidates),
                "strategy": "round_robin_book_rrf",
                "latency_ms": round((perf_counter() - aggregate_started) * 1000, 2),
            })
        if on_progress and self.settings.rerank_enabled and candidates:
            on_progress("reranking", {"count": min(len(candidates), self.settings.rerank_top_n)})
        rerank, final_items = self._rerank(prepared.parsed.raw_intent, candidates)
        if on_progress and self.settings.rerank_enabled and candidates:
            on_progress("reranking_complete", rerank)
        logger.info(
            "multi_book_aggregation",
            extra={
                "book_count": len(book_ids), "candidate_count": len(candidates),
                "degraded_book_count": sum(
                    result["status"] != "ok" for result in book_results.values()
                ),
                "rerank_status": rerank["status"],
                "rerank_latency_ms": rerank.get("latency_ms"),
            },
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
            "rerank": rerank,
            "final": {
                "source": "rerank" if rerank["status"] == "ok" else "aggregation",
                "items": final_items,
            },
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }
