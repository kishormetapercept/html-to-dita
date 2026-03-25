from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Html2Dita Backend (FastAPI)"
    port: int = 8001
    base: str = Field(
        default="http://localhost:8001",
        validation_alias=AliasChoices("BASE", "BASE_URL"),
    )

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "htmltodita"

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
