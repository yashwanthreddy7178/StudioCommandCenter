"""Configuration settings for impact-engine."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Impact engine service configuration."""
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    service_name: str = "impact-engine"
    port: int = 8002
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Database configuration (Defaults to in-memory SQLite for seamless testing)
    database_url: str = "sqlite+aiosqlite:///:memory:"


settings = Settings()
