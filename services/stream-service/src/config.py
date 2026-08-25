"""Configuration settings for stream-service."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Stream service configuration."""
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    service_name: str = "stream-service"
    port: int = 8005
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Agent worker / Firestore source endpoint
    agent_worker_url: str = "http://localhost:8010"
    sse_heartbeat_interval_sec: float = 15.0


settings = Settings()
