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
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from src.config import settings
from src.world import TenantProductionWorld
from services.common.telemetry import setup_logging

logger = setup_logging("render-sim-otel")


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

        if settings.otlp_export_enabled:
            self._start_otlp_pipeline()
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
            resource=Resource.create({"service.name": settings.service_name}),
            metric_readers=[reader],
        )
        meter = self._provider.get_meter("render-sim")

        gauges = [
            ("render_worker_frame_duration_seconds", "s", self._observe_frame_duration),
            ("render_worker_gpu_utilization_ratio", "1", self._observe_gpu_utilization),
            ("render_worker_gpu_memory_used_bytes", "By", self._observe_gpu_memory),
            ("render_worker_temperature_celsius", "Cel", self._observe_temperature),
            ("render_worker_cpu_utilization_ratio", "1", self._observe_cpu_utilization),
            ("render_worker_memory_used_bytes", "By", self._observe_memory),
            ("render_worker_active_jobs", "1", self._observe_active_jobs),
            ("render_queue_depth_frames", "1", self._observe_queue_depth),
            ("render_throughput_frames_per_minute", "1", self._observe_throughput),
            ("render_fleet_total_workers", "1", self._observe_total_workers),
            ("render_fleet_degraded_workers", "1", self._observe_degraded_workers),
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

        for wid, worker in world.workers.items():
            if worker.is_degraded:
                level = "warn"
                line = (
                    f"[WARN] Worker {wid} [{worker.renderer_version}] "
                    f"tile_size={worker.tile_size} VRAM paging stall, "
                    f"duration={worker.current_frame_duration_sec:.1f}s"
                )
            else:
                level = "info"
                line = (
                    f"[INFO] Worker {wid} [{worker.renderer_version}] "
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

    async def close(self) -> None:
        """Flushes and shuts down the OTLP pipeline."""
        if self._provider is None:
            return
        try:
            self._provider.force_flush(timeout_millis=5000)
            self._provider.shutdown()
        except Exception as exc:
            logger.warning(
                "Error shutting down OTLP pipeline", extra={"error": str(exc)}
            )
