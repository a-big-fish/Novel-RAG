from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from app.api.dependencies import get_repository
from app.main import app


class MissingTable(Exception):
    sqlstate = "42P01"


class EmptyDatabaseRepository:
    def list_books(self):
        raise ProgrammingError("SELECT FROM books", {}, MissingTable())


def test_missing_schema_explains_preview_startup():
    app.dependency_overrides[get_repository] = EmptyDatabaseRepository
    try:
        response = TestClient(app).get("/api/v1/books")
        assert response.status_code == 503
        assert "start_dashboard_preview.ps1" in response.json()["detail"]
        assert "SELECT" not in response.text
    finally:
        app.dependency_overrides.clear()
