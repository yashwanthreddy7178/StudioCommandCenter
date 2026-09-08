"""Configuration settings for stream-service."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/<name>/src/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Stream service configuration."""
    # Reads .env like every other service. Without env_file this settings
    # class only ever saw the process environment, so anything set in .env
    # for this service was silently ignored -- the value looked configured
    # and the default was what actually ran. extra="ignore" is required
    # alongside it: .env holds keys belonging to the other services too.
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "stream-service"
    port: int = 8005
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Agent worker / Firestore source endpoint
    agent_worker_url: str = "http://localhost:8010"
    sse_heartbeat_interval_sec: float = 15.0
    sse_poll_interval_sec: float = 0.5

    # Hard ceiling on one stream.
    #
    # A run that never reaches a terminal event -- a worker killed mid-run, an
    # approval nobody ever gives -- would otherwise leave the connection polling
    # agent-worker twice a second until the browser goes away. With many viewers
    # that is a steady load generated entirely by runs that are already over.
    sse_max_stream_sec: float = 1800.0


settings = Settings()
