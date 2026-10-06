"""Restore the public Qdrant JSONL fixture into an empty Qdrant instance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from qdrant_client.http import models

from app.db.qdrant import QdrantAdapter, validate_scenes_collection


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore(fixture_dir: Path, adapter: QdrantAdapter) -> None:
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "novel-rag-public-fixture-v1":
        raise ValueError("unsupported fixture format")
    for book in manifest["books"]:
        name = validate_scenes_collection(book["collection"])
        source = fixture_dir / book["file"]
        if source.resolve().parent != (fixture_dir / "qdrant").resolve():
            raise ValueError("fixture file must be inside qdrant directory")
        if sha256(source) != book["sha256"]:
            raise ValueError(f"fixture checksum mismatch: {name}")
        if adapter.client.collection_exists(name):
            raise ValueError(f"collection already exists: {name}")

        adapter.create_scenes_collection(int(book["id"]), int(book["version"]))
        batch: list[models.PointStruct] = []
        count = 0
        with source.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                point = models.PointStruct.model_validate({
                    "id": record["id"],
                    "vector": record["vector"],
                    "payload": record.get("payload") or {},
                })
                batch.append(point)
                if len(batch) >= 64:
                    adapter.client.upsert(collection_name=name, points=batch, wait=True)
                    count += len(batch)
                    batch.clear()
        if batch:
            adapter.client.upsert(collection_name=name, points=batch, wait=True)
            count += len(batch)
        if count != int(book["selected_points"]) or adapter.count(name) != count:
            raise ValueError(f"point count mismatch after restoring {name}")
        print(f"restored {name}: {count} points")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_dir", type=Path)
    args = parser.parse_args()
    restore(args.fixture_dir, QdrantAdapter())


if __name__ == "__main__":
    main()
