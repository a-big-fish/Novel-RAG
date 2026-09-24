from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.splitter import split_chapters_into_scenes
from app.utils.text import split_chapters


DEMO_DIR = ROOT / "demo"
SOURCE_PATH = ROOT / "data" / "books" / "micro-novel-smoke.txt"
VOCAB_PATH = ROOT / "app" / "data" / "tag_vocab" / "v1.json"
BOOK_ID = 20


def load_display_names() -> dict[str, dict[str, str]]:
    rows = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("status", "active") != "active":
            continue
        namespace = str(row["namespace"])
        canonical = str(row["canonical_key"])
        result.setdefault(namespace, {}).setdefault(canonical, str(row["display_name"]))
    return result


def scroll_scenes(qdrant: QdrantAdapter, collection: str) -> dict[int, dict[str, Any]]:
    records, _ = qdrant.client.scroll(
        collection,
        limit=1000,
        with_payload=True,
        with_vectors=True,
    )
    return {
        int((record.payload or {})["scene_index_in_book"]): {
            "payload": dict(record.payload or {}),
            "vectors": record.vector,
        }
        for record in records
    }


def dense_vector(record: dict[str, Any], name: str) -> list[float]:
    vector = record["vectors"]
    if not isinstance(vector, dict):
        raise TypeError(f"{name} is not a named vector")
    value = vector[name]
    return [float(item) for item in value]


def display_values(
    values: list[str],
    namespace: str,
    display_names: dict[str, dict[str, str]],
) -> list[str]:
    return [display_names.get(namespace, {}).get(value, value) for value in values]


def tag_counter(
    scenes: list[dict[str, Any]],
    key: str,
    display_key: str,
) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for scene in scenes:
        raw_value = scene[key] or []
        values = [raw_value] if isinstance(raw_value, str) else list(raw_value)
        if not values:
            continue
        display_value = scene[display_key]
        display_values_for_scene = (
            [display_value] if isinstance(display_value, str) else list(display_value)
        )
        # 展示名与原始键一一对应，优先使用展示名；长度不匹配时退回原始键。
        labels = (
            display_values_for_scene
            if len(display_values_for_scene) == len(values)
            else values
        )
        counter.update(labels)
    return [
        {"name": name, "count": count}
        for name, count in counter.most_common()
    ]


def pca_points(vectors: np.ndarray) -> list[dict[str, float]]:
    centered = vectors - vectors.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    coords = centered @ vt[:2].T
    minimum = coords.min(axis=0)
    maximum = coords.max(axis=0)
    span = np.where(maximum - minimum == 0, 1.0, maximum - minimum)
    normalized = (coords - minimum) / span
    return [
        {"x": round(float(point[0]), 5), "y": round(float(point[1]), 5)}
        for point in normalized
    ]


def top_neighbors(
    vectors: np.ndarray,
    count: int = 3,
) -> dict[int, list[tuple[int, float]]]:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    safe_norms = np.where(norms == 0, 1.0, norms)
    normalized = vectors / safe_norms
    similarity = normalized @ normalized.T
    np.fill_diagonal(similarity, -np.inf)
    result: dict[int, list[tuple[int, float]]] = {}
    for row_index in range(similarity.shape[0]):
        ordered = np.argsort(similarity[row_index])[::-1][:count]
        result[row_index] = [
            (int(index), round(float(similarity[row_index][index]), 4))
            for index in ordered
        ]
    return result


