import os
import pytest

from src.config.settings import Settings


def test_default_settings_values(monkeypatch):
    # Ensure environment variables are not set
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    settings = Settings()
    assert settings.LOG_LEVEL == "INFO"
    assert settings.ENVIRONMENT == "development"


def test_settings_reads_environment_variables(monkeypatch):
    # Override environment variables
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ENVIRONMENT", "production")

    settings = Settings()
    assert settings.LOG_LEVEL == "DEBUG"
    assert settings.ENVIRONMENT == "production"


def test_env_file_configured():
    # Check that the Settings.Config points to .env file
    config = Settings.Config
    assert hasattr(config, 'env_file'), "Config must define env_file"
    assert config.env_file == ".env"
    assert hasattr(config, 'env_file_encoding'), "Config must define env_file_encoding"
    assert config.env_file_encoding == "utf-8"
