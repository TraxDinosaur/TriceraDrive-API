from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "MEGA"
    debug: bool = False
    data_dir: str = "data"
    encryption_key: str = "change-me-in-production-use-a-real-secret"
    sync_interval_seconds: int = 300
    max_retry_count: int = 3
    cors_origins: list[str] = ["http://localhost:3000"]

    model_config = {"env_prefix": "MEGA_", "env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
