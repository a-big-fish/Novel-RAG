from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.config import Settings, get_settings
from app.utils.errors import StorageError

SCENES_COLLECTION_RE = re.compile(r"^scenes_book_[0-9]+_v[0-9]+$")
DENSE_VECTOR_NAMES = ("text-dense", "meta-dense", "summary-dense")
SPARSE_VECTOR_NAME = "text-sparse"


def scenes_collection_name(book_id: int, version: int) -> str:
    if book_id <= 0 or version <= 0:
        raise ValueError("book_id and version must be positive")
    return f"scenes_book_{book_id}_v{version}"


def validate_scenes_collection(name: str) -> str:
    if not SCENES_COLLECTION_RE.fullmatch(name):
        raise ValueError(f"collection is not allowed: {name}")
    return name


class QdrantAdapter:
    """Thin adapter around qdrant-client with application-specific guardrails."""

    def __init__(
        self,
        client: QdrantClient | None = None,
        *,
        settings: Settings | None = None,
        dimension: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if client is not None:
            self.client = client
        else:
            api_key = (
                self.settings.qdrant_api_key.get_secret_value()
                if self.settings.qdrant_api_key
                else None
            )
            self.client = QdrantClient(
                url=self.settings.qdrant_url,
                api_key=api_key,
                timeout=self.settings.qdrant_timeout_seconds,
            )
        self.dimension = dimension or self.settings.embedding_dimension

    def ping(self) -> None:
        try:
            self.client.get_collections()
        except Exception as exc:
            raise StorageError(f"Qdrant unavailable: {exc}") from exc

    def create_scenes_collection(
        self,
        book_id: int,
        version: int,
        *,
        recreate: bool = False,
    ) -> str:
        name = scenes_collection_name(book_id, version)
        try:
            exists = self.client.collection_exists(name)
            if exists and recreate:
                self.client.delete_collection(name)
                exists = False
            if not exists:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config={
                        vector_name: models.VectorParams(
                            size=self.dimension,
                            distance=models.Distance.COSINE,
                        )
                        for vector_name in DENSE_VECTOR_NAMES
                    },
                    sparse_vectors_config={
                        SPARSE_VECTOR_NAME: models.SparseVectorParams(
                            modifier=models.Modifier.IDF
                        )
                    },
                )
        except Exception as exc:
            raise StorageError(f"cannot create collection {name}: {exc}") from exc
        return name

    def upsert_scene_points(
        self,
        collection_name: str,
        points: Sequence[dict[str, Any]],
        *,
        batch_size: int = 64,
    ) -> int:
        validate_scenes_collection(collection_name)
        count = 0
        try:
            for start in range(0, len(points), batch_size):
                batch = points[start : start + batch_size]
                structs = [self._to_point(point) for point in batch]
                self.client.upsert(
                    collection_name=collection_name,
                    points=structs,
                    wait=True,
                )
                count += len(structs)
        except Exception as exc:
            raise StorageError(
                f"cannot upsert into {collection_name}: {exc}"
            ) from exc
        return count

    @staticmethod
    def _to_point(point: Mapping[str, Any]) -> models.PointStruct:
        vector = point["vector"]
        sparse = vector[SPARSE_VECTOR_NAME]
        normalized_vectors: dict[str, Any] = {
            name: vector[name] for name in DENSE_VECTOR_NAMES
        }
        normalized_vectors[SPARSE_VECTOR_NAME] = models.SparseVector(
            indices=list(sparse["indices"]),
            values=list(sparse["values"]),
        )
        return models.PointStruct(
            id=int(point["id"]),
            vector=normalized_vectors,
            payload=dict(point.get("payload") or {}),
        )

    def count(self, collection_name: str) -> int:
        validate_scenes_collection(collection_name)
        try:
            result = self.client.count(
                collection_name=collection_name,
                exact=True,
            )
            return int(result.count)
        except Exception as exc:
            raise StorageError(
                f"cannot count collection {collection_name}: {exc}"
            ) from exc

    def get_point(
        self,
        collection_name: str,
        point_id: int,
        *,
        with_vectors: bool = False,
    ) -> models.Record | None:
        validate_scenes_collection(collection_name)
        try:
            records = self.client.retrieve(
                collection_name=collection_name,
                ids=[point_id],
                with_payload=True,
                with_vectors=with_vectors,
            )
        except Exception as exc:
            raise StorageError(
                f"cannot retrieve point {point_id} from {collection_name}: {exc}"
            ) from exc
        return records[0] if records else None

    def search(
        self,
        collection_name: str,
        *,
        vector_name: str,
        vector: Sequence[float] | Mapping[str, Any],
        limit: int = 10,
        with_payload: bool = True,
        query_filter: Mapping[str, Any] | None = None,
    ) -> list[models.ScoredPoint]:
        validate_scenes_collection(collection_name)
        if vector_name not in (*DENSE_VECTOR_NAMES, SPARSE_VECTOR_NAME):
            raise ValueError(f"unsupported vector name: {vector_name}")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        if vector_name == SPARSE_VECTOR_NAME:
            if not isinstance(vector, Mapping):
                raise ValueError("sparse vector must contain indices and values")
            query: Any = models.SparseVector(
                indices=list(vector["indices"]),
                values=list(vector["values"]),
            )
        else:
            if isinstance(vector, Mapping):
                raise ValueError("dense vector must be a numeric sequence")
            query = list(vector)

        parsed_filter = (
            models.Filter.model_validate(query_filter) if query_filter else None
        )
        try:
            result = self.client.query_points(
                collection_name=collection_name,
                query=query,
                using=vector_name,
                limit=limit,
                with_payload=with_payload,
                query_filter=parsed_filter,
            )
            return list(result.points)
        except Exception as exc:
            raise StorageError(
                f"cannot search collection {collection_name}: {exc}"
            ) from exc

    def delete_collection(self, collection_name: str) -> None:
        validate_scenes_collection(collection_name)
        try:
            self.client.delete_collection(collection_name)
        except Exception as exc:
            raise StorageError(
                f"cannot delete collection {collection_name}: {exc}"
            ) from exc

    def close(self) -> None:
        self.client.close()
