"""Configuration settings for render-sim."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/render-sim/src/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Render simulator service configuration."""
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "render-sim"
    # Stable across restarts so telemetry lands on the same Prometheus series.
    # Override per instance when running more than one simulator.
    service_instance_id: str = "render-sim-0"
    port: int = 8004
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Multi-tenant world counts
    num_tenant_worlds: int = 24
    enable_observer_world: bool = True
    simulation_tick_interval_sec: float = 2.0
    firestore_mirror_interval_sec: float = 10.0

    # Grafana Cloud OTLP ingest (write path).
    # Distinct from the query credential in mcp-gateway: ingest authenticates with
    # HTTP Basic using the numeric instance ID plus a glc_ access policy token,
    # while queries use a glsa_ service account token as a Bearer credential.
    grafana_otlp_endpoint_url: str = ""
    grafana_otlp_instance_id: str = ""
    grafana_access_policy_token: str = ""

    # How often buffered world state is pushed upstream, independent of tick rate.
    otel_export_interval_sec: float = 15.0

    # Render traces are what the agent's trace-attribution test reads: each frame
    # span breaks out asset fetch, GPU render and output write, so a slow frame
    # can be attributed to the GPU rather than to storage or the control API.
    # Disable to cut span volume when running all 24 worlds on a small stack.
    emit_render_traces: bool = True

    # Firestore configuration
    firestore_database: str = "(default)"

    @property
    def otlp_metrics_endpoint(self) -> str:
        """Full OTLP/HTTP metrics URL, tolerating a base endpoint with or without the suffix."""
        base = self.grafana_otlp_endpoint_url.rstrip("/")
        if not base:
            return ""
        return base if base.endswith("/v1/metrics") else f"{base}/v1/metrics"

    @property
    def otlp_logs_endpoint(self) -> str:
        """Full OTLP/HTTP logs URL, derived from the same base endpoint."""
        base = self.grafana_otlp_endpoint_url.rstrip("/")
        if not base:
            return ""
        if base.endswith("/v1/metrics"):
            base = base[: -len("/v1/metrics")]
        return base if base.endswith("/v1/logs") else f"{base}/v1/logs"

    @property
    def otlp_traces_endpoint(self) -> str:
        """Full OTLP/HTTP traces URL, derived from the same base endpoint."""
        from services.common.tracing import derive_otlp_endpoint

        return derive_otlp_endpoint(self.grafana_otlp_endpoint_url, "traces")

    @property
    def otlp_export_enabled(self) -> bool:
        """True when all three ingest credentials are present."""
        return bool(
            self.grafana_otlp_endpoint_url
            and self.grafana_otlp_instance_id
            and self.grafana_access_policy_token
        )


settings = Settings()
