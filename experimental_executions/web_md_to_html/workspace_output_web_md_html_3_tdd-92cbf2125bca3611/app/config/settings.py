try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings

from pydantic import validator, ValidationError


class Settings(BaseSettings):
    app_name: str = "MarkdownToHTML"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    markdown_max_length: int = 10000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @validator('markdown_max_length')
    def markdown_max_length_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('markdown_max_length must be greater than zero')
        return v


_settings_singleton: Settings

def get_settings() -> Settings:
    global _settings_singleton
    try:
        return _settings_singleton
    except NameError:
        _settings_singleton = Settings()
        return _settings_singleton