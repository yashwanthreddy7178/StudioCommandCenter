"""Configuration settings for api-gateway."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """API Gateway service configuration."""
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    service_name: str = "api-gateway"
    port: int = 8000
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Downstream Microservices
    agent_worker_url: str = "http://localhost:8010"
    action_executor_url: str = "http://localhost:8003"
    # How far ahead a scenario reset places the delivery deadline. Sized so a
    # healthy fleet meets it and a degraded one does not.
    delivery_window_minutes: int = 185
    render_sim_url: str = "http://localhost:8004"
    mcp_gateway_url: str = "http://localhost:8001"
    impact_engine_url: str = "http://localhost:8002"

    # Multi-tenant Leasing
    tenant_lease_ttl_sec: float = 1200.0 # 20 minutes
    num_tenant_worlds: int = 24


settings = Settings()
