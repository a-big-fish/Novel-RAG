from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.clients.llm_client import JsonLLMClient, build_llm_client
from app.clients.ollama_client import OllamaClient
from app.config import Settings, get_settings
from app.db.postgres import PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.services.annotator import Annotator
from app.services.embedder import Embedder
from app.services.llm_splitter import LLMSceneSplitter
from app.services.reference_evaluator import ReferenceEvaluator
from app.services.sparse import build_sparse_vector, collect_doc_frequencies
from app.services.tagger import TagVocabulary
from app.utils.epub import CONVERTER_VERSION, convert_with_cache, sha256_file
from app.utils.errors import NovelRagError
from app.utils.text import clean_text, split_chapters

logger = logging.getLogger(__name__)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            if content and not content.endswith("\n"):
                stream.write("\n")
        Path(temp_name).replace(path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


class Indexer:
    """Offline upper-pipeline orchestrator."""

    def __init__(
        self,
        *,
        repository: PostgresRepository,
        qdrant: QdrantAdapter,
        llm_client: JsonLLMClient,
        ollama_client: OllamaClient,
        vocabulary: TagVocabulary,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.qdrant = qdrant
        self.llm_client = llm_client
        self.ollama_client = ollama_client
        self.vocabulary = vocabulary
        self.settings = settings or get_settings()

    @classmethod
    def from_settings(
        cls,
        repository: PostgresRepository,
        *,
        vocabulary_path: Path | None = None,
        settings: Settings | None = None,
    ) -> "Indexer":
        settings = settings or get_settings()
        root = Path(__file__).resolve().parents[1]
        vocabulary_path = (
            vocabulary_path
            or root / "data" / "tag_vocab" / f"{settings.tag_vocab_version}.json"
        )
        return cls(
            repository=repository,
            qdrant=QdrantAdapter(settings=settings),
            llm_client=build_llm_client(settings),
            ollama_client=OllamaClient(settings=settings),
            vocabulary=TagVocabulary.from_json(vocabulary_path),
            settings=settings,
        )

    def close(self) -> None:
        self.llm_client.close()
        self.ollama_client.close()
        self.qdrant.close()

    def _run_job(
        self,
        book_id: int,
        stage: str,
        operation: Callable[..., Any],
        *,
        total_items: int = 0,
        progress_operation: bool = False,
    ) -> Any:
        job_id = self.repository.create_job(
            book_id,
            stage,
            total_items=total_items,
        )
        progress_done = 0
        progress_total = 0

        def report_progress(done: int, total: int) -> None:
            nonlocal progress_done, progress_total
            progress_done, progress_total = done, total
            self.repository.update_job(
                job_id, done_items=done, total_items=total,
            )

        try:
            result = (
                operation(report_progress) if progress_operation else operation()
            )
        except Exception as exc:
            self.repository.update_job(
                job_id,
                status="failed",
                error_message=str(exc)[:2000],
                finished=True,
            )
            raise
        done_items = (
            int(result.get("total", total_items)) if isinstance(result, Mapping)
            else len(result) if isinstance(result, list)
            else total_items
        )
        if progress_operation and progress_total:
            done_items = progress_done
        self.repository.update_job(
            job_id,
            status="completed",
            total_items=(
                progress_total if progress_operation and progress_total
                else done_items if isinstance(result, list) and not total_items
                else None
            ),
            done_items=done_items,
            finished=True,
        )
        return result

    def _converted_txt(self, book: Mapping[str, Any]) -> Path:
        source_path = Path(str(book["source_path"])).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"source book not found: {source_path}")

        source_format = str(book["source_format"])
        if source_format == "epub":
            path, _cache_hit = convert_with_cache(
                source_path,
                self.settings.data_converted_dir,
                converter_version=self._epub_converter_version(),
            )
            return path

        if source_format != "txt":
            raise ValueError(f"unsupported source format: {source_format}")

        # Hash the current file instead of trusting the registration row. A
        # source file may have been replaced after registration; using the
        # current content keeps the local TXT cache content-addressed.
        source_sha = sha256_file(source_path)
        output_path = (
            self.settings.data_converted_dir
            / f"{source_sha}.{self.settings.epub_converter_version}.txt"
        )
        if output_path.is_file() and output_path.stat().st_size > 0:
            return output_path
        raw = source_path.read_text(encoding="utf-8-sig")
        _atomic_write_text(output_path, clean_text(raw))
        return output_path

    def _epub_converter_version(self) -> str:
        # Include the implementation version even when a local .env still has
        # the previous EPUB_CONVERTER_VERSION, so stale TXT is never reused.
        return f"{self.settings.epub_converter_version}.{CONVERTER_VERSION}"

    def _split_and_store(
        self,
        book_id: int,
        version: int,
        txt_path: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        text = txt_path.read_text(encoding="utf-8")
        chapter_drafts = split_chapters(text)
        if not chapter_drafts:
            raise NovelRagError("book contains no readable chapters")

        scene_drafts = LLMSceneSplitter(
            self.llm_client, settings=self.settings, progress=progress,
        ).split(chapter_drafts)
        if not scene_drafts:
            raise NovelRagError("book contains no readable scenes")

        # LLM splitting can fail. Keep the previous chapters until all scene
        # boundaries have been validated, so a failed request does not replace
        # the active book's chapter text with an incomplete new version.
        self.repository.replace_chapters(
            book_id,
            [
                {
                    "chapter_index": chapter.chapter_index,
                    "title": chapter.title,
                    "raw_text": chapter.text,
                    "char_count": chapter.char_count,
                    "start_paragraph_index": chapter.start_paragraph_index,
                    "end_paragraph_index": chapter.end_paragraph_index,
                }
                for chapter in chapter_drafts
            ],
        )

        # A retry may reuse the same version after a partial failure. Recreate
        # the version collection so stale points can never survive the retry.
        self.qdrant.create_scenes_collection(book_id, version, recreate=True)
        scene_ids = self.repository.upsert_scenes(
            book_id,
            version,
            [
                {
                    "scene_index_in_book": scene.scene_index_in_book,
                    "chapter_start_index": scene.chapter_start_index,
                    "chapter_end_index": scene.chapter_end_index,
                    "text": scene.text,
                    "char_count": scene.char_count,
                    "split_reason": scene.split_reason,
                    "is_cross_chapter": scene.is_cross_chapter,
                    "reference_status": "unevaluated",
                    "reference_score": 0.0,
                    "reference_reason": "",
                    "reference_prompt_version": "",
                    "reference_rule_version": "",
                    "reference_meta_json": {},
                    "annotate_status": "pending",
                    "index_status": "pending",
                    "is_active": False,
                }
                for scene in scene_drafts
            ],
        )
        scene_rows = self.repository.list_scenes(book_id, version)
        by_index = {int(row["id"]): row for row in scene_rows}
        return [dict(by_index[scene_id]) for scene_id in scene_ids]

    def _build_points(
        self,
        *,
        book_id: int,
        version: int,
        scene_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        doc_freq = collect_doc_frequencies(row["text"] for row in scene_rows)
        token_to_id = self.repository.sync_token_map(book_id, doc_freq)
        embedder = Embedder(
            repository=self.repository,
            ollama_client=self.ollama_client,
            settings=self.settings,
        )
        points: list[dict[str, Any]] = []
        for scene in scene_rows:
            if scene.get("reference_status") != "selected":
                raise NovelRagError(
                    "cannot embed scene not selected as a writing reference: "
                    f"scene_id={scene.get('id')}, "
                    f"status={scene.get('reference_status')}"
                )
            if scene.get("annotate_status") != "annotated":
                raise NovelRagError(
                    "cannot embed scene without successful annotation: "
                    f"scene_id={scene.get('id')}, "
                    f"status={scene.get('annotate_status')}"
                )
            vectors = embedder.embed_scene(scene)
            vectors["text-sparse"] = build_sparse_vector(
                str(scene["text"]),
                token_to_id,
            )
            points.append(
                {
                    "id": int(scene["id"]),
                    "vector": vectors,
                    "payload": {
                        "scene_id": int(scene["id"]),
                        "book_id": book_id,
                        "version": version,
                        "scene_index_in_book": int(scene["scene_index_in_book"]),
                        "chapter_start_index": int(scene["chapter_start_index"]),
                        "chapter_end_index": int(scene["chapter_end_index"]),
                        "reference_status": scene["reference_status"],
                        "reference_score": float(scene["reference_score"]),
                        "reference_reason": scene["reference_reason"],
                        "reference_rule_version": scene[
                            "reference_rule_version"
                        ],
                        "summary": scene["summary"],
                        "style_summary": scene["style_summary"],
                        "usage_hint": scene["usage_hint"],
                        "scene_type": list(scene["scene_type"] or []),
                        "technique": list(scene["technique"] or []),
                        "style_tags": list(scene["style_tags"] or []),
                        "emotion_tags": list(scene["emotion_tags"] or []),
                        "key_images": list(scene["key_images"] or []),
                        "scene_type_display": list(
                            scene["scene_type_display"] or []
                        ),
                        "technique_display": list(
                            scene["technique_display"] or []
                        ),
                        "style_tags_display": list(
                            scene["style_tags_display"] or []
                        ),
                        "emotion_tags_display": list(
                            scene["emotion_tags_display"] or []
                        ),
                        "key_images_display": list(
                            scene["key_images_display"] or []
                        ),
                        "narrative_func": scene["narrative_func"],
                        "char_count": int(scene["char_count"]),
                    },
                }
            )
        return points

    def _store_points(
        self,
        *,
        book_id: int,
        version: int,
        points: list[dict[str, Any]],
        selected_rows: list[dict[str, Any]],
        total_scenes: int,
    ) -> tuple[str, int]:
        collection_name = self.qdrant.create_scenes_collection(book_id, version)
        self.qdrant.upsert_scene_points(collection_name, points)
        for scene in selected_rows:
            self.repository.update_scene(
                int(scene["id"]), index_status="indexed", error_message=None,
            )
        indexed_count = len(self.repository.list_scenes(
            book_id, version, reference_status="selected",
            annotate_status="annotated", index_status="indexed",
        ))
        qdrant_count = self.qdrant.count(collection_name)
        if indexed_count != qdrant_count:
            raise NovelRagError(
                "index consistency check failed: "
                f"selected_indexed={indexed_count}, "
                f"qdrant={qdrant_count}, total_scenes={total_scenes}"
            )
        return collection_name, indexed_count

    def run(self, book_id: int) -> dict[str, Any]:
        book = self.repository.get_book(book_id)
        if book is None:
            raise NovelRagError(f"book not found: {book_id}")

        version = int(book["current_version"] or 0) + 1
        collection_name = ""
        is_epub = book["source_format"] == "epub"
        try:
            self.repository.update_book(
                book_id,
                status="converting" if is_epub else "splitting",
                error_message=None,
            )
            txt_path = self._run_job(
                book_id,
                "convert" if is_epub else "prepare_text",
                lambda: self._converted_txt(book),
            )
            self.repository.update_book(
                book_id,
                converted_path=str(txt_path),
                converter_version=(
                    self._epub_converter_version()
                    if book["source_format"] == "epub"
                    else None
                ),
            )

            self.repository.update_book(book_id, status="splitting")
            scene_rows = self._run_job(
                book_id,
                "split",
                lambda progress: self._split_and_store(
                    book_id, version, txt_path, progress,
                ),
                progress_operation=True,
            )

            self.repository.update_book(book_id, status="evaluating")
            evaluator = ReferenceEvaluator(
                repository=self.repository,
                llm_client=self.llm_client,
                settings=self.settings,
            )
            evaluation_stats = self._run_job(
                book_id,
                "evaluate",
                lambda: evaluator.evaluate_book(book_id, version),
                total_items=len(scene_rows),
            )
            self.repository.update_book(
                book_id,
                selected_scenes=evaluation_stats["selected"],
                archived_scenes=evaluation_stats["archived"],
                evaluation_failed_scenes=evaluation_stats["evaluation_failed"],
            )
            if evaluation_stats["evaluation_failed"]:
                raise NovelRagError(
                    "reference evaluation failed for "
                    f"{evaluation_stats['evaluation_failed']} scene(s)"
                )

            self.repository.update_book(book_id, status="annotating")
            self.repository.upsert_tag_vocab(self.vocabulary.rows)
            annotator = Annotator(
                repository=self.repository,
                llm_client=self.llm_client,
                vocabulary=self.vocabulary,
                settings=self.settings,
            )
            annotation_stats = self._run_job(
                book_id,
                "annotate",
                lambda: annotator.annotate_book(book_id, version),
                total_items=evaluation_stats["selected"],
            )

            self.repository.update_book(book_id, status="indexing")
            all_scene_rows = [
                dict(row)
                for row in self.repository.list_scenes(book_id, version)
            ]
            selected_rows = [
                row
                for row in all_scene_rows
                if row["reference_status"] == "selected"
            ]
            failed_annotations = [
                int(row["id"])
                for row in selected_rows
                if row["annotate_status"] != "annotated"
            ]
            if failed_annotations:
                raise NovelRagError(
                    "annotation failed for scene ids: "
                    + ",".join(str(scene_id) for scene_id in failed_annotations)
                )
            points = self._run_job(
                book_id,
                "embed",
                lambda: self._build_points(
                    book_id=book_id,
                    version=version,
                    scene_rows=selected_rows,
                ),
                total_items=len(selected_rows),
            )
            collection_name, indexed_selected_count = self._run_job(
                book_id,
                "store",
                lambda: self._store_points(
                    book_id=book_id, version=version, points=points,
                    selected_rows=selected_rows,
                    total_scenes=len(all_scene_rows),
                ),
                total_items=len(points),
            )
            self._run_job(
                book_id,
                "sync",
                lambda: self.repository.activate_version(book_id, version),
                total_items=indexed_selected_count,
            )
            return {
                "book_id": book_id,
                "version": version,
                "collection": collection_name,
                "chapters": len(split_chapters(txt_path.read_text(encoding="utf-8"))),
                "scenes": len(all_scene_rows),
                "selected_scenes": evaluation_stats["selected"],
                "archived_scenes": evaluation_stats["archived"],
                "evaluation_failed_scenes": evaluation_stats[
                    "evaluation_failed"
                ],
                "indexed_scenes": indexed_selected_count,
                "reference_evaluation": evaluation_stats,
                "annotation": annotation_stats,
            }
        except Exception as exc:
            self.repository.update_book(
                book_id,
                status="failed",
                error_message=str(exc)[:2000],
            )
            logger.exception(
                "index failed",
                extra={
                    "book_id": book_id,
                    "version": version,
                    "collection": collection_name,
                },
            )
            raise
