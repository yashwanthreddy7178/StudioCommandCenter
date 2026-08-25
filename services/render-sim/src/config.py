"""Configuration settings for render-sim."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Render simulator service configuration."""
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    service_name: str = "render-sim"
    port: int = 8004
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Multi-tenant world counts
    num_tenant_worlds: int = 24
    enable_observer_world: bool = True
    simulation_tick_interval_sec: float = 2.0
    firestore_mirror_interval_sec: float = 10.0

    # OTel & Grafana Cloud export targets
    grafana_otel_metrics_url: str = ""
    grafana_otel_logs_url: str = ""
    grafana_otel_traces_url: str = ""
    grafana_instance_user: str = ""
    grafana_service_account_token: str = ""

    # Firestore configuration
    firestore_database: str = "(default)"


settings = Settings()
