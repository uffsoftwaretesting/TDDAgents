import os
import pytest
from pydantic import ValidationError
from config.config import Settings


def test_load_settings_from_env(monkeypatch):
    # Arrange: set all required environment variables
    monkeypatch.setenv("APP_NAME", "TestApp")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("HOST", "localhost")
    monkeypatch.setenv("PORT", "1234")

    # Act: create Settings instance
    settings = Settings()

    # Assert: values are correctly loaded and cast
    assert settings.APP_NAME == "TestApp"
    assert isinstance(settings.DEBUG, bool)
    assert settings.DEBUG is False
    assert settings.HOST == "localhost"
    assert isinstance(settings.PORT, int)
    assert settings.PORT == 1234


def test_missing_env_raises_validation_error(monkeypatch):
    # Remove any existing environment variables
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    # Expect a ValidationError due to missing required settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None)