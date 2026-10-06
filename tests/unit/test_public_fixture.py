from __future__ import annotations

import json
from pathlib import Path

from qdrant_client import QdrantClient

from app.db.qdrant import QdrantAdapter
from scripts.restore_public_qdrant import restore


def test_public_fixture_restores_searchable_vectors() -> None:
    fixture_dir = Path(__file__).resolve().parents[2] / "sql" / "fixtures" / "three-books"
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    adapter = QdrantAdapter(client=QdrantClient(":memory:"))
    try:
        restore(fixture_dir, adapter)
        for book in manifest["books"]:
            collection = book["collection"]
            records, _ = adapter.client.scroll(
                collection_name=collection,
                limit=1,
                with_vectors=True,
                with_payload=True,
            )
            assert records
            point = records[0]
            assert point.payload["book_id"] == book["id"]
            hits = adapter.search(
                collection,
                vector_name="text-dense",
                vector=point.vector["text-dense"],
                limit=1,
            )
            assert hits[0].id == point.id
    finally:
        adapter.close()
