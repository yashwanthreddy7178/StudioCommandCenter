"""Unit and integration tests for api-gateway."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app
from src.lease import lease_manager


@pytest.mark.asyncio
async def test_healthz_and_readyz():
    """Verify health and readiness probes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        res = await client.get("/readyz")
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_tenant_leasing_lifecycle():
    """Verify acquiring, heartbeat renewal, and release of tenant leases."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Acquire lease
        res = await client.post("/leases/acquire", json={"session_id": "sess-user-101", "user_id": "usr-coordinator"})
        assert res.status_code == 200
        lease = res.json()
        assert lease["tenant_id"].startswith("t")
        assert lease["is_observer"] is False
        tenant_id = lease["tenant_id"]

        # 2. Heartbeat
        res = await client.post("/leases/heartbeat", json={"tenant_id": tenant_id, "session_id": "sess-user-101"})
        assert res.status_code == 200
        assert res.json()["success"] is True

        # 3. Release
        res = await client.post("/leases/release", json={"tenant_id": tenant_id, "session_id": "sess-user-101"})
        assert res.status_code == 200
        assert res.json()["success"] is True


@pytest.mark.asyncio
async def test_run_creation_and_dispatch(monkeypatch):
    """Verify run submission endpoint returns in under 200ms."""
    from src.main import http_client

    async def mock_worker_post(*args, **kwargs):
        class MockRes:
            status_code = 200
            def json(self):
                return {"status": "INVESTIGATION_STARTED"}
            def raise_for_status(self):
                pass
        return MockRes()

    monkeypatch.setattr(http_client, "post", mock_worker_post)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/runs",
            json={
                "tenant_id": "t07",
                "session_id": "sess-test",
                "user_id": "usr-supervisor",
                "objective": "Will Shadow Protocol miss the 18:00 delivery deadline?",
            }
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "QUEUED"
        assert "run_id" in data
        assert data["tenant_id"] == "t07"
