from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from ``TOP_ARENA_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="TOP_ARENA_",
        env_file=".env",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./data/top-arena.db"
    storage_backend: Literal["filesystem", "s3"] = "filesystem"
    storage_path: Path = Path("data/objects")
    s3_bucket: str = ""
    s3_prefix: str = "top-arena"
    s3_region: str = "us-east-1"
    public_base_url: str = "http://127.0.0.1:8000"
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    score_worker_count: int = 1
    score_poll_interval_seconds: float = 0.25
    run_completion_timeout_seconds: float = 600.0
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
