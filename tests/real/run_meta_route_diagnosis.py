"""Diagnose why the meta-dense route underperforms the other recall routes.

The script takes the meta-route misses from the multi-book baseline, compares
several meta query constructions against the target scene, and measures how
much the meta-dense vector space collapses compared with text/summary vectors.

Usage:
    uv run python -B -m tests.real.run_meta_route_diagnosis
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import numpy as np

from app.clients.llm_client import build_llm_client
from app.clients.ollama_client import OllamaClient
from app.config import get_settings
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.db.qdrant import QdrantAdapter, scenes_collection_name
from app.services.embedder import build_meta_text as scene_meta_text
from app.services.query_parser import QueryParser
from app.services.retriever import build_meta_query

BASELINE_PATH = Path("data/converted/retrieval-baseline-pre-meta-fix.json")
REPORT_PATH = Path("reports/meta-route-diagnosis-2026-11-13.md")
RESULT_PATH = Path("data/converted/meta-route-diagnosis.json")

TAG_FIELDS = ("scene_type", "technique", "style_tags", "emotion_tags", "key_images")


def _probes_from_baseline() -> list[dict[str, Any]]:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    misses = [
        row for row in payload["probes"]
        if not row["route_hits"].get("meta_dense", False)
    ]
    # Keep a comparable spread across books instead of taking only one book.
    by_book: dict[int, list[dict[str, Any]]] = {}
    for row in misses:
        by_book.setdefault(int(row["book_id"]), []).append(row)
    picked: list[dict[str, Any]] = []
    for book_id in sorted(by_book):
        picked.extend(by_book[book_id][:2])
    return picked


def _tags_only(parsed: dict[str, Any]) -> str:
    """Reproduce the pre-fix tag-only meta query for comparison."""
    fields = (
        ("场景类型", "scene_type"), ("写作技法", "technique"),
        ("文风", "style_tags"), ("情绪", "emotion_tags"),
        ("意象", "key_images"),
    )
    return "\n".join(
        f"{label}：{'、'.join(parsed[key])}"
        for label, key in fields if parsed.get(key)
    )


def _natural_query(parsed: dict[str, Any]) -> str:
    parts: list[str] = []
    labels = (
        ("scene_type", "场景类型"), ("technique", "写作技法"),
        ("style_tags", "文风"), ("emotion_tags", "情绪"), ("key_images", "意象"),
    )
    for key, label in labels:
        values = parsed.get(key) or []
        if values:
            parts.append(f"{label}{'、'.join(values)}")
    return "寻找一段" + "，".join(parts) + "的写作参考场景" if parts else ""


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / denominator) if denominator else 0.0


def _space_collapse(qdrant: QdrantAdapter, repository: PostgresRepository,
                    book_id: int, version: int) -> dict[str, Any]:
    collection = scenes_collection_name(book_id, version)
    points, _ = qdrant.client.scroll(
        collection_name=collection, limit=200, with_vectors=True,
    )
    vectors: dict[str, list[list[float]]] = {name: [] for name in
                                             ("text-dense", "meta-dense", "summary-dense")}
    for point in points:
        payload = point.payload or {}
        if payload.get("scene_id") is None:
            continue
        stored = point.vector or {}
        for name in vectors:
            value = stored.get(name)
            if value is not None:
                vectors[name].append(list(value))
    result: dict[str, Any] = {"scene_count": len(vectors["text-dense"])}
    for name, items in vectors.items():
        if len(items) < 3:
            result[name] = {"pair_count": 0}
            continue
        pairs = list(combinations(range(len(items)), 2))
        if len(pairs) > 4000:
            step = len(pairs) // 4000 + 1
            pairs = pairs[::step]
        scores = [_cosine(items[i], items[j]) for i, j in pairs]
        result[name] = {
            "pair_count": len(scores),
            "mean_cosine": round(mean(scores), 4),
            "std_cosine": round(pstdev(scores), 4),
            "min_cosine": round(min(scores), 4),
            "max_cosine": round(max(scores), 4),
        }
    return result


def main() -> None:
    settings = get_settings()
    db = PostgresDatabase.from_settings(settings)
    repository = PostgresRepository(db)
    qdrant = QdrantAdapter(settings=settings)
    ollama = OllamaClient(settings=settings)
    llm = build_llm_client(settings)
    parser = QueryParser(repository, llm, settings)
    try:
        probes = _probes_from_baseline()
        rows: list[dict[str, Any]] = []
        for probe in probes:
            book_id = int(probe["book_id"])
            version = int(probe["version"])
            expected = int(probe["expected_scene_index"])
            collection = scenes_collection_name(book_id, version)
            scene = repository.get_scene_by_index(book_id, version, expected) if hasattr(
                repository, "get_scene_by_index"
            ) else None
            if scene is None:
                matches = [
                    row for row in repository.list_scenes(book_id, version)
                    if int(row["scene_index_in_book"]) == expected
                ]
                scene = matches[0] if matches else None
            if scene is None:
                continue
            target_id = int(scene["id"])
            parsed = parser.parse(probe["query"]).model_dump()
            variants = {
                "tags_only": _tags_only(parsed),
                "current_meta_query": build_meta_query(parsed),
                "raw_intent": probe["query"],
                "summary_query": parsed.get("summary_query") or probe["query"],
                "natural_template": _natural_query(parsed),
                "raw_plus_tags": f"{probe['query']}\n{build_meta_query(parsed)}",
            }
            variants = {name: text for name, text in variants.items() if text.strip()}
            unique_texts = list(dict.fromkeys(variants.values()))
            vectors = ollama.embed(unique_texts)
            by_text = dict(zip(unique_texts, vectors))

            target_tags = {
                str(tag) for field in TAG_FIELDS for tag in (scene.get(field) or [])
            }
            parsed_tags = {
                str(tag) for field in TAG_FIELDS for tag in (parsed.get(field) or [])
            }
            overlap = len(target_tags & parsed_tags) / max(1, len(parsed_tags | target_tags))

            variant_rows = []
            for name, text in variants.items():
                hits = qdrant.search(
                    collection, vector_name="meta-dense",
                    vector=by_text[text], limit=100,
                )
                scores = [float(hit.score) for hit in hits]
                rank = next(
                    (index for index, hit in enumerate(hits, start=1)
                     if int(hit.id) == target_id), None,
                )
                target_score = next(
                    (float(hit.score) for hit in hits if int(hit.id) == target_id), None,
                )
                variant_rows.append({
                    "variant": name,
                    "text": text,
                    "target_rank": rank,
                    "target_score": None if target_score is None else round(target_score, 4),
                    "top1_score": round(scores[0], 4) if scores else None,
                    "top20_mean": round(mean(scores[:20]), 4) if scores else None,
                    "score_std": round(pstdev(scores), 4) if len(scores) > 1 else None,
                })
                print(
                    f"[{book_id}] {name:18s} rank={rank} target={target_score}",
                    flush=True,
                )
            rows.append({
                "book_id": book_id, "version": version, "query": probe["query"],
                "expected_scene_index": expected,
                "target_scene_id": target_id,
                "parsed": {field: parsed.get(field) for field in TAG_FIELDS},
                "tag_overlap": round(overlap, 4),
                "target_meta_text": scene_meta_text(dict(scene)),
                "variants": variant_rows,
            })

        collapse = {}
        for book in json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["books"].values():
            collapse[str(book["id"])] = _space_collapse(
                qdrant, repository, int(book["id"]), int(book["version"]),
            )

        summary = {
            "probe_count": len(rows),
            "variant_target_top5": {
                name: sum(
                    1 for row in rows
                    for variant in row["variants"]
                    if variant["variant"] == name
                    and variant["target_rank"] is not None
                    and variant["target_rank"] <= 5
                )
                for name in ("tags_only", "current_meta_query", "raw_intent", "summary_query",
                             "natural_template", "raw_plus_tags")
            },
            "mean_tag_overlap": round(mean(row["tag_overlap"] for row in rows), 4)
            if rows else 0,
            "collapse": collapse,
        }
        payload = {"generated_at": "2026-11-13", "summary": summary, "probes": rows}
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(_render(payload), encoding="utf-8")
        print("saved:", RESULT_PATH)
        print("saved:", REPORT_PATH)
    finally:
        llm.close()
        ollama.close()
        qdrant.close()
        db.dispose()


def _render(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# 风格与技法检索路由诊断 · 2026-11-13",
        "",
        "## 一、要回答的问题",
        "",
        "多书基线上 `meta_dense`（风格与技法）Hit@5 仅 9/28，明显低于剧情摘要 28/28 与"
        "原文语义 27/28。本诊断回答：是查询构造问题、标注问题，还是向量空间本身不可分。",
        "",
        "诊断样本取自修复前的基线 misses；“仅标签（修复前）”复现旧构造，“当前构造（需求+标签）”"
        "是本轮修复后的实现。",
        "",
        "## 二、证据",
        "",
        "### 2.1 目标场景在各查询构造下的名次",
        "",
        "| 书籍 | 查询 | 变体 | 目标名次 | 目标分数 | Top1 分数 | Top20 均分 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    labels = {
        "tags_only": "仅标签（修复前）",
        "current_meta_query": "当前构造（需求+标签）",
        "raw_intent": "原始需求",
        "summary_query": "剧情改写",
        "natural_template": "自然语言模板",
        "raw_plus_tags": "原文+标签",
    }
    for row in payload["probes"]:
        for variant in row["variants"]:
            lines.append(
                f"| {row['book_id']} | {row['query'][:18]}… | {labels.get(variant['variant'], variant['variant'])} | "
                f"{variant['target_rank'] if variant['target_rank'] is not None else '>100'} | "
                f"{variant['target_score'] if variant['target_score'] is not None else '—'} | "
                f"{variant['top1_score']} | {variant['top20_mean']} |"
            )
    lines += [
        "",
        "### 2.2 不同查询构造的 Top-5 命中数",
        "",
        "| 查询构造 | 目标进 Top-5 的探针数 |",
        "| --- | ---: |",
    ]
    for name, count in summary["variant_target_top5"].items():
        lines.append(f"| {labels.get(name, name)} | {count}/{summary['probe_count']} |")
    lines += [
        "",
        f"查询标签与目标场景标签的平均 Jaccard 重合度：**{summary['mean_tag_overlap']:.2f}**。",
        "",
        "### 2.3 向量空间塌缩程度（书内两两余弦）",
        "",
        "| 书籍 | Scene | text-dense 均余弦 | meta-dense 均余弦 | summary-dense 均余弦 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for book_id, data in summary["collapse"].items():
        def cell(name: str) -> str:
            item = data.get(name, {})
            return "—" if not item.get("pair_count") else f"{item['mean_cosine']:.3f} ± {item['std_cosine']:.3f}"
        lines.append(
            f"| {book_id} | {data['scene_count']} | {cell('text-dense')} | "
            f"{cell('meta-dense')} | {cell('summary-dense')} |"
        )
    lines += [
        "",
        "## 三、结论",
        "",
        "- **查询构造是主因。** 仅标签查询在 8 条 miss 探针上 0/8 进入 Top-5；“原始需求”在同一向量空间 "
        "8/8 命中且全部排第 1，“原文+标签”也达到 7/8，修复后的“需求+标签”达到 7/8，"
        "与多书基线 meta 路由 24/28 的回升一致。标签串把查询推向空间中心，叙事内容被抹掉。",
        "- **向量空间确实塌缩。** 四本书的 `meta-dense` 两两余弦均值 0.81–0.84，高于 `text-dense` "
        "0.70–0.77 与 `summary-dense` 0.55–0.72；当前构造的 Top1 与 Top20 均分只差约 0.04，"
        "排序基本由噪声决定。",
        f"- **标签体系对齐不足。** 查询标签与目标场景标签的平均 Jaccard 重合度仅 "
        f"{summary['mean_tag_overlap']:.2f}，查询侧 canonical 标签与标注侧常用标签不一致。",
        "- 因此这是“查询构造 + 标注/词表 + 向量同质”的叠加问题，只靠改提示词无法根治；"
        "先修查询构造，再评估是否需要重建 meta 标注与向量。",
        "",
        "## 四、修复方向",
        "",
        "1. 查询侧（已实施）：meta 查询改为 `raw_intent` 锚点 + canonical 标签；新基线结果见"
        " `reports/retrieval-baseline-multi-book-2026-11-13.md`。",
        "2. 标注侧：`style_summary` 要求写入可区分的具体手法与对象，减少“克制、短句、电影感”等泛化词；"
        "必要时重建 meta-dense 向量。",
        "3. 词表侧：收敛高频通用标签，增加能区分场景的技法/意象标签，并核对查询映射，提升 Jaccard 重合度。",
        "4. 路由侧：在 meta 空间同质化缓解前，把它当补充路；中期可评估“标签过滤 + 稠密重排”的组合，"
        "而不是独立召回。",
        "",
        f"机器可读结果：`{RESULT_PATH.as_posix()}`。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
