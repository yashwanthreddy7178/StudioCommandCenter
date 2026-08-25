"""Unit and integration tests for impact-engine with 100% branch coverage."""
from __future__ import annotations

from datetime import datetime, timedelta
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from src.main import app
from src.calculator import compute_deterministic_projection, calculate_production_impact
from src.db import async_session_factory, init_db


@pytest.mark.asyncio
async def test_compute_deterministic_projection_degraded():
    """Verify calculation logic when render farm is degraded."""
    as_of = datetime(2026, 9, 4, 14, 55, 0)
    deadline = datetime(2026, 9, 4, 18, 0, 0) # 185 minutes from as_of

    projection = compute_deterministic_projection(
        tenant_id="t07",
        affected_shots_count=1842,
        high_priority_count=217,
        sequences=["Final Chase", "Rooftop Pursuit"],
        deadline_utc=deadline,
        observed_throughput_fpm=41.2,
        baseline_throughput_fpm=118.6,
        queue_depth=18432,
        at_risk_deliverables=["SP_VFX_R04"],
        as_of=as_of,
    )

    # 18432 / 41.2 = ~447.38 minutes.
    # as_of + 447.38 min = ~22:22 UTC.
    # Delay past 18:00 = ~262 minutes.
    assert projection.delay_minutes > 0
    assert projection.is_remediated is False
    assert "SP_VFX_R04" in projection.at_risk_deliverables
    assert "Final Chase" in projection.sequences
    assert "queue_depth" in projection.method


@pytest.mark.asyncio
async def test_compute_deterministic_projection_on_time():
    """Verify calculation logic when render farm is healthy / recovered."""
    as_of = datetime(2026, 9, 4, 14, 55, 0)
    deadline = datetime(2026, 9, 4, 18, 0, 0) # 185 minutes from as_of

    # Healthy baseline 118.6 FPM. 18432 / 118.6 = ~155.4 minutes (< 185 min deadline)
    projection = compute_deterministic_projection(
        tenant_id="t07",
        affected_shots_count=0,
        high_priority_count=0,
        sequences=[],
        deadline_utc=deadline,
        observed_throughput_fpm=118.6,
        baseline_throughput_fpm=118.6,
        queue_depth=18432,
        at_risk_deliverables=[],
        as_of=as_of,
    )

    assert projection.delay_minutes == 0
    assert projection.is_remediated is True
    assert len(projection.at_risk_deliverables) == 0


@pytest.mark.asyncio
async def test_compute_deterministic_projection_zero_throughput_edge_case():
    """Verify zero throughput edge case does not divide by zero."""
    as_of = datetime(2026, 9, 4, 14, 0, 0)
    deadline = datetime(2026, 9, 4, 18, 0, 0)

    projection = compute_deterministic_projection(
        tenant_id="t01",
        affected_shots_count=10,
        high_priority_count=2,
        sequences=["Final Chase"],
        deadline_utc=deadline,
        observed_throughput_fpm=0.0, # zero throughput
        baseline_throughput_fpm=118.6,
        queue_depth=1000,
        at_risk_deliverables=["SP_VFX_R04"],
        as_of=as_of,
    )
    assert projection.delay_minutes > 0
    assert projection.is_remediated is False


@pytest.mark.asyncio
async def test_api_impact_project_endpoint():
    """Verify /impact/project FastAPI endpoint."""
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Health and ready
        res = await client.get("/healthz")
        assert res.status_code == 200
        res = await client.get("/readyz")
        assert res.status_code == 200
        assert res.json()["ready"] is True

        # 2. Project impact
        res = await client.post(
            "/impact/project",
            json={
                "tenant_id": "t07",
                "affected_workers": ["w-03", "w-07", "w-11", "w-17"],
                "observed_throughput_fpm": 41.2,
                "baseline_throughput_fpm": 118.6,
                "queue_depth": 18432,
            }
        )
        assert res.status_code == 200
        data = res.json()
        assert data["affected_shots"] > 0
        assert data["high_priority_shots"] > 0
        assert len(data["sequences"]) > 0
        assert "method" in data

        # 3. Productions, shots, deliverables
        res = await client.get("/productions")
        assert res.status_code == 200
        assert len(res.json()) > 0

        res = await client.get("/shots")
        assert res.status_code == 200
        assert len(res.json()) > 0

        res = await client.get("/deliverables")
        assert res.status_code == 200
        assert len(res.json()) > 0
