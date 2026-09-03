"""Shared OpenTelemetry trace pipeline for Grafana Cloud.

Installing a global TracerProvider is the whole of the agent-observability
integration. `google-adk` emits GenAI-semantic-convention spans -- `invoke_agent`,
`execute_tool`, and `call_llm`, the last carrying token-usage attributes -- from a
module-level tracer resolved against the *global* provider. Once a provider
carrying an OTLP exporter is installed, those spans reach Grafana Cloud without a
single agent call site changing.

Ingest authenticates with HTTP Basic: the numeric instance id as the username and
a glc_ access policy token as the password. That is a different credential from
the glsa_ service account token the query path presents as a Bearer token, and
the two are not interchangeable.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Signal suffixes an operator may or may not have included when copying the
# endpoint out of the Grafana Cloud portal.
_SIGNAL_PATHS = ("/v1/traces", "/v1/metrics", "/v1/logs")

_provider: Optional[Any] = None
_configured_service: Optional[str] = None


def basic_auth_header(instance_id: str, access_policy_token: str) -> str:
    """Builds the HTTP Basic credential Grafana Cloud OTLP ingest expects."""
    raw = f"{instance_id}:{access_policy_token}".encode("ascii")
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


def derive_otlp_endpoint(base_url: str, signal: str) -> str:
    """Returns the per-signal OTLP/HTTP URL for a configured base endpoint.

    Tolerates a base that already carries any signal suffix, so one
    GRAFANA_OTLP_ENDPOINT_URL value works whether it was pasted with a path or
    without one.
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return ""
    for path in _SIGNAL_PATHS:
        if base.endswith(path):
            base = base[: -len(path)]
            break
    return f"{base}/v1/{signal}"


def configure_tracing(
    service_name: str,
    service_instance_id: str,
    endpoint_url: str,
    otlp_instance_id: str,
    access_policy_token: str,
) -> bool:
    """Installs a global TracerProvider exporting spans to Grafana Cloud.

    Returns True when the pipeline is active. Missing credentials are not an
    error: the service keeps the default no-op provider, every span becomes a
    cheap no-op, and local development without a Grafana stack still runs.

    Idempotent. The OTel API refuses to replace an already-installed provider and
    logs an error when asked to, so a repeat call is skipped rather than retried.
    """
    global _provider, _configured_service

    if _configured_service is not None:
        return _provider is not None

    if not (endpoint_url and otlp_instance_id and access_policy_token):
        logger.info(
            "Trace export disabled; set GRAFANA_OTLP_ENDPOINT_URL, "
            "GRAFANA_OTLP_INSTANCE_ID and GRAFANA_ACCESS_POLICY_TOKEN to enable"
        )
        _configured_service = service_name
        return False

    # Imported here so a service that never enables tracing does not pay the SDK
    # import cost at startup.
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from services.common.tls import enable_system_trust_store

    enable_system_trust_store()

    endpoint = derive_otlp_endpoint(endpoint_url, "traces")
    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers={
            "Authorization": basic_auth_header(otlp_instance_id, access_policy_token)
        },
    )

    # The instance id is pinned by the caller for the same reason the metric
    # pipeline pins it: a fresh UUID per process would split one service's spans
    # across a new `instance` on every restart.
    provider = TracerProvider(
        resource=Resource.create({
            "service.name": service_name,
            "service.instance.id": service_instance_id,
        })
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _provider = provider
    _configured_service = service_name
    logger.info(
        "Trace export to Grafana Cloud enabled",
        extra={"service": service_name, "endpoint": endpoint},
    )
    return True


def shutdown_tracing() -> None:
    """Flushes pending spans on shutdown. No-op when tracing was never enabled.

    Without the flush a short-lived run exits with the last batch still sitting
    in the BatchSpanProcessor queue, and the spans that matter most -- the tail of
    an investigation -- are the ones lost.
    """
    global _provider
    if _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=5000)
        _provider.shutdown()
    except Exception as exc:
        logger.warning("Error shutting down trace pipeline: %s", exc)
    finally:
        _provider = None
