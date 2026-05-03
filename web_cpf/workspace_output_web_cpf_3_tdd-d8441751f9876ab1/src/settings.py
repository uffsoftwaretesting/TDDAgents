from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    class Config:
        env_file = ".env"


# Instância global de configurações

t_settings = Settings()
settings = t_settings
