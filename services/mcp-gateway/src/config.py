"""Configuration settings for mcp-gateway."""
from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MCP Gateway service configuration."""
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    service_name: str = "mcp-gateway"
    port: int = 8001
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Redis configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_enabled: bool = False # Set True when Redis instance is reachable

    # Grafana MCP Upstream
    grafana_mcp_server_url: str = ""
    grafana_service_account_token: str = ""
    render_sim_url: str = "http://localhost:8004"

    # Rate Limiting & Caching Parameters
    mcp_global_qps_limit: int = 25
    quantization_bucket_seconds: int = 15
    range_cache_ttl_seconds: int = 20
    metadata_cache_ttl_seconds: int = 300
    stale_cache_max_seconds: int = 120


settings = Settings()
