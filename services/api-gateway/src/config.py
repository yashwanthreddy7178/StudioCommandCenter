"""Configuration settings for api-gateway."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/<name>/src/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """API Gateway service configuration."""
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

    # Demo quota on investigation runs.
    #
    # Every run is a chain of model calls billed to whoever deployed this, and
    # the service is published with a public link, so the ceiling is a budget
    # control rather than an abuse control. Set either to 0 to disable that half;
    # both at 0 removes the quota entirely, which is the sensible local setting.
    max_runs_per_session: int = 6
    max_runs_per_deployment: int = 60
    run_quota_window_sec: float = 3600.0 # 1 hour


settings = Settings()
