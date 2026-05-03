from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings loaded from environment or .env file.
    """
    environment: str = "development"
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'