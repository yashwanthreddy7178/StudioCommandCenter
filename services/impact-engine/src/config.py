"""Configuration settings for impact-engine."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/impact-engine/src/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Impact engine service configuration."""
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "impact-engine"
    port: int = 8002
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Database configuration (Defaults to in-memory SQLite for seamless testing)
    database_url: str = "sqlite+aiosqlite:///:memory:"


settings = Settings()
