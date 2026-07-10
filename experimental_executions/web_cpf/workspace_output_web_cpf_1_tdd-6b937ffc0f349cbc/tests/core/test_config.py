import os
import sys
import importlib
import pytest


def reload_config_module():
    """
    Remove src.core.config from sys.modules (if loaded) and re-import it.
    """
    sys.modules.pop('src.core.config', None)
    return importlib.import_module('src.core.config')


def test_default_settings_applied_when_no_env_vars(monkeypatch):
    # Ensure environment variables are not set
    monkeypatch.delenv('APP_ENV', raising=False)
    monkeypatch.delenv('API_HOST', raising=False)
    monkeypatch.delenv('API_PORT', raising=False)

    # Reload config to pick up new environment state
    config = reload_config_module()
    # Settings should expose defaults as in .env.example
    Settings = config.Settings
    settings = Settings()

    assert hasattr(settings, 'ENV'), "Settings must have attribute 'ENV'"
    assert hasattr(settings, 'HOST'), "Settings must have attribute 'HOST'"
    assert hasattr(settings, 'PORT'), "Settings must have attribute 'PORT'"

    assert settings.ENV == 'development', (
        f"Expected default ENV='development', got '{settings.ENV}'"
    )
    assert settings.HOST == '127.0.0.1', (
        f"Expected default HOST='127.0.0.1', got '{settings.HOST}'"
    )
    assert isinstance(settings.PORT, int), (
        f"PORT should be int, got {type(settings.PORT)}"
    )
    assert settings.PORT == 8000, (
        f"Expected default PORT=8000, got {settings.PORT}"
    )


def test_environment_variables_override_defaults(monkeypatch):
    # Set custom environment variables
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('API_HOST', '0.0.0.0')
    monkeypatch.setenv('API_PORT', '9000')

    # Reload config to pick up overrides
    config = reload_config_module()
    Settings = config.Settings
    settings = Settings()

    assert settings.ENV == 'production', (
        f"Expected ENV from env var to be 'production', got '{settings.ENV}'"
    )
    assert settings.HOST == '0.0.0.0', (
        f"Expected HOST from env var to be '0.0.0.0', got '{settings.HOST}'"
    )
    # PORT should be coerced to int
    assert isinstance(settings.PORT, int), (
        f"PORT should be an int, got {type(settings.PORT)}"
    )
    assert settings.PORT == 9000, (
        f"Expected PORT from env var to be 9000, got {settings.PORT}"
    )
