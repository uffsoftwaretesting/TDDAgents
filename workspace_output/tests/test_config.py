import pytest
from pydantic import ValidationError
import os

def test_missing_required_env_vars(monkeypatch):
    # Ensure no relevant environment variables are set
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    
    from core.config import Settings
    
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    
    errors = exc_info.value.errors()
    # Expect errors for both database_url and secret_key
    assert any(
        error["loc"] == ("database_url",) and error["type"] == "missing"
        for error in errors
    ), f"Expected missing database_url error, got: {errors}"
    assert any(
        error["loc"] == ("secret_key",) and error["type"] == "missing"
        for error in errors
    ), f"Expected missing secret_key error, got: {errors}"


def test_env_vars_loading(monkeypatch):
    # Set environment variables explicitly
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db"
    )
    monkeypatch.setenv("SECRET_KEY", "supersecretkey")
    
    from core.config import Settings
    
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://user:pass@localhost:5432/db"
    assert settings.secret_key == "supersecretkey"


def test_env_file_loading(tmp_path, monkeypatch):
    # Create a .env file in a temporary directory
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=file_db_url_from_env_file\nSECRET_KEY=file_secret_from_env_file"
    )
    # Change current working directory to tmp_path so Pydantic will read this .env
    monkeypatch.chdir(tmp_path)
    
    from core.config import Settings
    
    settings = Settings()
    assert settings.database_url == "file_db_url_from_env_file"
    assert settings.secret_key == "file_secret_from_env_file"