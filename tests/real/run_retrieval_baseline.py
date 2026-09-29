"""Run ten real single-book retrieval probes against the isolated test database."""

from __future__ import annotations

import json
from pathlib import Path

from app.clients.llm_client import build_llm_client
from app.clients.ollama_client import OllamaClient
from app.config import get_settings
from app.db.postgres import PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.query_parser import QueryParser
from app.services.retriever import Retriever
from tests.integration.support import build_test_database


PROBES = [
    ("写一段穷困之下铤而走险、雪夜抢劫的开场", 1),
    ("写一段朋友坦白父亲是逃犯的对话", 2),
    ("写一段饭馆劝阻酒驾后遭到报复的场景", 3),
    ("写一段黑店中多方持枪对峙的场景", 4),
    ("写一段父子多年后无言相认的场景", 5),
    ("写一段通过细节揭穿冒牌父亲的反转", 6),
    ("写一段寻父未果后走向绝望的场景", 7),
    ("写一段探监时揭开多年友情真相的场景", 8),
    ("写一段青春回忆与悲剧现实交错的结尾", 10),
    ("写一段人物在压力下做出艰难选择的场景", 6),
]


def main() -> None:
    settings = get_settings().model_copy(
        update={"postgres_test_db": "novel-rag-test-2"}
    )
    database = build_test_database()
    repository = PostgresRepository(database)
    qdrant = QdrantAdapter(settings=settings)
    ollama = OllamaClient(settings=settings)
    llm = build_llm_client(settings)
    try:
        retriever = Retriever(
            repository, qdrant, ollama,
            QueryParser(repository, llm, settings), settings,
        )
        rows = []
        for query, expected_index in PROBES:
            result = retriever.search(
                book_id=10, version=1, query=query,
                route_top_n=5, rrf_top_n=5,
            )
            def indexes(items):
                return [int(repository.get_scene(item["scene_id"])["scene_index_in_book"])
                        for item in items]
            routes = {name: indexes(route["items"])
                      for name, route in result["routes"].items()}
            fused = indexes(result["rrf"]["items"])
            rows.append({
                "query": query, "expected_scene_index": expected_index,
                "routes": routes, "rrf": fused,
                "rrf_hit_at_5": expected_index in fused,
                "fallback": result["parsed_query"]["fallback"],
                "latency_ms": result["latency_ms"],
            })
            print(f"{len(rows):02d}/10 expected={expected_index} "
                  f"rrf={fused} hit={expected_index in fused}", flush=True)
        path = Path("data/converted/retrieval-baseline-ma-zhitu.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"RRF Hit@5: {sum(row['rrf_hit_at_5'] for row in rows)}/10")
        print(f"saved: {path}")
    finally:
        llm.close()
        ollama.close()
        qdrant.close()
        database.dispose()


if __name__ == "__main__":
    main()
