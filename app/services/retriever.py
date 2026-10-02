from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.clients.ollama_client import OllamaClient
from app.config import Settings
from app.db.postgres import PostgresRepository
from app.db.qdrant import QdrantAdapter, scenes_collection_name
from app.services.fusion import reciprocal_rank_fusion
from app.services.query_parser import ParsedQuery, QueryParser
from app.services.sparse import build_sparse_vector
from app.utils.errors import StorageError


class RetrievalError(StorageError):
    pass


ProgressCallback = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class PreparedQuery:
    parsed: ParsedQuery
    dense_vectors: dict[str, list[float]]
    dense_error: str | None = None


def build_meta_query(parsed: dict[str, Any]) -> str:
    fields = (
        ("场景类型", "scene_type"), ("写作技法", "technique"),
        ("文风", "style_tags"), ("情绪", "emotion_tags"),
        ("意象", "key_images"),
    )
    return "\n".join(
        f"{label}：{'、'.join(parsed[key])}"
        for label, key in fields if parsed.get(key)
    )


class Retriever:
    def __init__(
        self, repository: PostgresRepository, qdrant: QdrantAdapter,
        ollama: OllamaClient, parser: QueryParser, settings: Settings,
    ) -> None:
        self.repository = repository
        self.qdrant = qdrant
        self.ollama = ollama
        self.parser = parser
        self.settings = settings

    def search(
        self, *, book_id: int, version: int, query: str,
        route_top_n: int, rrf_top_n: int,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        prepared = self.prepare(query, on_progress=on_progress)
        result = self.search_prepared(
            book_id=book_id, version=version, prepared=prepared,
            route_top_n=route_top_n, rrf_top_n=rrf_top_n,
            on_progress=on_progress,
        )
        result["latency_ms"] = round((perf_counter() - started) * 1000, 2)
        return result

    def prepare(
        self, query: str, *, on_progress: ProgressCallback | None = None,
    ) -> PreparedQuery:
        parse_started = perf_counter()
        if on_progress:
            on_progress("parsing", {})
        parsed = self.parser.parse(query)
        parsed_dict = parsed.model_dump()
        if on_progress:
            on_progress("parsing_complete", {
                "parsed_query": parsed_dict,
                "latency_ms": round((perf_counter() - parse_started) * 1000, 2),
            })
        raw = parsed.raw_intent
        meta = build_meta_query(parsed_dict) or raw
        summary = parsed.summary_query or raw

        embed_started = perf_counter()
        embed_model = getattr(self.ollama, "model", self.settings.ollama_embed_model)
        if on_progress:
            on_progress("embedding", {
                "model": embed_model,
                "inputs": {"text_dense": raw, "meta_dense": meta,
                           "summary_dense": summary},
            })
        try:
            vectors = self.ollama.embed([raw, meta, summary])
            if len(vectors) != 3:
                raise StorageError("query embedding returned the wrong vector count")
            if on_progress:
                on_progress("embedding_complete", {
                    "status": "ok", "model": embed_model,
                    "dimension": len(vectors[0]),
                    "latency_ms": round((perf_counter() - embed_started) * 1000, 2),
                })
            return PreparedQuery(
                parsed=parsed,
                dense_vectors=dict(zip(
                    ("text_dense", "meta_dense", "summary_dense"), vectors,
                )),
            )
        except (StorageError, ValueError, TypeError) as exc:
            if on_progress:
                on_progress("embedding_complete", {
                    "status": "failed", "error": str(exc),
                    "latency_ms": round((perf_counter() - embed_started) * 1000, 2),
                })
            return PreparedQuery(parsed=parsed, dense_vectors={}, dense_error=str(exc))

    def search_prepared(
        self, *, book_id: int, version: int, prepared: PreparedQuery,
        route_top_n: int, rrf_top_n: int,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        parsed_dict = prepared.parsed.model_dump()
        raw = prepared.parsed.raw_intent
        collection = scenes_collection_name(book_id, version)
        routes: dict[str, dict[str, Any]] = {}
        degraded: list[str] = []
        specs = (
            ("text_dense", "text-dense"),
            ("meta_dense", "meta-dense"),
            ("summary_dense", "summary-dense"),
            ("text_sparse", "text-sparse"),
        )
        for route_name, vector_name in specs:
            if on_progress:
                on_progress("retrieving", {"book_id": book_id, "route": route_name})
            route_started = perf_counter()
            route: dict[str, Any] = {"status": "ok", "items": [], "latency_ms": 0}
            try:
                if vector_name == "text-sparse":
                    vector = build_sparse_vector(
                        raw, self.repository.get_token_map(book_id)
                    )
                    if not vector["indices"]:
                        route.update(status="skipped", reason="no_known_tokens")
                        routes[route_name] = route
                        continue
                else:
                    if prepared.dense_error:
                        raise StorageError(prepared.dense_error)
                    vector = prepared.dense_vectors[route_name]
                hits = self.qdrant.search(
                    collection, vector_name=vector_name, vector=vector,
                    limit=route_top_n, with_payload=True,
                )
                for point in hits:
                    payload = point.payload or {}
                    scene_id = int(payload.get("scene_id", point.id))
                    if (int(payload.get("book_id", -1)) != book_id
                            or int(payload.get("version", -1)) != version
                            or payload.get("reference_status") != "selected"):
                        raise RetrievalError("Qdrant hit is outside requested reference version")
                    route["items"].append({
                        "scene_id": scene_id, "rank": len(route["items"]) + 1,
                        "score": float(point.score),
                        "summary": payload.get("summary", ""),
                    })
            except RetrievalError:
                raise
            except (StorageError, ValueError, KeyError, TypeError) as exc:
                route.update(status="failed", error=str(exc), items=[])
                degraded.append(route_name)
            finally:
                route["latency_ms"] = round((perf_counter() - route_started) * 1000, 2)
                routes[route_name] = route
                if on_progress:
                    on_progress("route_complete", {
                        "book_id": book_id, "route": route_name,
                        "status": route["status"],
                        "count": len(route["items"]),
                        "latency_ms": route["latency_ms"],
                        "reason": route.get("reason") or route.get("error"),
                    })

        if all(route["status"] != "ok" for route in routes.values()):
            raise RetrievalError("all retrieval routes failed or were skipped")
        if on_progress:
            on_progress("fusion", {"book_id": book_id})
        fusion_started = perf_counter()
        fused = reciprocal_rank_fusion(
            routes, k=self.settings.rrf_k, limit=rrf_top_n,
        )
        for candidate in fused:
            scene = self.repository.get_scene(candidate["scene_id"])
            if (scene is None or int(scene["book_id"]) != book_id
                    or int(scene["version"]) != version
                    or scene["reference_status"] != "selected"):
                raise RetrievalError("scene lookup does not match requested reference version")
            candidate.update({
                "book_id": book_id, "version": version,
                "scene_index_in_book": int(scene["scene_index_in_book"]),
                "chapter_start_index": int(scene["chapter_start_index"]),
                "chapter_end_index": int(scene["chapter_end_index"]),
                "text_preview": str(scene["text"])[:240],
                "summary": scene["summary"],
                "style_summary": scene["style_summary"],
                "usage_hint": scene["usage_hint"],
            })
        if on_progress:
            on_progress("fusion_complete", {
                "book_id": book_id, "count": len(fused),
                "k": self.settings.rrf_k,
                "latency_ms": round((perf_counter() - fusion_started) * 1000, 2),
            })
        return {
            "book_id": book_id, "version": version,
            "raw_intent": raw, "parsed_query": parsed_dict,
            "routes": routes,
            "rrf": {"k": self.settings.rrf_k, "items": fused},
            "degraded_routes": degraded,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }
