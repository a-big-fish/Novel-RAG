import pytest
from fastapi import HTTPException

from app import config
from app.api.dependencies import get_multi_search_settings
from app.config import Settings


def test_rerank_switch_reloads_dotenv_between_requests(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(config, "RERANK_ENV_FILE", env_file)
    base = Settings(_env_file=None, rerank_enabled=None)

    env_file.write_text("RERANK_ENABLED=true\n", encoding="utf-8")
    assert get_multi_search_settings(base).rerank_enabled is True

    env_file.write_text("RERANK_ENABLED=false\n", encoding="utf-8")
    assert get_multi_search_settings(base).rerank_enabled is False
    assert base.rerank_enabled is None


def test_missing_rerank_switch_reports_configuration_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RERANK_ENV_FILE", tmp_path / ".env")
    with pytest.raises(HTTPException) as error:
        get_multi_search_settings(Settings(_env_file=None))
    assert error.value.status_code == 503
    assert "RERANK_ENABLED" in error.value.detail


@pytest.mark.parametrize("value", ["", "perhaps"])
def test_rerank_switch_rejects_missing_or_invalid_value(tmp_path, monkeypatch, value):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(config, "RERANK_ENV_FILE", env_file)
    env_file.write_text(f"RERANK_ENABLED={value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="RERANK_ENABLED"):
        config.read_rerank_enabled()
