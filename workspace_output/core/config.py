from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(..., env="DATABASE_URL")
    secret_key: str = Field(..., env="SECRET_KEY")

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8'
    )
