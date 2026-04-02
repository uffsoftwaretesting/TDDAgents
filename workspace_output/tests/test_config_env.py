import os
import pytest
from pydantic import ValidationError
from src.infra.config import Settings

def test_env_example_exists_and_contains_required_vars():
    # The .env.example should be in the project root and list the required keys
    base = os.path.dirname(os.path.dirname(__file__))
    example_path = os.path.join(base, ".env.example")
    assert os.path.isfile(example_path), ".env.example must exist in project root"
    content = open(example_path).read()
    for var in ["DATABASE_URL", "ENV"]:
        assert var in content, f"'{var}' must be defined in .env.example"


def test_settings_load_from_env(monkeypatch):
    # When environment variables are set, Settings should pick them up
    test_db_url = "postgresql://user:pass@localhost/db"
    test_env = "testing"
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("ENV", test_env)
    settings = Settings()
    assert settings.database_url == test_db_url
    assert settings.env == test_env


def test_missing_database_url_raises_validation_error(monkeypatch):
    # DATABASE_URL is required, missing it should trigger ValidationError
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENV", "testing")
    with pytest.raises(ValidationError):
        Settings()


def test_missing_env_raises_validation_error(monkeypatch):
    # ENV is required, missing it should trigger ValidationError
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("ENV", raising=False)
    with pytest.raises(ValidationError):
        Settings()