def compact_diff(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    display_names: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    if before is None or after is None:
        return []
    labels = {
        "scene_type": ("场景类型", "scene_type"),
        "technique": ("写作技法", "technique"),
        "style_tags": ("风格标签", "style"),
        "emotion_tags": ("情绪标签", "emotion"),
        "narrative_func": ("叙事功能", "narrative_func"),
    }
    diff: list[dict[str, str]] = []
    for key, (label, namespace) in labels.items():
        namespace_display = display_names.get(namespace, {})
        before_value = before.get(key)
        after_value = after.get(key)
        before_set = set(before_value or [])
        after_set = set(after_value or [])
        if before_set != after_set:
            diff.append(
                {
                    "field": label,
                    "added": "、".join(
                        namespace_display.get(item, item)
                        for item in sorted(after_set - before_set)
                    ),
                    "removed": "、".join(
                        namespace_display.get(item, item)
                        for item in sorted(before_set - after_set)
                    ),
                }
            )
    return diff


def main() -> None:
    settings = Settings()
    display_names = load_display_names()
    raw_text = SOURCE_PATH.read_text(encoding="utf-8-sig")
    chapters = split_chapters(raw_text)
    scene_drafts = split_chapters_into_scenes(chapters)
    if len(chapters) != 9 or len(scene_drafts) != 27:
        raise RuntimeError(
            f"unexpected sample shape: chapters={len(chapters)}, scenes={len(scene_drafts)}"
        )

    qdrant = QdrantAdapter(settings=settings)
    try:
        v1 = scroll_scenes(qdrant, f"scenes_book_{BOOK_ID}_v1")
        v2 = scroll_scenes(qdrant, f"scenes_book_{BOOK_ID}_v2")
        c1 = qdrant.client.get_collection(f"scenes_book_{BOOK_ID}_v1")
        c2 = qdrant.client.get_collection(f"scenes_book_{BOOK_ID}_v2")
    finally:
        qdrant.close()

    ordered = [v2[index] for index in range(1, 28)]
    vectors = np.array(
        [dense_vector(record, "summary-dense") for record in ordered],
        dtype=np.float64,
    )
    points = pca_points(vectors)
    neighbors = top_neighbors(vectors)

    scenes: list[dict[str, Any]] = []
    for index, (draft, record, point) in enumerate(
        zip(scene_drafts, ordered, points, strict=True),
        start=1,
    ):
        payload = record["payload"]
        scenes.append(
            {
                "index": index,
                "scene_id": payload["scene_id"],
                "chapter_start": payload["chapter_start_index"],
                "chapter_end": payload["chapter_end_index"],
                "text": draft.text,
                "char_count": draft.char_count,
                "summary": payload.get("summary") or "",
                "style_summary": payload.get("style_summary") or "",
                "usage_hint": payload.get("usage_hint") or "",
                "scene_type": payload.get("scene_type") or [],
                "scene_type_display": display_values(
                    payload.get("scene_type") or [], "scene_type", display_names
                ),
                "technique": payload.get("technique") or [],
                "technique_display": display_values(
                    payload.get("technique") or [], "technique", display_names
                ),
                "style_tags": payload.get("style_tags") or [],
                "style_tags_display": display_values(
                    payload.get("style_tags") or [], "style", display_names
                ),
                "emotion_tags": payload.get("emotion_tags") or [],
                "emotion_tags_display": display_values(
                    payload.get("emotion_tags") or [], "emotion", display_names
                ),
                "key_images": payload.get("key_images") or [],
                "key_images_display": display_values(
                    payload.get("key_images") or [], "image", display_names
                ),
                "narrative_func": payload.get("narrative_func") or "",
                "narrative_func_display": display_names.get(
                    "narrative_func", {}
                ).get(payload.get("narrative_func") or "", payload.get("narrative_func") or ""),
                "x": point["x"],
                "y": point["y"],
                "neighbors": [
                    {"index": value + 1, "score": score}
                    for value, score in neighbors[index - 1]
                ],
                "chapter_title": next(
                    (
                        chapter.title
                        for chapter in chapters
                        if chapter.chapter_index == payload["chapter_start_index"]
                    ),
                    "",
                ),
            }
        )

    version_diff: list[dict[str, Any]] = []
    for index in range(1, 28):
        before = v1.get(index, {}).get("payload")
        after = v2.get(index, {}).get("payload")
        version_diff.append(
            {
                "index": index,
                "chapter_start": (after or before or {}).get("chapter_start_index", 0),
                "chapter_end": (after or before or {}).get("chapter_end_index", 0),
                "before_narrative": display_names.get("narrative_func", {}).get(
                    (before or {}).get("narrative_func", ""),
                    (before or {}).get("narrative_func", ""),
                ),
                "after_narrative": display_names.get("narrative_func", {}).get(
                    (after or {}).get("narrative_func", ""),
                    (after or {}).get("narrative_func", ""),
                ),
                "before_emotions": display_values(
                    (before or {}).get("emotion_tags") or [], "emotion", display_names
                ),
                "after_emotions": display_values(
                    (after or {}).get("emotion_tags") or [], "emotion", display_names
                ),
                "changes": compact_diff(before, after, display_names),
            }
        )

    database = PostgresDatabase.from_settings(settings, test=True)
    repository = PostgresRepository(database)
    try:
        book33 = repository.get_book(33)
        scenes33 = repository.list_scenes(33, 1) if book33 else []
        jobs33 = repository.list_jobs(33) if book33 else []
        runtime_sample = {
            "book_id": 33 if book33 else None,
            "title": book33["title"] if book33 else "",
            "status": book33["status"] if book33 else "",
            "chapters": book33["total_chapters"] if book33 else 0,
            "scenes": book33["total_scenes"] if book33 else 0,
            "annotated": sum(
                1 for row in scenes33 if row["annotate_status"] == "annotated"
            ),
            "indexed": sum(
                1 for row in scenes33 if row["index_status"] == "indexed"
            ),
            "jobs": [
                {
                    "stage": row["stage"],
                    "status": row["status"],
                    "done_items": row["done_items"],
                }
                for row in jobs33
            ],
        }
    finally:
        database.dispose()

    chapter_rows: list[dict[str, Any]] = []
    for chapter in chapters:
        chapter_scenes = [
            scene
            for scene in scenes
            if scene["chapter_start"] == chapter.chapter_index
        ]
        chapter_rows.append(
            {
                "index": chapter.chapter_index,
                "title": chapter.title,
                "char_count": chapter.char_count,
                "scene_count": len(chapter_scenes),
                "scene_indexes": [scene["index"] for scene in chapter_scenes],
            }
        )

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": {
            "name": "雨夜微型样本",
            "source_path": str(SOURCE_PATH),
            "source_chars": len(raw_text),
            "chapters": len(chapters),
            "scenes": len(scenes),
            "book_id": BOOK_ID,
            "source_collection": f"scenes_book_{BOOK_ID}_v2",
            "compare_collection": f"scenes_book_{BOOK_ID}_v1",
            "vector_dimension": int(c1.config.params.vectors["summary-dense"].size),
            "vectors": {
                "dense": sorted(c2.config.params.vectors.keys()),
                "sparse": sorted(c2.config.params.sparse_vectors.keys()),
            },
            "points": int(c2.points_count or 0),
            "collection_status": str(c2.status),
            "provenance": (
                "真实 Qdrant 标注与向量数据；正文来自 4112 字合成微型小说。"
                "book_id=20 的 PostgreSQL 记录已被测试清理，演示数据由 Qdrant payload "
                "与原始 TXT 对齐导出。"
            ),
        },
        "runtime_sample": runtime_sample,
        "chapters": chapter_rows,
        "scenes": scenes,
        "stats": {
            "scene_type": tag_counter(
                scenes, "scene_type", "scene_type_display"
            ),
            "technique": tag_counter(
                scenes, "technique", "technique_display"
            ),
            "style": tag_counter(scenes, "style_tags", "style_tags_display"),
            "emotion": tag_counter(
                scenes, "emotion_tags", "emotion_tags_display"
            ),
            "image": tag_counter(scenes, "key_images", "key_images_display"),
            "narrative": tag_counter(
                scenes, "narrative_func", "narrative_func_display"
            ),
        },
        "version_diff": version_diff,
    }

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    (DEMO_DIR / "data.json").write_text(json_text + "\n", encoding="utf-8")
    (DEMO_DIR / "data.js").write_text(
        "window.BIZHEN_DEMO = " + json_text + ";\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(DEMO_DIR / "data.js"),
                "chapters": len(chapters),
                "scenes": len(scenes),
                "vectors": payload["dataset"]["vectors"],
                "runtime_sample": runtime_sample,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
