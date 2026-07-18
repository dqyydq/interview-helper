from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_prefix="INTERVIEW_HELPER_",
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    app_name: str = "Interview Helper API"
    app_version: str = "0.1.0"
    api_prefix: str = "/api"
    frontend_origin: AnyHttpUrl = Field(default="http://localhost:5173")
    database_url: str = (
        "postgresql+asyncpg://interview_helper:local-development-only@localhost:5432/"
        "interview_helper"
    )
    database_echo: bool = False
    database_connect_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
