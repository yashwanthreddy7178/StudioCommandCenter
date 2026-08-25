"""Configuration settings for agent-worker."""
from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent worker service configuration."""
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    service_name: str = "agent-worker"
    port: int = 8010
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Google Gemini & ADK
    gemini_api_key: Optional[str] = None
    planning_model: str = "gemini-2.5-flash"
    synthesis_model: str = "gemini-2.5-pro"
    max_investigation_turns: int = 14

    # Microservice endpoints
    mcp_gateway_url: str = "http://localhost:8001"
    impact_engine_url: str = "http://localhost:8002"
    action_executor_url: str = "http://localhost:8003"

    # Pub/Sub & Firestore
    pubsub_runs_subscription: str = "studio-production-runs-sub"
    firestore_database: str = "(default)"


settings = Settings()
