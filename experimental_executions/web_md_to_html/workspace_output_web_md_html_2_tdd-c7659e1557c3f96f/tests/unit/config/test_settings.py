import pytest
from pydantic import ValidationError
from app.config.settings import Settings

def test_settings_load_env(monkeypatch):
    """
    Given valid environment variables, Settings should load and cast them correctly.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEBUG", "True")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "8080")
    settings = Settings()
    assert settings.APP_ENV == "production"
    assert isinstance(settings.DEBUG, bool) and settings.DEBUG is True
    assert settings.HOST == "0.0.0.0"
    assert isinstance(settings.PORT, int) and settings.PORT == 8080

def test_settings_missing_env_vars(monkeypatch, tmp_path):
    """
    Without required environment variables, instantiating Settings should raise ValidationError.
    """
    for var in ["APP_ENV", "DEBUG", "HOST", "PORT"]:
        monkeypatch.delenv(var, raising=False)
    # Change to a temp dir so pydantic-settings cannot find a .env file
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError):
        Settings()
