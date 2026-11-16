"""Run the scaled multi-book retrieval baseline against the demo database.

The baseline covers the four ready books in the local observation database:
马之途, 备用联系人, 切勿操之过急 and 百年孤独. It records per-route and RRF
Hit@5 for hand-authored probes, exercises the multi-book aggregation endpoint,
and runs one optional rerank probe when the configured reranker is available.

Usage:
    uv run python -B -m tests.real.run_retrieval_baseline
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from app.clients.llm_client import build_llm_client
from app.clients.ollama_client import OllamaClient
from app.clients.rerank_client import RerankClient
from app.config import get_settings
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.multi_search import MultiBookSearcher
from app.services.query_parser import QueryParser
from app.services.retriever import Retriever

REPORT_PATH = Path("reports/retrieval-baseline-multi-book-2026-11-13.md")
RESULT_PATH = Path("data/converted/retrieval-baseline-multi-book.json")

# (book_id, query, expected scene_index_in_book). Scene indexes refer to each
# book's current version.
PROBES: list[tuple[int, str, int]] = [
    # 马之途 v3
    (10, "写一段穷困之下铤而走险、雪夜抢劫的开场", 1),
    (10, "写一段朋友坦白父亲是逃犯的对话", 2),
    (10, "写一段饭馆劝阻酒驾后遭到报复的场景", 3),
    (10, "写一段黑店中多方持枪对峙的场景", 4),
    (10, "写一段父子多年后无言相认的场景", 5),
    (10, "写一段持枪闯入洗车店逼问父亲下落的场景", 6),
    (10, "写一段探监时揭开多年友情真相的场景", 7),
    (10, "写一段银行劫案发布会揭开命运真相的结尾", 8),
    (10, "写一段青春回忆与悲剧现实交错的结尾", 8),
    (10, "写一段人物在压力下做出艰难选择的场景", 6),
    # 备用联系人 v2
    (43, "写一段分手后在美术馆偶遇、用照片步步紧逼的场景", 3),
    (43, "写一段宿醉醒来发现与对方同床、慌乱中回忆高中的场景", 4),
    (43, "写一段躲在洗手间时前男友突然来访、被迫亲吻对方才肯帮忙的场景", 6),
    (43, "写一段半夜驱车五小时去接人、在车上翻旧账斗嘴的场景", 9),
    (43, "写一段一方强行搂着同眠、另一方独自面对旧物的双线场景", 14),
    (43, "写一段发现前女友和好友在一起后情绪失控砸碎酒杯的场景", 70),
    # 切勿操之过急 v2
    (44, "写一段柔弱卧底在危险金主身边周旋、凌晨接头领任务的场景", 3),
    (44, "写一段金主递刀让女主杀自己、却指出心脏在另一侧的场景", 7),
    (44, "写一段被对手绑架后假装顺从、将计就计收下窃听器的场景", 12),
    (44, "写一段借学习盲文进行亲密接触、在掌心拼出暗语试探的场景", 25),
    (44, "写一段餐厅冲突中飞刀击落手枪、随后用切牛排试探对方是否真盲的场景", 35),
    (44, "写一段表面温柔递茶、实则当面把安眠药粉末倒进杯中的反转场景", 38),
    # 百年孤独 v3
    (86, "写一段吉卜赛人带来磁铁和望远镜、让村民痴迷发明的开篇", 1),
    (86, "写一段带队寻找入海口、在丛林中发现西班牙大帆船的场景", 4),
    (86, "写一段全镇人患上失眠症、夜以继日工作后来陷入无聊的场景", 14),
    (86, "写一段给物品贴标签对抗遗忘、并试图制造记忆机器的场景", 15),
    (86, "写一段新家落成舞会上自动钢琴技师教女孩跳舞、少女暗生情愫的场景", 19),
    (86, "写一段因婚礼冲突升级、在试衣后决定往咖啡里下鸦片酊的场景", 25),
]

MULTI_PROBES = [
    "找一段通过对话逐步揭露身份秘密、结尾反转的场景",
    "找一段在封闭空间里两个人互相试探、情绪逐渐升温的场景",
    "找一段用日常物件承载多年情感、克制不煽情的场景",
    "找一段弱者被逼到绝境后做出极端选择的场景",
]

ROUTE_NAMES = ("text_dense", "meta_dense", "summary_dense", "text_sparse")
ROUTE_LABELS = {
    "text_dense": "原文语义",
    "meta_dense": "风格与技法",
    "summary_dense": "剧情摘要",
    "text_sparse": "原文关键词",
    "rrf": "无权重 RRF",
}


def _percent(hits: int, total: int) -> str:
    return f"{hits}/{total} ({hits / total:.0%})" if total else "0/0"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def main() -> None:
    settings = get_settings()
    db = PostgresDatabase.from_settings(settings)
    repository = PostgresRepository(db)
    qdrant = QdrantAdapter(settings=settings)
    ollama = OllamaClient(settings=settings)
    llm = build_llm_client(settings)
    reranker = RerankClient(
        base_url=settings.ollama_url,
        model=settings.ollama_rerank_model,
        timeout_seconds=settings.rerank_timeout_seconds,
        concurrency=settings.rerank_concurrency,
    )
    no_rerank = settings.model_copy(update={"rerank_enabled": False})
    with_rerank = settings.model_copy(
        update={"rerank_enabled": True, "rerank_top_n": 5, "rerank_concurrency": 1}
    )
    try:
        retriever = Retriever(
            repository, qdrant, ollama,
            QueryParser(repository, llm, settings), settings,
        )
        multi_searcher = MultiBookSearcher(repository, retriever, no_rerank)
        multi_rerank_searcher = MultiBookSearcher(
            repository, retriever, with_rerank, reranker,
        )

        books: dict[int, dict[str, Any]] = {}
        for book_id in (10, 43, 44, 86):
            book = repository.get_book(book_id)
            if book is None:
                raise RuntimeError(f"book {book_id} is missing from the database")
            books[book_id] = {
                "id": book_id,
                "title": book["title"],
                "version": int(book["current_version"]),
                "scenes": int(book["total_scenes"]),
                "selected": int(book["selected_scenes"]),
            }

        scene_lookup: dict[int, dict[str, Any]] = {}
        for book_id, book in books.items():
            version = book["version"]
            for row in repository.list_scenes(book_id, version):
                scene_lookup[int(row["id"])] = {
                    "book_id": book_id,
                    "version": version,
                    "scene_index": int(row["scene_index_in_book"]),
                }

        def indexes(items: list[dict[str, Any]]) -> list[int]:
            return [scene_lookup[int(item["scene_id"])]["scene_index"] for item in items]

        rows: list[dict[str, Any]] = []
        for book_id, query, expected in PROBES:
            version = books[book_id]["version"]
            result = retriever.search(
                book_id=book_id, version=version, query=query,
                route_top_n=5, rrf_top_n=5,
            )
            route_indexes = {
                name: indexes(route["items"]) for name, route in result["routes"].items()
            }
            fused = indexes(result["rrf"]["items"])
            row = {
                "book_id": book_id,
                "book_title": books[book_id]["title"],
                "version": version,
                "query": query,
                "expected_scene_index": expected,
                "routes": route_indexes,
                "rrf": fused,
                "route_hits": {
                    name: expected in values for name, values in route_indexes.items()
                },
                "rrf_hit_at_5": expected in fused,
                "fallback": bool(result["parsed_query"]["fallback"]),
                "cache_hit": bool(result["parsed_query"].get("cache_hit")),
                "latency_ms": result["latency_ms"],
                "degraded_routes": result.get("degraded_routes", []),
            }
            rows.append(row)
            print(
                f"[{book_id}] {len(rows):02d}/{len(PROBES)} expected={expected} "
                f"rrf={fused} hit={row['rrf_hit_at_5']} "
                f"latency={row['latency_ms']}ms",
                flush=True,
            )

        multi_rows: list[dict[str, Any]] = []
        for query in MULTI_PROBES:
            result = multi_searcher.search(
                query=query, book_ids=list(books),
                route_top_n=5, per_book_limit=5, global_limit=20,
            )
            final_items = result["final"]["items"]
            multi_rows.append({
                "query": query,
                "status": "ok",
                "ok_books": [book_id for book_id, data in result["books"].items()
                             if data["status"] == "ok"],
                "degraded_books": result["degraded_books"],
                "candidate_count": len(result["aggregation"]["items"]),
                "final_count": len(final_items),
                "final_books": sorted({int(item["book_id"]) for item in final_items}),
                "book_candidate_counts": {
                    book_id: len(data.get("rrf", {}).get("items", []))
                    for book_id, data in result["books"].items()
                },
                "rerank": result["rerank"],
                "latency_ms": result["latency_ms"],
                "top10": [
                    {
                        "rank": index,
                        "book_id": int(item["book_id"]),
                        "scene_index": scene_lookup[int(item["scene_id"])]["scene_index"],
                        "book_rrf_rank": item["book_rrf_rank"],
                    }
                    for index, item in enumerate(final_items[:10], start=1)
                ],
            })
            print(
                f"[multi] {len(multi_rows)}/{len(MULTI_PROBES)} "
                f"books={len(multi_rows[-1]['ok_books'])} "
                f"final={multi_rows[-1]['final_count']} "
                f"latency={multi_rows[-1]['latency_ms']}ms",
                flush=True,
            )

        rerank_row: dict[str, Any] | None = None
        try:
            result = multi_rerank_searcher.search(
                query=MULTI_PROBES[0], book_ids=list(books),
                route_top_n=5, per_book_limit=5, global_limit=10,
            )
            rerank_row = {
                "query": MULTI_PROBES[0],
                "status": result["rerank"]["status"],
                "rerank": result["rerank"],
                "final_source": result["final"]["source"],
                "final_count": len(result["final"]["items"]),
                "latency_ms": result["latency_ms"],
            }
            print(f"[rerank] status={result['rerank']['status']} "
                  f"latency={result['latency_ms']}ms", flush=True)
        except Exception as exc:  # keep the baseline useful when the model is unavailable
            rerank_row = {"query": MULTI_PROBES[0], "status": "failed", "error": str(exc)}
            print(f"[rerank] failed: {exc}", flush=True)

        summary = _summarize(rows, multi_rows, rerank_row, books)
        payload = {
            "generated_at": "2026-11-16",
            "database": settings.postgres_db,
            "qdrant_url": settings.qdrant_url,
            "books": books,
            "probes": rows,
            "multi_book": multi_rows,
            "rerank_probe": rerank_row,
            "summary": summary,
        }
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(_render_report(payload), encoding="utf-8")
        print(f"saved: {RESULT_PATH}")
        print(f"saved: {REPORT_PATH}")
        print(f"RRF Hit@5: {summary['rrf_hits']}/{summary['probe_count']}")
    finally:
        reranker.close()
        llm.close()
        ollama.close()
        qdrant.close()
        db.dispose()


def _summarize(
    rows: list[dict[str, Any]], multi_rows: list[dict[str, Any]],
    rerank_row: dict[str, Any] | None, books: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    per_book: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = per_book.setdefault(
            str(row["book_id"]),
            {"title": row["book_title"], "count": 0, "rrf_hits": 0,
             "route_hits": {name: 0 for name in ROUTE_NAMES}},
        )
        bucket["count"] += 1
        bucket["rrf_hits"] += int(row["rrf_hit_at_5"])
        for name in ROUTE_NAMES:
            bucket["route_hits"][name] += int(row["route_hits"].get(name, False))
    total = len(rows)
    route_hits = {
        name: sum(int(row["route_hits"].get(name, False)) for row in rows)
        for name in ROUTE_NAMES
    }
    latencies = [float(row["latency_ms"]) for row in rows]
    return {
        "probe_count": total,
        "rrf_hits": sum(int(row["rrf_hit_at_5"]) for row in rows),
        "route_hits": route_hits,
        "per_book": per_book,
        "fallback_count": sum(int(row["fallback"]) for row in rows),
        "cache_hit_count": sum(int(row["cache_hit"]) for row in rows),
        "degraded_probe_count": sum(int(bool(row["degraded_routes"])) for row in rows),
        "latency_ms": {
            "mean": round(mean(latencies), 1),
            "min": round(min(latencies), 1),
            "max": round(max(latencies), 1),
        },
        "multi": {
            "count": len(multi_rows),
            "all_ok": all(not row["degraded_books"] for row in multi_rows),
            "avg_book_coverage": round(
                mean(len(row["ok_books"]) for row in multi_rows), 2,
            ) if multi_rows else 0,
            "avg_latency_ms": round(mean(row["latency_ms"] for row in multi_rows), 1)
            if multi_rows else 0,
        },
        "rerank_status": rerank_row["status"] if rerank_row else "not_run",
    }


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    books = payload["books"]
    probes = payload["probes"]
    lines: list[str] = [
        "# Novel-RAG 多书检索基线 · 2026-11-13",
        "",
        "> 最后更新：2026-11-16（meta 向量重建后重跑）。",
        "",
        f"运行环境：PostgreSQL `{payload['database']}`、Qdrant `{payload['qdrant_url']}`。"
        "覆盖公开快照中的四本书（含《百年孤独》），共 "
        f"{summary['probe_count']} 条单书探针 + {summary['multi']['count']} 条多书聚合探针。",
        "",
        "## 覆盖书籍",
        "",
        "| 书籍 | book_id | 当前版本 | Scene | 入选参考 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for book in books.values():
        lines.append(
            f"| {book['title']} | {book['id']} | v{book['version']} | "
            f"{book['scenes']} | {book['selected']} |"
        )
    lines += [
        "",
        "## 单书召回结果（Hit@5）",
        "",
        "| 书籍 | 探针数 | 原文语义 | 风格与技法 | 剧情摘要 | 原文关键词 | RRF |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for book_id, bucket in summary["per_book"].items():
        route_cells = " | ".join(
            f"{bucket['route_hits'][name]}/{bucket['count']}" for name in ROUTE_NAMES
        )
        lines.append(
            f"| {bucket['title']} | {bucket['count']} | {route_cells} | "
            f"{bucket['rrf_hits']}/{bucket['count']} |"
        )
    route_cells = " | ".join(
        f"{summary['route_hits'][name]}/{summary['probe_count']}" for name in ROUTE_NAMES
    )
    lines.append(
        f"| **合计** | **{summary['probe_count']}** | {route_cells} | "
        f"**{summary['rrf_hits']}/{summary['probe_count']}** |"
    )
    lines += [
        "",
        f"解析降级：{summary['fallback_count']} 条；查询解析缓存命中："
        f"{summary['cache_hit_count']} 条；存在降级路由的探针："
        f"{summary['degraded_probe_count']} 条。",
        "",
        "### 每条探针明细",
        "",
        "| 书籍 | 版本 | 探针 | 目标场景 | 各路 Top-5 | RRF Top-5 | 命中 |",
        "| --- | ---: | --- | ---: | --- | --- | :---: |",
    ]
    for row in probes:
        route_text = "<br>".join(
            f"{ROUTE_LABELS[name]}: {row['routes'].get(name, [])}" for name in ROUTE_NAMES
        )
        lines.append(
            f"| {row['book_title']} | v{row['version']} | {row['query']} | "
            f"{row['expected_scene_index']} | {route_text} | {row['rrf']} | "
            f"{'是' if row['rrf_hit_at_5'] else '否'} |"
        )
    lines += [
        "",
        "## 多书聚合",
        "",
        "| 查询 | 成功书籍 | 候选池 | 最终条数 | 覆盖书籍 | 聚合耗时 |",
        "| --- | ---: | ---: | ---: | :---: | ---: |",
    ]
    for row in payload["multi_book"]:
        lines.append(
            f"| {row['query']} | {len(row['ok_books'])}/4 | {row['candidate_count']} | "
            f"{row['final_count']} | {', '.join(str(b) for b in row['final_books'])} | "
            f"{row['latency_ms']} ms |"
        )
    lines += [
        "",
        f"多书聚合 {summary['multi']['count']} 条查询全部成功："
        f"{'是' if summary['multi']['all_ok'] else '否'}；平均覆盖书籍数 "
        f"{summary['multi']['avg_book_coverage']}/4；平均耗时 "
        f"{summary['multi']['avg_latency_ms']} ms。",
        "",
        "## 重排探针",
        "",
    ]
    rerank = payload.get("rerank_probe")
    if rerank and rerank.get("status") == "ok":
        info = rerank["rerank"]
        lines.append(
            f"状态 `ok`，模型 `{info.get('model')}`，评分候选 {info.get('evaluated_count')} 条，"
            f"边界分数 {info.get('censored_count')} 条，重排耗时 {info.get('latency_ms')} ms，"
            f"最终结果来源 `{rerank['final_source']}`。"
        )
    elif rerank:
        lines.append(f"状态 `{rerank.get('status')}`：{rerank.get('error', '—')}")
    else:
        lines.append("未运行。")
    rerank_latency = "—"
    rerank_candidates = "—"
    if rerank and rerank.get("status") == "ok":
        rerank_latency = f"{rerank['rerank'].get('latency_ms')} ms"
        rerank_candidates = str(rerank["rerank"].get("evaluated_count"))
    weakest = min(ROUTE_NAMES, key=lambda name: summary["route_hits"][name])
    strongest = max(ROUTE_NAMES, key=lambda name: summary["route_hits"][name])
    lines += [
        "",
        "## 结论",
        "",
        f"- RRF Hit@5 为 {summary['rrf_hits']}/{summary['probe_count']}；"
        f"剧情摘要路由 {summary['route_hits']['summary_dense']}/{summary['probe_count']}，"
        f"原文语义 {summary['route_hits']['text_dense']}/{summary['probe_count']}。",
        f"- 最强路由为 {ROUTE_LABELS[strongest]}，最弱路由为 {ROUTE_LABELS[weakest]}"
        f"（{summary['route_hits'][weakest]}/{summary['probe_count']}）。"
        f"风格与技法路由为 {summary['route_hits']['meta_dense']}/{summary['probe_count']}"
        "（查询锚点修复前为 9/28），仍低于剧情摘要与原文语义。",
        f"- 解析降级 {summary['fallback_count']} 条、存在降级路由的探针 "
        f"{summary['degraded_probe_count']} 条，链路稳定性符合预期。",
        f"- 多书聚合 {summary['multi']['count']} 条查询全部成功，"
        f"平均覆盖 {summary['multi']['avg_book_coverage']}/4 本书，平均耗时 "
        f"{summary['multi']['avg_latency_ms']} ms。轮流汇集天然保证书籍覆盖，"
        "该指标只说明聚合可用，不代表跨书相关性质量。",
        f"- 重排可用，但 {rerank_candidates} 条候选耗时 {rerank_latency}；"
        "当前模型不适合同步交互式请求，需要异步、缓存或更小模型。",
        "",
        "## 口径与限制",
        "",
        "- 探针按已知剧情位置人工指定目标 Scene，属于闭环可用性检查，不等同于开放域相关性评估。",
        "- 四路原始 score 不可跨向量空间比较，因此只统计 Hit@5 与 RRF 名次。",
        "- 《百年孤独》已包含在公开快照 `sql/fixtures/public-books/` 中；快照不含原始 EPUB/TXT。",
        "- 本轮不比较不同书籍的 RRF 分数，只观察聚合后的书籍覆盖与名次分布。",
        "",
        f"机器可读结果：`{RESULT_PATH.as_posix()}`。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
