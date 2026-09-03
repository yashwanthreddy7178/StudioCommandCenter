"""Configuration settings for agent-worker."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/agent-worker/src/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Agent worker service configuration."""
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "agent-worker"
    port: int = 8010
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # Google Gemini & ADK.
    # Two auth paths are supported. Vertex AI with Application Default Credentials
    # is preferred: on Cloud Run the service account supplies identity, so no key
    # material exists to leak from a public repository. The API key path remains
    # for local work on a machine without gcloud.
    google_cloud_project: Optional[str] = None
    # `global` rather than a named region: us-central1 serves only the 2.5
    # generation, while the global endpoint additionally serves 3.x. Verified by
    # probing both against this project.
    google_cloud_location: str = "global"
    gemini_api_key: Optional[str] = None
    # Flash plans and selects tools, Pro synthesises the final narrative, per
    # section 5.3. Both are pinned to versions verified against this project on
    # the global endpoint. The `-latest` aliases are a Developer API convention
    # and 404 on Vertex, so they are not used here. Synthesis stays on the GA
    # 2.5 Pro rather than a preview model, since a preview can change or be
    # withdrawn during the contest window.
    planning_model: str = "gemini-3.7-flash"
    synthesis_model: str = "gemini-2.5-pro"
    max_investigation_turns: int = 14

    # When true the agent is given a metric-name discovery tool and told to
    # enumerate the farm's series itself rather than being handed the list. It
    # costs a turn and makes the run less deterministic, which is the trade: an
    # agent that discovers what exists is doing the investigation, not reciting
    # an inventory. Set false to pin the demo to the fixed list.
    enable_metric_discovery: bool = True

    # Whether the connected Grafana MCP server exposes a trace-search tool.
    #
    # True for the vendored mcp-grafana build, verified against a live tools/list:
    # it exposes tempo_traceql-search taking a TraceQL `query` and a
    # `datasourceUid`. Set false only when pointing at a server without trace
    # search, which makes the trace-attribution criterion report as skipped rather
    # than fail.
    tempo_search_available: bool = True

    # Grafana Cloud OTLP ingest (write path), the same credential render-sim uses
    # to publish farm telemetry.
    #
    # This is what makes the agent observable rather than a black box. ADK emits
    # GenAI-semconv spans for every LLM call, tool invocation and token count
    # against the global tracer provider, so exporting here puts the agent's own
    # traces in the same Grafana stack, on the same timeline, as the render farm
    # telemetry it is investigating.
    service_instance_id: str = "agent-worker-0"
    grafana_otlp_endpoint_url: str = ""
    grafana_otlp_instance_id: str = ""
    grafana_access_policy_token: str = ""

    @property
    def use_vertex_ai(self) -> bool:
        """True when ADC via a Cloud project should be used instead of an API key."""
        return bool(self.google_cloud_project)

    # Microservice endpoints
    mcp_gateway_url: str = "http://localhost:8001"
    impact_engine_url: str = "http://localhost:8002"
    action_executor_url: str = "http://localhost:8003"

    # Pub/Sub & Firestore
    pubsub_runs_subscription: str = "studio-production-runs-sub"
    firestore_database: str = "(default)"


settings = Settings()
