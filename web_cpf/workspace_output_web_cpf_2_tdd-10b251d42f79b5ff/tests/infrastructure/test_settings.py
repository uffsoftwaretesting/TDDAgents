import inspect
import pytest
from pydantic_settings import BaseSettings
from infrastructure.config.settings import Settings


def test_settings_class_exists_and_inherits_base_settings():
    # Settings should be a subclass of BaseSettings
    assert inspect.isclass(Settings)
    assert issubclass(Settings, BaseSettings)


def test_settings_config_env_file_specified():
    # Config.env_file should be set to '.env'
    assert hasattr(Settings, 'Config')
    assert getattr(Settings.Config, 'env_file', None) == ".env"


def test_default_values(monkeypatch):
    # Ensure no environment variables are set
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    settings = Settings()
    assert settings.environment == "development"
    assert settings.debug is False


@pytest.mark.parametrize("env_vars, expected", [
    ({"ENVIRONMENT": "production"}, {"environment": "production", "debug": False}),
    ({"DEBUG": "True"}, {"environment": "development", "debug": True}),
    ({"ENVIRONMENT": "stage", "DEBUG": "False"}, {"environment": "stage", "debug": False}),
])
def test_env_variables_override(monkeypatch, env_vars, expected):
    # Set environment vars and verify they override defaults
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    settings = Settings()
    assert settings.environment == expected["environment"]
    assert settings.debug == expected["debug"]
