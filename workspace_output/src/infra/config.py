from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    database_url: str
    env: str

    # load additional variables from .env file if present
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )
