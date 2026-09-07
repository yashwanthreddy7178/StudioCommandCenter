"""Unit and integration tests for impact-engine with 100% branch coverage."""
from __future__ import annotations

from datetime import datetime, timedelta
import pytest
from services.common.timeutil import utc_now
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


@pytest.mark.asyncio
async def test_api_impact_project_accepts_zoned_as_of():
    """Verify an `as_of` carrying a zone does not break the projection.

    Deliverable deadlines are stored naive, so an aware `as_of` used to reach
    the calculator and raise TypeError on the subtraction against the deadline,
    returning a 500. Both offset forms are resolved to the same instant.
    """
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "tenant_id": "t07",
            "affected_workers": ["w-03", "w-07"],
            "observed_throughput_fpm": 41.2,
            "baseline_throughput_fpm": 118.6,
            "queue_depth": 18432,
        }

        res_z = await client.post(
            "/impact/project", json={**payload, "as_of": "2026-09-04T14:55:00Z"}
        )
        assert res_z.status_code == 200

        # Same instant, stated as an offset rather than Z.
        res_offset = await client.post(
            "/impact/project", json={**payload, "as_of": "2026-09-04T16:55:00+02:00"}
        )
        assert res_offset.status_code == 200

        # Naive already, and the same instant again.
        res_naive = await client.post(
            "/impact/project", json={**payload, "as_of": "2026-09-04T14:55:00"}
        )
        assert res_naive.status_code == 200

        assert (
            res_z.json()["projected_completion_utc"]
            == res_offset.json()["projected_completion_utc"]
            == res_naive.json()["projected_completion_utc"]
        )


@pytest.mark.asyncio
async def test_untraced_workers_keep_the_production_deadline_and_report_no_delay():
    """Verify the fallback taken when no work traces to the affected workers.

    Two behaviours guard the same failure mode -- a healthy fleet reported as
    catastrophically late. The deadline falls back to the production's earliest
    deliverable rather than to `as_of`, which would make the entire queue drain
    time read as delay; and an incident that touched no shots is attributed no
    delay, so `delay_minutes` agrees with the zero affected shots beside it.
    """
    await init_db()
    as_of = datetime(2026, 9, 4, 14, 55, 0)

    async with async_session_factory() as session:
        projection = await calculate_production_impact(
            session=session,
            tenant_id="t07",
            affected_workers=["w-not-in-this-production"],
            observed_throughput_fpm=41.2,  # well under baseline: degraded fleet
            baseline_throughput_fpm=118.6,
            queue_depth=18432,
            as_of=as_of,
        )

    assert projection.affected_shots == 0
    assert projection.high_priority_shots == 0
    assert projection.sequences == []
    assert projection.at_risk_deliverables == []

    # The deadline is a fact about the production, not a restatement of as_of.
    assert projection.deadline_utc != as_of

    # Degraded throughput, but nothing of this production's work was affected.
    assert projection.delay_minutes == 0
    assert projection.is_remediated is True
    assert "no deliverable at risk" in projection.method


@pytest.mark.asyncio
async def test_api_production_sequences_are_counted_from_metadata():
    """Verify /production/sequences derives every figure from the seed."""
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/production/sequences")
        assert res.status_code == 200
        rows = res.json()
        assert len(rows) > 0

        for row in rows:
            assert row["total_shots"] > 0
            assert 0 <= row["completed_shots"] <= row["total_shots"]
            assert 0 <= row["rendering_shots"] <= row["total_shots"]
            assert row["progress_pct"] == pytest.approx(
                round(100.0 * row["completed_shots"] / row["total_shots"], 1)
            )
            assert row["priority"] in {"HIGH", "NORMAL"}

        # High-priority sequences sort ahead of normal ones.
        priorities = [r["priority"] for r in rows]
        assert priorities == sorted(priorities, key=lambda p: p != "HIGH")


@pytest.mark.asyncio
async def test_api_reanchor_deadline_moves_every_deliverable():
    """Verify /production/reanchor-deadline rewrites the stored deadlines.

    Runs last: it mutates the shared seed deadlines, which every projection is
    measured against.
    """
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = await client.get("/deliverables")
        assert before.status_code == 200
        assert len(before.json()) > 0

        res = await client.post(
            "/production/reanchor-deadline", json={"minutes_from_now": 185}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "REANCHORED"
        assert body["deliverables_updated"] == len(before.json())

        after = await client.get("/deliverables")
        deadlines = {d["deadline_utc"] for d in after.json()}
        assert deadlines == {body["deadline_utc"]}

        # The new deadline is a window ahead of now, not in the past.
        assert datetime.fromisoformat(body["deadline_utc"]) > utc_now()
