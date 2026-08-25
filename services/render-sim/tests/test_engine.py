"""Unit and integration tests for the render farm simulation engine."""
from __future__ import annotations

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
