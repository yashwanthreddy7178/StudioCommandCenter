"""Configuration settings for mcp-gateway."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/mcp-gateway/src/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """MCP Gateway service configuration."""
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

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

    # Optional explicit datasource pinning. Left empty the gateway prefers the
    # canonical Grafana Cloud UIDs and falls back to the first of each type.
    grafana_prometheus_datasource_uid: str = ""
    grafana_loki_datasource_uid: str = ""
    grafana_tempo_datasource_uid: str = ""

    # Rate Limiting & Caching Parameters
    mcp_global_qps_limit: int = 25
    quantization_bucket_seconds: int = 15
    range_cache_ttl_seconds: int = 20
    metadata_cache_ttl_seconds: int = 300
    stale_cache_max_seconds: int = 120


settings = Settings()
