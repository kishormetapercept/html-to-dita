from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Html2Dita Backend (FastAPI)"
    port: int = 8000
    base: str = "http://localhost:8000"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "htmltodita"

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080

    input_root: str = "input"
    output_root: str = "output"
    downloads_root: str = "downloads"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
