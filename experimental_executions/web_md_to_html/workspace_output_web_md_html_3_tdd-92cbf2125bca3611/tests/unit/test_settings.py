import os
from importlib import reload
import pytest
from pydantic import ValidationError

import app.config.settings as settings_module
from app.config.settings import get_settings

@pytest.fixture(autouse=True)
def clear_env_and_singleton(monkeypatch):
    # Clear relevant environment variables
    for var in ["APP_NAME", "DEBUG", "HOST", "PORT", "MARKDOWN_MAX_LENGTH"]:
        monkeypatch.delenv(var, raising=False)
    # Remove existing singleton
    if hasattr(settings_module, '_settings_singleton'):
        delattr(settings_module, '_settings_singleton')
    yield
    # Cleanup after test
    if hasattr(settings_module, '_settings_singleton'):
        delattr(settings_module, '_settings_singleton')


def test_default_settings_values():
    reload(settings_module)
    settings = get_settings()

    assert settings.app_name == "MarkdownToHTML"
    assert isinstance(settings.app_name, str)

    assert settings.debug is True
    assert isinstance(settings.debug, bool)

    assert settings.host == "127.0.0.1"
    assert isinstance(settings.host, str)

    assert settings.port == 8000
    assert isinstance(settings.port, int)

    assert settings.markdown_max_length == 10000
    assert isinstance(settings.markdown_max_length, int)


def test_environment_variable_overrides(monkeypatch):
    monkeypatch.setenv("APP_NAME", "CustomApp")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("MARKDOWN_MAX_LENGTH", "5000")

    reload(settings_module)
    settings = get_settings()

    assert settings.app_name == "CustomApp"
    assert settings.debug is False
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.markdown_max_length == 5000


def test_singleton_behavior():
    reload(settings_module)
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


@pytest.mark.parametrize("value", ["-1", "0"])
def test_invalid_markdown_max_length_raises(value, monkeypatch):
    monkeypatch.setenv("MARKDOWN_MAX_LENGTH", value)
    reload(settings_module)
    with pytest.raises(ValidationError):
        get_settings()