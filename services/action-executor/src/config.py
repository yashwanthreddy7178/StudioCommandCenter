"""Configuration settings for action-executor."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Action executor service configuration."""
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    service_name: str = "action-executor"
    port: int = 8003
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Microservice Endpoints
    render_sim_url: str = "http://localhost:8004"
    mcp_gateway_url: str = "http://localhost:8001"
    impact_engine_url: str = "http://localhost:8002"
    agent_worker_url: str = "http://localhost:8010"

    # Verification settle delay (seconds to wait after action before re-querying telemetry)
    verification_settle_sec: float = 3.0


settings = Settings()
