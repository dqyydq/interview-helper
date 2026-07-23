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
    log_level: str = "INFO"
    api_prefix: str = "/api"
    frontend_origin: AnyHttpUrl = Field(default="http://localhost:5173")
    database_url: str = (
        "postgresql+asyncpg://interview_helper:local-development-only@localhost:5432/"
        "interview_helper"
    )
    database_echo: bool = False
    database_connect_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    encryption_secret: str = Field(
        default="local-development-only-change-me",
        min_length=16,
        repr=False,
    )
    upload_dir: Path = REPOSITORY_ROOT / "data" / "uploads"
    resume_upload_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1_024)
    audio_upload_max_bytes: int = Field(default=15 * 1024 * 1024, ge=1_024)
    resume_parse_timeout_seconds: float = Field(default=20.0, ge=1.0, le=300.0)
    websocket_connections_per_minute: int = Field(default=12, ge=1, le=1_000)
    websocket_rate_limiter_max_keys: int = Field(default=4_096, ge=1, le=100_000)
    job_poll_interval_seconds: float = Field(default=0.5, ge=0.05, le=10.0)
    worker_heartbeat_interval_seconds: float = Field(default=2.0, ge=0.25, le=60.0)
    worker_heartbeat_stale_after_seconds: float = Field(default=15.0, ge=1.0, le=600.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
