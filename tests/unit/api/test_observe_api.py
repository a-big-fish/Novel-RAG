from fastapi.testclient import TestClient

from app.api.dependencies import get_qdrant_adapter, get_repository
from app.api.routes.observe import project_vectors
from app.main import app


class FakeRepository:
    def get_book(self, book_id):
        if book_id != 7:
            return None
        return {"id": 7, "title": "测试书", "author": "甲", "current_version": 2, "status": "ready"}

    def list_versions(self, _book_id):
        return [
            {"version": 2, "total_scenes": 2, "selected": 1,
             "archived": 1, "discarded": 0, "evaluation_failed": 0},
            {"version": 1, "total_scenes": 2, "selected": 1,
             "archived": 1, "discarded": 0, "evaluation_failed": 0},
        ]

    def list_version_scene_metrics(self, _book_id, _version):
        return [
            {"reference_status": "selected", "annotate_status": "annotated",
             "index_status": "indexed", "char_count": 200,
             "scene_type": ["action"], "technique": [], "style_tags": [],
             "emotion_tags": [], "key_images": []},
            {"reference_status": "archived", "annotate_status": "not_applicable",
             "index_status": "not_applicable", "char_count": 100,
             "scene_type": [], "technique": [], "style_tags": [],
             "emotion_tags": [], "key_images": []},
        ]

    def list_jobs(self, _book_id):
        return []

    def get_tag_vocab(self):
        return [{"namespace": "scene_type", "canonical_key": "action", "display_name": "动作场景"}]

    def list_scene_fingerprints(self, _book_id, version):
        if version == 2:
            return [{"text_hash": "a", "reference_status": "selected"},
                    {"text_hash": "b", "reference_status": "archived"}]
        return [{"text_hash": "a", "reference_status": "archived"},
                {"text_hash": "c", "reference_status": "selected"}]

    def list_version_scenes_page(self, _, __, *, limit, offset, reference_status):
        assert limit == 1 and offset == 0 and reference_status in {"selected", "discarded"}
        return 1, [{"id": 10, "reference_status": reference_status, "summary": "摘要"}]


class FakeQdrant:
    def count(self, _):
        return 1


def test_observation_uses_versioned_read_only_contract():
    app.dependency_overrides[get_repository] = FakeRepository
    app.dependency_overrides[get_qdrant_adapter] = FakeQdrant
    try:
        client = TestClient(app)
        base = "/api/v1/books/7/versions/2"
        overview = client.get(base + "/overview").json()
        assert overview["counts_match"] is True
        assert overview["counts"]["archived"] == 1
        assert overview["tags"]["scene_type"][0]["label"] == "动作场景"
        page = client.get(base + "/scenes?limit=1&reference_status=selected").json()
        assert page["items"][0]["id"] == 10
        discarded = client.get(base + "/scenes?limit=1&reference_status=discarded").json()
        assert discarded["items"][0]["reference_status"] == "discarded"
        comparison = client.get(base + "/compare?other_version=1").json()
        assert comparison["new_text"] == 1
        assert comparison["reference_status_changed"] == 1
        assert client.get("/api/v1/books/7/versions/3/overview").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_projection_handles_tiny_vector_sets():
    assert project_vectors([]) == []
    assert project_vectors([[1, 2]]) == [[0.0, 0.0]]
    points = project_vectors([[1, 0], [0, 1]])
    assert len(points) == 2
    assert all(len(point) == 2 for point in points)


def test_dashboard_is_served_without_static_export_dependency():
    response = TestClient(app).get("/dashboard")
    assert response.status_code == 200
    assert 'id="search-form"' in response.text
    assert 'data.js' not in response.text
