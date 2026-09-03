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
    # Section 8 specifies a 90 second settle window. It is not cosmetic: fleet
    # throughput is a smoothed trailing rate, so sampling seconds after a rollback
    # catches the average mid-convergence and reports a recovered fleet as only
    # partially recovered. Tests pass settle_seconds=0 explicitly.
    verification_settle_sec: float = 90.0

    # Grafana write-back. Once a human approves a remediation, the result is
    # written back to Grafana so the people who own the dashboard see it: an
    # annotation on the timeline, and an incident when a delivery is at risk.
    grafana_writeback_enabled: bool = True
    # Incidents are filed as drills because the render farm behind them is a
    # simulator. Filing a simulated outage as a live incident would put a false
    # record into a system other people are on call against. Turn this off only
    # for a deployment backed by a real farm.
    grafana_incident_is_drill: bool = True
    # Opening an incident needs the Grafana Incident (IRM) app provisioned for the
    # org. On a stack where it is not, `list_incidents` still answers but creation
    # fails inside the incident service's own database with a foreign-key error on
    # Counters.orgID, because the org was never onboarded. Annotations are
    # unaffected. Set false to skip the attempt cleanly rather than record a
    # failed write on every approval.
    grafana_incident_enabled: bool = True


settings = Settings()
