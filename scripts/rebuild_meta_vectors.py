"""Rebuild meta-dense vectors from concrete style signatures.

The original meta vectors were embedded from generic style labels and collapsed
into a near-uniform space. This script:

1. asks the LLM for a scene-specific style signature and stores it under
   ``scenes.meta_json.style_v1`` (idempotent; reruns skip existing signatures);
2. rebuilds the ``meta-dense`` vector from the signature and upserts the point
   while preserving the other vectors and payload;
3. reports the pairwise-cosine collapse before and after.

Usage:
    uv run python -B -m scripts.rebuild_meta_vectors --book-ids 10 43 44 86
    uv run python -B -m scripts.rebuild_meta_vectors --limit 3 --dry-run
"""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from app.clients.llm_client import build_llm_client
from app.clients.ollama_client import OllamaClient
from app.config import get_settings
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.db.qdrant import QdrantAdapter, scenes_collection_name
from app.services.embedder import Embedder, build_meta_text, _style_signature
from app.services.style_signature import StyleSigner

RESULT_PATH = Path("data/converted/meta-rebuild-2026-11-16.json")


def _sparse_payload(value: Any) -> dict[str, list]:
    if hasattr(value, "indices"):
        return {"indices": list(value.indices), "values": list(value.values)}
    return {"indices": list(value["indices"]), "values": list(value["values"])}


def _mean_pairwise(vectors: list[list[float]]) -> float | None:
    if len(vectors) < 3:
        return None
    pairs = list(combinations(range(len(vectors)), 2))
    if len(pairs) > 1500:
        pairs = pairs[:: len(pairs) // 1500 + 1]
    scores = []
    for left, right in pairs:
        a, b = np.asarray(vectors[left]), np.asarray(vectors[right])
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denominator:
            scores.append(float(a @ b / denominator))
    return round(float(np.mean(scores)), 4) if scores else None


def _collapse(
    qdrant: QdrantAdapter, book_id: int, version: int, *, vector_name: str,
    limit: int = 40,
) -> float | None:
    points, _ = qdrant.client.scroll(
        collection_name=scenes_collection_name(book_id, version),
        limit=limit, with_vectors=True,
    )
    vectors = []
    for point in points:
        stored = point.vector or {}
        value = stored.get(vector_name)
        if value is not None:
            vectors.append(list(value))
    return _mean_pairwise(vectors)


def _ready_books(repository: PostgresRepository, book_ids: list[int]) -> list[dict[str, Any]]:
    books = []
    for book in repository.list_books():
        book_id = int(book["id"])
        if book_ids and book_id not in book_ids:
            continue
        if str(book["status"]) != "ready" or int(book["current_version"] or 0) <= 0:
            continue
        books.append({"id": book_id, "title": book["title"],
                      "version": int(book["current_version"])})
    return books


def rebuild(
    *, book_ids: list[int], limit: int | None, workers: int | None,
    force: bool, skip_sign: bool, dry_run: bool,
) -> dict[str, Any]:
    settings = get_settings()
    database = PostgresDatabase.from_settings(settings)
    repository = PostgresRepository(database)
    qdrant = QdrantAdapter(settings=settings)
    ollama = OllamaClient(settings=settings)
    llm = build_llm_client(settings)
    signer = StyleSigner(repository=repository, llm_client=llm, settings=settings)
    embedder = Embedder(repository=repository, ollama_client=ollama, settings=settings)
    report: dict[str, Any] = {"books": {}, "dry_run": dry_run}
    try:
        for book in _ready_books(repository, book_ids):
            book_id, version = book["id"], book["version"]
            before = _collapse(qdrant, book_id, version, vector_name="meta-dense")
            sign_stats = None
            if not skip_sign:
                sign_stats = signer.sign_book(
                    book_id, version, limit=limit, workers=workers, force=force,
                )
            scenes = [
                dict(row) for row in repository.list_scenes(
                    book_id, version,
                    reference_status="selected", annotate_status="annotated",
                )
            ]
            if limit is not None:
                scenes = scenes[:limit]
            signed = [scene for scene in scenes if _style_signature(scene)]
            embedded = 0
            if not dry_run:
                collection = scenes_collection_name(book_id, version)
                for scene in signed:
                    scene_id = int(scene["id"])
                    point = qdrant.get_point(collection, scene_id, with_vectors=True)
                    if point is None or not point.vector:
                        continue
                    dense = {
                        name: list(point.vector[name])
                        for name in ("text-dense", "summary-dense")
                    }
                    dense["meta-dense"] = embedder.embed_cached(build_meta_text(scene))
                    vector = {
                        **dense,
                        "text-sparse": _sparse_payload(point.vector["text-sparse"]),
                    }
                    qdrant.upsert_scene_points(collection, [{
                        "id": scene_id, "vector": vector,
                        "payload": dict(point.payload or {}),
                    }])
                    embedded += 1
            after = _collapse(qdrant, book_id, version, vector_name="meta-dense")
            report["books"][str(book_id)] = {
                "title": book["title"], "version": version,
                "sign_stats": sign_stats, "candidates": len(signed),
                "embedded": embedded,
                "meta_collapse_before": before, "meta_collapse_after": after,
            }
            print(
                f"[{book_id}] signed={len(signed)} embedded={embedded} "
                f"collapse {before} -> {after}", flush=True,
            )
    finally:
        llm.close()
        ollama.close()
        qdrant.close()
        database.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-ids", type=int, nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=None,
                        help="per-book scene limit (useful for sampling)")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--force", action="store_true",
                        help="re-run the LLM even when a signature exists")
    parser.add_argument("--skip-sign", action="store_true",
                        help="only re-embed scenes that already have a signature")
    parser.add_argument("--dry-run", action="store_true",
                        help="sign but do not embed or upsert")
    args = parser.parse_args()
    report = rebuild(
        book_ids=args.book_ids, limit=args.limit, workers=args.workers,
        force=args.force, skip_sign=args.skip_sign, dry_run=args.dry_run,
    )
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"saved: {RESULT_PATH}")


if __name__ == "__main__":
    main()
