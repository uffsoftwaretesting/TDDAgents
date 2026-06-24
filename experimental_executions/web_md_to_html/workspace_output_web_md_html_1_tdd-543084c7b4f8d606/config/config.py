from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str
    DEBUG: bool
    HOST: str
    PORT: int

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }