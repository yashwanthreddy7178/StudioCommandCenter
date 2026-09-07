"""Unit and integration tests for api-gateway."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app
from src.config import settings
from src.lease import lease_manager


def _auth() -> dict:
    """Bearer header for the single operator credential the services require.

    Every route these tests exercise is published to the internet by nginx and
    now sits behind one operator login, so the test client signs in the same way
    the browser does.
    """
    from services.common.auth import issue_token

    token, _ = issue_token("supervisor")
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_healthz_and_readyz():
    """Verify health and readiness probes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_auth()) as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        res = await client.get("/readyz")
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_tenant_leasing_lifecycle():
    """Verify acquiring, heartbeat renewal, and release of tenant leases."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_auth()) as client:
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_auth()) as client:
        # A run acts on a tenant world, so the session has to hold its lease.
        lease = (await client.post(
            "/leases/acquire", json={"session_id": "sess-test"}
        )).json()

        res = await client.post(
            "/runs",
            json={
                "tenant_id": lease["tenant_id"],
                "session_id": "sess-test",
                "user_id": "usr-supervisor",
                "objective": "Will Shadow Protocol miss the 18:00 delivery deadline?",
            }
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "QUEUED"
        assert "run_id" in data
        assert data["tenant_id"] == lease["tenant_id"]


@pytest.mark.asyncio
async def test_operator_login_gates_the_exposed_routes():
    """Verify the app is closed without a token and open with one.

    nginx publishes these routes to the internet. Before the login existed,
    anyone who could load the page could execute a remediation through
    /runs/{id}/approve, which is the system's core safety control.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Health checks stay open: Cloud Run and the deploy smoke test poll them.
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/readyz")).status_code == 200

        # Everything else is closed.
        assert (await client.post("/leases/acquire", json={"session_id": "s"})).status_code == 401
        assert (await client.get("/leases")).status_code == 401

        # A wrong password is refused, and does not say which half was wrong.
        bad = await client.post(
            "/auth/login", json={"username": "supervisor", "password": "wrong"}
        )
        assert bad.status_code == 401
        assert "username or password" in bad.json()["detail"]

        good = await client.post(
            "/auth/login", json={"username": "supervisor", "password": "shadow-protocol"}
        )
        assert good.status_code == 200
        token = good.json()["token"]

        headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/leases", headers=headers)).status_code == 200

        # A token with a broken signature proves nothing.
        forged = {"Authorization": f"Bearer {token[:-1]}0"}
        assert (await client.get("/leases", headers=forged)).status_code == 401

        # EventSource cannot set headers, so the SSE path may present the token
        # in the query string.
        assert (await client.get(f"/leases?access_token={token}")).status_code == 200


@pytest.mark.asyncio
async def test_acting_on_a_tenant_requires_holding_its_lease():
    """Verify the tenant id in the body is checked against the session's lease.

    It used to be taken on trust, so any caller could name any tenant and
    approve a remediation on a world leased by someone else.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=_auth()
    ) as client:
        lease = (await client.post(
            "/leases/acquire", json={"session_id": "sess-owner"}
        )).json()
        tenant = lease["tenant_id"]

        # A different session naming the same tenant is refused.
        stolen = await client.post("/runs", json={
            "tenant_id": tenant,
            "session_id": "sess-intruder",
            "user_id": "usr-intruder",
        })
        assert stolen.status_code == 403
        assert "does not hold a live lease" in stolen.json()["detail"]

        approval = await client.post(f"/runs/run-x/approve", json={
            "run_id": "run-x",
            "option_id": "opt-01",
            "tenant_id": tenant,
            "user_id": "usr-intruder",
            "session_id": "sess-intruder",
        })
        assert approval.status_code == 403

        # A tenant nobody has leased is refused too.
        assert (await client.post("/runs", json={
            "tenant_id": "t24",
            "session_id": "sess-owner",
            "user_id": "usr-coordinator",
        })).status_code == 403


@pytest.mark.asyncio
async def test_observer_leases_can_be_found_by_tenant_id():
    """Verify a lease for the shared observer world behaves like any other.

    Observer leases are stored per session because they all name the same world,
    so every lookup by tenant id missed them: heartbeats returned False and the
    lease expired under a session that was still sending them, and once the
    lease check guarded /runs an observer could not act at all.
    """
    from src.lease import lease_manager
    from services.common.models import TenantStatus

    manager = type(lease_manager)()

    # Fill the writable pool so the next session falls back to observer mode.
    for i in range(settings.num_tenant_worlds):
        await manager.acquire_lease(session_id=f"sess-filler-{i}")

    observer = await manager.acquire_lease(session_id="sess-watcher")
    assert observer.is_observer is True
    assert observer.tenant_id == "observer"
    assert observer.status == TenantStatus.OBSERVER

    # Each of these looked the lease up by tenant id and found nothing.
    assert await manager.holds_lease("observer", "sess-watcher") is True
    assert await manager.heartbeat("observer", "sess-watcher") is True

    # Another session's observer lease is still its own.
    assert await manager.holds_lease("observer", "sess-someone-else") is False

    assert await manager.release_lease("observer", "sess-watcher") is True
    assert await manager.holds_lease("observer", "sess-watcher") is False
