from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings loaded from environment or .env file.
    """
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"

    class Config:
        # Load from a .env file in project root if present
        env_file = ".env"
        env_file_encoding = "utf-8"
