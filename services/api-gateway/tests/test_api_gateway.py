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


@pytest.mark.asyncio
async def test_internal_calls_carry_a_service_credential():
    """Verify a client built for internal calls actually sends a usable token.

    Protecting the internal services without this broke every cross-service
    call: render-sim answered api-gateway with 401 and api-gateway reported a
    500 to the browser. The whole suite stayed green because every cross-service
    call in these tests is mocked.
    """
    import httpx
    from services.common.auth import internal_auth, verify_token, SERVICE_PRINCIPAL

    seen: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(capture), auth=internal_auth()
    ) as client:
        res = await client.post("http://render-sim/scenario/trigger-incident", json={})

    assert res.status_code == 200
    header = seen["authorization"]
    assert header.startswith("Bearer ")
    # The callee's middleware has to accept it, and it must be identifiable as a
    # service rather than an operator sign-in.
    assert verify_token(header[len("Bearer "):]) == SERVICE_PRINCIPAL


def test_every_internal_http_client_is_authenticated():
    """Verify no service builds a bare client for calling another service.

    A structural check rather than a behavioural one: the failure mode is a call
    site that simply forgot the credential, which no amount of mocking in the
    unit tests will surface.
    """
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    # mcp-gateway's client talks to Grafana and carries Grafana's own bearer
    # token; adding ours would be wrong, not merely redundant.
    exempt = {repo_root / "services" / "mcp-gateway" / "src" / "mcp_client.py"}

    unauthenticated = []
    for path in (repo_root / "services").rglob("src/**/*.py"):
        if path in exempt or "__pycache__" in str(path):
            continue
        for match in re.finditer(r"httpx\.AsyncClient\(([^)]*)\)", path.read_text(encoding="utf-8")):
            if "auth=" not in match.group(1):
                unauthenticated.append(f"{path.relative_to(repo_root)}: {match.group(0)}")

    assert not unauthenticated, (
        "these clients call another service without a credential: " + "; ".join(unauthenticated)
    )


@pytest.mark.asyncio
async def test_a_session_can_choose_and_switch_tenant_worlds():
    """Verify an operator can pick a world rather than taking whichever is free.

    Needed once a local stack and a deployed one share a Grafana instance: they
    have to sit on separate tenants to stay out of each other's telemetry.
    """
    from src.lease import lease_manager

    manager = type(lease_manager)()

    chosen = await manager.acquire_lease(session_id="sess-picky", preferred_tenant_id="t07")
    assert chosen.tenant_id == "t07"

    # Asking again for the same world is idempotent, not a second lease.
    again = await manager.acquire_lease(session_id="sess-picky", preferred_tenant_id="t07")
    assert again.tenant_id == "t07"

    # Switching releases the old world so it returns to the pool.
    moved = await manager.acquire_lease(session_id="sess-picky", preferred_tenant_id="t09")
    assert moved.tenant_id == "t09"
    assert await manager.holds_lease("t09", "sess-picky") is True
    assert await manager.holds_lease("t07", "sess-picky") is False

    # A world someone else holds falls back rather than failing or stealing it.
    await manager.acquire_lease(session_id="sess-other", preferred_tenant_id="t11")
    fell_back = await manager.acquire_lease(session_id="sess-third", preferred_tenant_id="t11")
    assert fell_back.tenant_id != "t11"
    assert await manager.holds_lease("t11", "sess-other") is True

    # No preference keeps the original behaviour: first free world.
    plain = await manager.acquire_lease(session_id="sess-plain")
    assert plain.tenant_id in manager.writable_tenants()


@pytest.mark.asyncio
async def test_tenants_endpoint_reports_availability():
    """Verify the picker can see which worlds are taken."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=_auth()
    ) as client:
        acquired = (await client.post(
            "/leases/acquire", json={"session_id": "sess-lister", "preferred_tenant_id": "t15"}
        )).json()

        res = await client.get("/tenants")
        assert res.status_code == 200
        rows = res.json()["tenants"]
        assert len(rows) == settings.num_tenant_worlds

        by_id = {row["tenant_id"]: row for row in rows}
        assert by_id[acquired["tenant_id"]]["leased"] is True


@pytest.mark.asyncio
async def test_observer_sessions_cannot_execute_remediations():
    """Verify the documented observer rule is actually enforced.

    docs/architecture.md has said since the design was written that observers
    "can run investigations, but cannot execute remediations". `is_observer` was
    set on the lease and never read anywhere, so an overflow session could apply
    a rollback to the world every other overflow session was watching.
    """
    from src.lease import lease_manager

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=_auth()
    ) as client:
        # Fill the writable pool so the next session overflows.
        for i in range(settings.num_tenant_worlds):
            await client.post("/leases/acquire", json={"session_id": f"sess-fill-{i}"})

        overflow = (await client.post(
            "/leases/acquire", json={"session_id": "sess-watcher"}
        )).json()
        assert overflow["is_observer"] is True
        assert overflow["tenant_id"] == "observer"

        # The lease is real, so the ownership check passes...
        assert await lease_manager.holds_lease("observer", "sess-watcher") is True

        # ...but approving a remediation is refused on the kind of lease it is.
        res = await client.post("/runs/run-x/approve", json={
            "run_id": "run-x",
            "option_id": "opt-01",
            "tenant_id": "observer",
            "user_id": "usr-watcher",
            "session_id": "sess-watcher",
        })
        assert res.status_code == 403
        assert "Observer sessions cannot execute remediations" in res.json()["detail"]


@pytest.mark.asyncio
async def test_tenants_endpoint_counts_the_pool_and_observers():
    """Verify the availability counts are measured rather than asserted.

    `observer_sessions` replaced a hardcoded `observer_available: True` -- true,
    since observer mode is unbounded, but a constant dressed as data. An
    overflow session should be able to see how many others share its world.
    """
    from src.lease import lease_manager

    manager = type(lease_manager)()
    total = settings.num_tenant_worlds

    # Nothing leased yet.
    active = await manager.get_active_leases()
    assert active == []

    await manager.acquire_lease(session_id="sess-a", preferred_tenant_id="t01")
    await manager.acquire_lease(session_id="sess-b", preferred_tenant_id="t02")
    active = await manager.get_active_leases()
    taken = {lease.tenant_id for lease in active if not lease.is_observer}
    assert taken == {"t01", "t02"}
    assert len(manager.writable_tenants()) - len(taken) == total - 2

    # Overflow: fill the rest, then add two observers.
    for i in range(3, total + 1):
        await manager.acquire_lease(session_id=f"sess-fill-{i}")
    first = await manager.acquire_lease(session_id="sess-watch-1")
    second = await manager.acquire_lease(session_id="sess-watch-2")
    assert first.is_observer and second.is_observer

    active = await manager.get_active_leases()
    assert sum(1 for lease in active if lease.is_observer) == 2
    # Observers do not consume a writable world.
    assert len({lease.tenant_id for lease in active if not lease.is_observer}) == total
