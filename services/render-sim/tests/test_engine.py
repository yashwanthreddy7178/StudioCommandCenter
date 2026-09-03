"""Unit and integration tests for the render farm simulation engine."""
from __future__ import annotations

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app
from src.engine import engine
from src.models import GPU_PROFILES
from src.world import TenantProductionWorld
from services.common.models import ActionType


@pytest.mark.asyncio
async def test_healthz_and_readyz():
    """Verify health and readiness endpoints respond 200."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        res = await client.get("/readyz")
        assert res.status_code == 200
        assert res.json()["ready"] is True
        assert res.json()["worlds_count"] >= 25


@pytest.mark.asyncio
async def test_tenant_world_initialization():
    """Verify 24 tenant worlds + 1 observer world are created with healthy nodes."""
    assert len(engine.worlds) >= 25
    assert "t01" in engine.worlds
    assert "t24" in engine.worlds
    assert "observer" in engine.worlds

    world = engine.get_world("t07")
    assert world is not None
    assert len(world.workers) == 8
    assert world.is_incident_active is False

    # The baseline must be derived from the GPU profiles the workers actually run
    # on. A hardcoded constant would make a healthy fleet read as permanently
    # degraded and feed a bogus shortfall into the impact projection.
    expected_fpm = round(
        sum(
            60.0 / GPU_PROFILES[w.gpu_type].baseline_duration_sec
            for w in world.workers.values()
        ),
        1,
    )
    assert world.baseline_throughput_fpm == expected_fpm


@pytest.mark.asyncio
async def test_incident_trigger_and_simulation_tick():
    """Verify incident injection alters worker telemetry and throughput."""
    world = engine.get_world("t01")
    assert world is not None

    # Trigger tile size regression on workers w-03 and w-07
    affected = ["w-03", "w-07"]
    engine.trigger_incident("t01", scenario_type="renderer_tile_regression", affected_worker_ids=affected)

    assert world.is_incident_active is True
    assert world.workers["w-03"].is_degraded is True
    assert world.workers["w-03"].renderer_version == "v2.4.1"
    assert world.workers["w-03"].gpu_utilization_pct < 40.0 # Mechanism: memory bus stall
    assert world.workers["w-03"].current_frame_duration_sec > 100.0

    # Healthy workers remain untouched
    assert world.workers["w-01"].is_degraded is False
    assert world.workers["w-01"].renderer_version == "v2.4.0"
    assert world.workers["w-01"].gpu_utilization_pct > 80.0

    # Simulate ticks
    for _ in range(5):
        world.tick()

    assert world.observed_throughput_fpm < world.baseline_throughput_fpm


@pytest.mark.asyncio
async def test_control_plane_rollback_remediation():
    """Verify applying rollback remediation restores fleet health."""
    world = engine.get_world("t01")
    assert world is not None

    # Roll back
    engine.rollback_renderer("t01", target_version="v2.4.0", target_tile_size=256)
    assert world.is_incident_active is False
    assert world.renderer_version == "v2.4.0"
    assert world.tile_size == 256

    for w in world.workers.values():
        assert w.is_degraded is False
        assert w.renderer_version == "v2.4.0"
        assert w.gpu_utilization_pct > 80.0


@pytest.mark.asyncio
async def test_api_endpoints_workflow():
    """End-to-end API test triggering an incident and applying remediation."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Trigger incident
        res = await client.post(
            "/scenario/trigger-incident",
            json={
                "tenant_id": "t02",
                "scenario_type": "renderer_tile_regression",
                "affected_worker_ids": ["w-03", "w-07"],
                "new_renderer_version": "v2.4.1",
                "new_tile_size": 2048,
            }
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "INCIDENT_TRIGGERED"
        assert data["world"]["degraded_workers"] == 2

        # 2. Control plane remediation
        res = await client.post(
            "/control/apply",
            json={
                "tenant_id": "t02",
                "action_type": ActionType.ROLLBACK_RENDERER_CONFIG.value,
                "parameters": {"target_version": "v2.4.0", "target_tile_size": 256},
            }
        )
        assert res.status_code == 200
        assert res.json()["status"] == "APPLIED"

        # 3. Check world state is recovered
        res = await client.get("/worlds/t02")
        assert res.status_code == 200
        world_data = res.json()
        assert world_data["is_incident_active"] is False
        assert world_data["degraded_workers"] == 0


def test_render_trace_attributes_slowdown_to_the_gpu_span():
    """A slow frame must show the extra time inside gpu_render, not fetch or write.

    This is the shape the trace-attribution criterion depends on. If a degraded
    frame simply produced one long flat span, a trace would prove nothing that the
    frame-duration metric does not already say; the value is in being able to rule
    out asset storage and the output write as the cause.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from src.otel_export import OTelTelemetryExporter
    from src.world import TenantProductionWorld

    exporter_memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter_memory))

    telemetry = OTelTelemetryExporter()
    telemetry._tracer = provider.get_tracer("test")

    world = TenantProductionWorld(tenant_id="t01", num_workers=2)
    world.trigger_incident(
        scenario_type="renderer_tile_regression",
        affected_worker_ids=["w-01"],
        new_version="v2.4.1",
        new_tile_size=2048,
    )

    healthy = world.workers["w-02"]
    degraded = world.workers["w-01"]
    now = datetime.utcnow()

    telemetry._emit_render_trace(world, healthy, now)
    telemetry._emit_render_trace(world, degraded, now)
    provider.force_flush()

    spans = exporter_memory.get_finished_spans()
    by_name = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)

    # One root plus three children per worker, for two workers.
    assert len(by_name["render_frame"]) == 2
    assert len(by_name["gpu_render"]) == 2
    assert len(by_name["fetch_assets"]) == 2
    assert len(by_name["write_output"]) == 2

    # Children hang off their own frame, not off each other or off nothing.
    root_ids = {s.context.span_id for s in by_name["render_frame"]}
    for name in ("fetch_assets", "gpu_render", "write_output"):
        for child in by_name[name]:
            assert child.parent is not None
            assert child.parent.span_id in root_ids

    def duration_sec(span):
        return (span.end_time - span.start_time) / 1_000_000_000

    # Fixed costs stay fixed across a healthy and a degraded frame; only the GPU
    # span absorbs the difference.
    fetch_durations = {round(duration_sec(s), 3) for s in by_name["fetch_assets"]}
    write_durations = {round(duration_sec(s), 3) for s in by_name["write_output"]}
    assert len(fetch_durations) == 1
    assert len(write_durations) == 1

    gpu_by_worker = {
        s.attributes["worker_id"]: duration_sec(s) for s in by_name["gpu_render"]
    }
    assert gpu_by_worker["w-01"] > gpu_by_worker["w-02"] * 3

    # Tenant and version must be queryable as span attributes, since that is what
    # a trace search would filter on.
    frame = by_name["render_frame"][0]
    assert frame.attributes["tenant_id"] == "t01"
    assert "renderer_version" in frame.attributes


def test_render_trace_timestamps_are_real_epoch_time():
    """Spans must land at the actual current time, not offset by the UTC offset.

    The exporter timestamps spans from a naive UTC datetime. Converting one with
    .timestamp() treats it as local time, so on any machine not running on UTC
    every span was written hours away from now and no search over a recent window
    matched it. Asserting relative span durations cannot catch that; only the
    absolute position can.
    """
    import time

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from src.otel_export import OTelTelemetryExporter
    from src.world import TenantProductionWorld

    exporter_memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter_memory))

    telemetry = OTelTelemetryExporter()
    telemetry._tracer = provider.get_tracer("test")

    world = TenantProductionWorld(tenant_id="t01", num_workers=1)
    worker = world.workers["w-01"]

    before = time.time()
    telemetry._emit_render_trace(world, worker, datetime.utcnow())
    provider.force_flush()
    after = time.time()

    frame = next(s for s in exporter_memory.get_finished_spans() if s.name == "render_frame")
    end_sec = frame.end_time / 1_000_000_000

    # A timezone-offset bug puts this hours away; a correct conversion puts it
    # within a second or two of the call.
    assert before - 5 <= end_sec <= after + 5, (
        f"span ended at {end_sec}, which is {end_sec - after:.0f}s from now"
    )
