"""OpenTelemetry metrics exporter for render-sim.

World state is sampled by observable gauges on the schedule owned by the OTLP
reader, so the export interval is decoupled from the simulation tick rate.

Every diagnostic label is attached as a data point attribute rather than a
resource attribute. Resource attributes are folded into `target_info` on
ingestion and would not be queryable as labels, which the tenant matchers in
section 5.4 and the localization test in section 6.2 both depend on.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from opentelemetry import trace as otel_trace
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from src.config import settings
from src.world import TenantProductionWorld
from services.common.telemetry import setup_logging
from services.common.tracing import configure_tracing, shutdown_tracing

logger = setup_logging("render-sim-otel")


def _telemetry_resource() -> Resource:
    """Builds the OTel resource with a stable instance identity.

    Left to the SDK, `service.instance.id` is a fresh UUID per process, which
    becomes a distinct `instance` label in Prometheus. Every restart then creates
    a second series per worker, and the previous process's final values stay
    queryable for the duration of the lookback window. A query then sees both the
    old and new value for the same worker and picks one arbitrarily, which is
    enough to make a recovered fleet still read as degraded.

    Pinning the id means restarts write to the same series. Deployments running
    more than one simulator instance should override it per instance.
    """
    return Resource.create({
        "service.name": settings.service_name,
        "service.instance.id": settings.service_instance_id,
    })


def _enable_system_trust_store() -> None:
    """Verify TLS against the OS certificate store instead of the certifi bundle.

    On a machine behind a TLS-inspecting proxy the served certificate is signed by
    a local root that exists in the OS store but not in certifi, so the default
    bundle cannot build a chain and every export fails verification. No-op when
    truststore is not installed, which is the normal case inside a container.
    """
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()
    logger.info("TLS verification delegated to the system trust store")


class TelemetryBuffer:
    """In-memory telemetry store for local inspection and offline testing."""

    def __init__(self, max_entries: int = 1000) -> None:
        self.metrics: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.traces: List[Dict[str, Any]] = []
        self._max_entries = max_entries

    def add_metric(self, entry: Dict[str, Any]) -> None:
        self.metrics.append(entry)
        if len(self.metrics) > self._max_entries:
            self.metrics.pop(0)

    def add_log(self, entry: Dict[str, Any]) -> None:
        self.logs.append(entry)
        if len(self.logs) > self._max_entries:
            self.logs.pop(0)

    def add_trace(self, entry: Dict[str, Any]) -> None:
        self.traces.append(entry)
        if len(self.traces) > self._max_entries:
            self.traces.pop(0)


class OTelTelemetryExporter:
    """Exports render farm telemetry to Grafana Cloud via OTLP, plus a local buffer."""

    def __init__(self) -> None:
        self.buffer = TelemetryBuffer()
        # Latest state per tenant, read by the observable gauge callbacks.
        self._worlds: Dict[str, TenantProductionWorld] = {}
        self._provider: Optional[MeterProvider] = None
        self._log_provider: Optional[Any] = None
        self._tracer: Optional[Any] = None
        self._farm_logger: Optional[logging.Logger] = None
        # Worker status lines are throttled to the export interval; the simulation
        # ticks far faster than anything needs to be logged.
        self._last_worker_log_at: Dict[str, datetime] = {}

        if settings.otlp_export_enabled:
            self._start_otlp_pipeline()
            self._start_log_pipeline()
            self._start_trace_pipeline()
        else:
            logger.info(
                "OTLP export disabled; set GRAFANA_OTLP_ENDPOINT_URL, "
                "GRAFANA_OTLP_INSTANCE_ID and GRAFANA_ACCESS_POLICY_TOKEN to enable"
            )

    def _start_otlp_pipeline(self) -> None:
        """Builds the OTLP/HTTP metric pipeline and registers observable gauges."""
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        _enable_system_trust_store()

        # Grafana Cloud authenticates ingest with HTTP Basic: numeric instance ID
        # as the username, glc_ access policy token as the password.
        credential = f"{settings.grafana_otlp_instance_id}:{settings.grafana_access_policy_token}"
        encoded = base64.b64encode(credential.encode("ascii")).decode("ascii")

        exporter = OTLPMetricExporter(
            endpoint=settings.otlp_metrics_endpoint,
            headers={"Authorization": f"Basic {encoded}"},
        )
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=int(settings.otel_export_interval_sec * 1000),
        )
        self._provider = MeterProvider(
            resource=_telemetry_resource(),
            metric_readers=[reader],
        )
        meter = self._provider.get_meter("render-sim")

        # Units are translated into Prometheus name suffixes on ingestion: "s"
        # yields _seconds, "By" yields _bytes, "1" yields _ratio. Each name below
        # already carries its correct suffix, so dimensionless counts declare no
        # unit at all -- unit "1" on a count produced names like
        # render_queue_depth_frames_ratio, which no query would ever match.
        gauges = [
            ("render_worker_frame_duration_seconds", "s", self._observe_frame_duration),
            ("render_worker_gpu_utilization_ratio", "1", self._observe_gpu_utilization),
            ("render_worker_gpu_memory_used_bytes", "By", self._observe_gpu_memory),
            ("render_worker_temperature_celsius", "Cel", self._observe_temperature),
            ("render_worker_cpu_utilization_ratio", "1", self._observe_cpu_utilization),
            ("render_worker_memory_used_bytes", "By", self._observe_memory),
            ("render_worker_active_jobs", "", self._observe_active_jobs),
            ("render_queue_depth_frames", "", self._observe_queue_depth),
            ("render_throughput_frames_per_minute", "", self._observe_throughput),
            ("render_baseline_throughput_frames_per_minute", "", self._observe_baseline_throughput),
            ("render_fleet_total_workers", "", self._observe_total_workers),
            ("render_fleet_degraded_workers", "", self._observe_degraded_workers),
        ]
        for name, unit, callback in gauges:
            meter.create_observable_gauge(name=name, unit=unit, callbacks=[callback])

        logger.info(
            "OTLP metric pipeline started",
            extra={
                "endpoint": settings.otlp_metrics_endpoint,
                "interval_sec": settings.otel_export_interval_sec,
            },
        )

    def _start_log_pipeline(self) -> None:
        """Builds the OTLP/HTTP log pipeline feeding Loki.

        Farm events go to a dedicated logger with propagation disabled so the
        service's own stdout logging is not shipped upstream as well.
        """
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        credential = f"{settings.grafana_otlp_instance_id}:{settings.grafana_access_policy_token}"
        encoded = base64.b64encode(credential.encode("ascii")).decode("ascii")

        exporter = OTLPLogExporter(
            endpoint=settings.otlp_logs_endpoint,
            headers={"Authorization": f"Basic {encoded}"},
        )
        self._log_provider = LoggerProvider(resource=_telemetry_resource())
        self._log_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))

        farm_logger = logging.getLogger("render-farm-events")
        farm_logger.setLevel(logging.INFO)
        farm_logger.propagate = False
        if not farm_logger.handlers:
            farm_logger.addHandler(
                LoggingHandler(level=logging.INFO, logger_provider=self._log_provider)
            )
        self._farm_logger = farm_logger

        logger.info(
            "OTLP log pipeline started",
            extra={"endpoint": settings.otlp_logs_endpoint},
        )

    def _start_trace_pipeline(self) -> None:
        """Builds the OTLP/HTTP trace pipeline feeding Tempo."""
        if not settings.emit_render_traces:
            logger.info("Render trace emission disabled by configuration")
            return

        enabled = configure_tracing(
            service_name=settings.service_name,
            service_instance_id=settings.service_instance_id,
            endpoint_url=settings.grafana_otlp_endpoint_url,
            otlp_instance_id=settings.grafana_otlp_instance_id,
            access_policy_token=settings.grafana_access_policy_token,
        )
        if not enabled:
            return

        self._tracer = otel_trace.get_tracer("render-sim")
        logger.info(
            "OTLP trace pipeline started",
            extra={"endpoint": settings.otlp_traces_endpoint},
        )

    def _emit_render_trace(
        self, world: TenantProductionWorld, worker: Any, now: datetime
    ) -> None:
        """Emits one frame-render trace for a worker.

        Timestamps are set explicitly rather than measured, because nothing here
        actually sleeps for the duration of a frame. The span tree is the point:
        asset fetch and output write are held at fixed cost while GPU time
        absorbs the whole of a frame's slowdown, so a trace search can attribute
        a slow frame to the GPU and rule out storage and the control API. A flat
        span carrying only a total duration would prove nothing the frame
        duration metric does not already say.
        """
        if self._tracer is None:
            return

        duration_sec = max(float(worker.current_frame_duration_sec), 0.3)
        fetch_sec = 1.2
        write_sec = 0.8
        gpu_sec = max(duration_sec - fetch_sec - write_sec, 0.1)

        # `now` is a naive datetime holding UTC. Calling .timestamp() on it makes
        # Python interpret it as local time, which on any machine that is not on
        # UTC shifts every span by the offset -- four hours into the future here.
        # Tempo stored them and no search over a recent window ever matched.
        end_ns = int(now.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        start_ns = end_ns - int(duration_sec * 1_000_000_000)

        attributes: Dict[str, Any] = {
            **self._worker_attributes(world, worker),
            "production_id": world.production_id,
            "tile_size": worker.tile_size,
            "scene": "sc_04_chase",
        }

        root = self._tracer.start_span(
            "render_frame", start_time=start_ns, attributes=attributes
        )
        context = otel_trace.set_span_in_context(root)

        cursor = start_ns
        for name, seconds in (
            ("fetch_assets", fetch_sec),
            ("gpu_render", gpu_sec),
            ("write_output", write_sec),
        ):
            child_end = cursor + int(seconds * 1_000_000_000)
            child = self._tracer.start_span(
                name, context=context, start_time=cursor, attributes=attributes
            )
            if name == "gpu_render":
                child.set_attribute(
                    "gpu_utilization_ratio",
                    round(worker.gpu_utilization_pct / 100.0, 3),
                )
                child.set_attribute(
                    "gpu_memory_used_mb", round(worker.gpu_memory_used_mb, 1)
                )
            child.end(end_time=child_end)
            cursor = child_end

        root.end(end_time=end_ns)

    def emit_config_event(
        self,
        world: TenantProductionWorld,
        event: str,
        affected_worker_ids: Optional[List[str]] = None,
    ) -> None:
        """Emits the discrete configuration-change line for a world.

        This is the evidence behind the temporal precedence test: it is written
        once, at the moment the change is applied, so its timestamp can be
        compared against the metric inflection rather than inferred from it.

        Identifying fields are duplicated into the message body because the OTLP
        to Loki mapping promotes only some attributes to stream labels; a line
        filter works regardless of how the mapping lands.
        """
        targets = affected_worker_ids or sorted(world.workers)
        body = (
            f"event={event} tenant_id={world.tenant_id} "
            f"renderer_version={world.renderer_version} tile_size={world.tile_size} "
            f"workers={','.join(targets)}"
        )
        attributes = {
            "event": event,
            "tenant_id": world.tenant_id,
            "renderer_version": world.renderer_version,
            "tile_size": world.tile_size,
            "worker_ids": ",".join(targets),
        }

        self.buffer.add_log({
            "timestamp": datetime.utcnow().isoformat(),
            "labels": {"tenant_id": world.tenant_id, "level": "info", "event": event},
            "line": body,
        })

        if self._farm_logger is not None:
            self._farm_logger.info(body, extra=attributes)

    # ------------------------------------------------------------------
    # Observable gauge callbacks
    # ------------------------------------------------------------------

    def _worker_attributes(
        self, world: TenantProductionWorld, worker: Any
    ) -> Dict[str, str]:
        """Data point attributes for one worker series.

        Deliberately excludes `is_degraded`. The agent has to infer degradation
        from the metrics, and labelling it would hand over the answer.
        """
        return {
            "tenant_id": world.tenant_id,
            "worker_id": worker.worker_id,
            "renderer_version": worker.renderer_version,
            "gpu_type": worker.gpu_type,
        }

    def _observe_workers(self, extract: Callable[[Any], float]) -> List[Observation]:
        observations: List[Observation] = []
        for world in list(self._worlds.values()):
            for worker in world.workers.values():
                observations.append(
                    Observation(
                        float(extract(worker)),
                        self._worker_attributes(world, worker),
                    )
                )
        return observations

    def _observe_fleet(self, extract: Callable[[Any], float]) -> List[Observation]:
        observations: List[Observation] = []
        for world in list(self._worlds.values()):
            observations.append(
                Observation(
                    float(extract(world)),
                    {
                        "tenant_id": world.tenant_id,
                        "production_id": world.production_id,
                    },
                )
            )
        return observations

    def _observe_frame_duration(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_workers(lambda w: w.current_frame_duration_sec)

    def _observe_gpu_utilization(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_workers(lambda w: w.gpu_utilization_pct / 100.0)

    def _observe_gpu_memory(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_workers(lambda w: w.gpu_memory_used_mb * 1024 * 1024)

    def _observe_temperature(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_workers(lambda w: w.temperature_celsius)

    def _observe_cpu_utilization(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_workers(lambda w: w.cpu_utilization_pct / 100.0)

    def _observe_memory(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_workers(lambda w: w.memory_used_mb * 1024 * 1024)

    def _observe_active_jobs(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_workers(lambda w: w.active_jobs)

    def _observe_queue_depth(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_fleet(lambda w: w.queue_depth)

    def _observe_throughput(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_fleet(lambda w: w.observed_throughput_fpm)

    def _observe_baseline_throughput(self, options: CallbackOptions) -> Iterable[Observation]:
        # Exported so the agent can derive the shortfall from Grafana rather than
        # carrying a hardcoded baseline of its own.
        return self._observe_fleet(lambda w: w.baseline_throughput_fpm)

    def _observe_total_workers(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_fleet(lambda w: len(w.workers))

    def _observe_degraded_workers(self, options: CallbackOptions) -> Iterable[Observation]:
        return self._observe_fleet(
            lambda w: sum(1 for x in w.workers.values() if x.is_degraded)
        )

    # ------------------------------------------------------------------
    # Simulation tick entry point
    # ------------------------------------------------------------------

    async def emit_world_telemetry(self, world: TenantProductionWorld) -> None:
        """Records the latest world state for export and local inspection."""
        self._worlds[world.tenant_id] = world
        now = datetime.utcnow()

        for wid, worker in world.workers.items():
            self.buffer.add_metric({
                "timestamp": now.isoformat(),
                "labels": {
                    "tenant_id": world.tenant_id,
                    "worker_id": wid,
                    "renderer_version": worker.renderer_version,
                    "gpu_type": worker.gpu_type,
                    "scene": "sc_04_chase",
                },
                "metrics": {
                    "render_worker_frame_duration_seconds": worker.current_frame_duration_sec,
                    "render_worker_gpu_utilization_ratio": worker.gpu_utilization_pct / 100.0,
                    "render_worker_gpu_memory_used_bytes": worker.gpu_memory_used_mb * 1024 * 1024,
                    "render_worker_temperature_celsius": worker.temperature_celsius,
                    "render_worker_cpu_utilization_ratio": worker.cpu_utilization_pct / 100.0,
                    "render_worker_memory_used_bytes": worker.memory_used_mb * 1024 * 1024,
                    "render_worker_active_jobs": worker.active_jobs,
                },
            })

        self.buffer.add_metric({
            "timestamp": now.isoformat(),
            "labels": {
                "tenant_id": world.tenant_id,
                "production_id": world.production_id,
            },
            "metrics": {
                "render_queue_depth_frames": world.queue_depth,
                "render_throughput_frames_per_minute": world.observed_throughput_fpm,
                "render_fleet_total_workers": len(world.workers),
                "render_fleet_degraded_workers": sum(
                    1 for w in world.workers.values() if w.is_degraded
                ),
            },
        })

        if not self._should_log_workers(world.tenant_id, now):
            return

        for wid, worker in world.workers.items():
            self._emit_render_trace(world, worker, now)

            if worker.is_degraded:
                level = "warn"
                line = (
                    f"[WARN] tenant_id={world.tenant_id} worker_id={wid} "
                    f"renderer_version={worker.renderer_version} "
                    f"tile_size={worker.tile_size} VRAM paging stall, "
                    f"duration={worker.current_frame_duration_sec:.1f}s"
                )
            else:
                level = "info"
                line = (
                    f"[INFO] tenant_id={world.tenant_id} worker_id={wid} "
                    f"renderer_version={worker.renderer_version} "
                    f"completed frame in {worker.current_frame_duration_sec:.1f}s, "
                    f"gpu_util={worker.gpu_utilization_pct:.1f}%"
                )

            self.buffer.add_log({
                "timestamp": now.isoformat(),
                "labels": {
                    "tenant_id": world.tenant_id,
                    "worker_id": wid,
                    "level": level,
                },
                "line": line,
            })

            if self._farm_logger is not None:
                attributes = {
                    "tenant_id": world.tenant_id,
                    "worker_id": wid,
                    "renderer_version": worker.renderer_version,
                    "gpu_type": worker.gpu_type,
                }
                if level == "warn":
                    self._farm_logger.warning(line, extra=attributes)
                else:
                    self._farm_logger.info(line, extra=attributes)

    def _should_log_workers(self, tenant_id: str, now: datetime) -> bool:
        """Rate-limits per-worker status lines to the configured export interval."""
        last = self._last_worker_log_at.get(tenant_id)
        if last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < settings.otel_export_interval_sec:
                return False
        self._last_worker_log_at[tenant_id] = now
        return True

    async def close(self) -> None:
        """Flushes and shuts down the OTLP pipelines."""
        for name, provider in (("metric", self._provider), ("log", self._log_provider)):
            if provider is None:
                continue
            try:
                provider.force_flush(timeout_millis=5000)
                provider.shutdown()
            except Exception as exc:
                logger.warning(
                    "Error shutting down OTLP pipeline",
                    extra={"pipeline": name, "error": str(exc)},
                )
        shutdown_tracing()
