from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./data/watchagent.db"
    poll_interval_seconds: int = Field(default=600, ge=1)
    log_level: str = "INFO"
    enable_poller: bool = True
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_base_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_timeout_seconds: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=3, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
