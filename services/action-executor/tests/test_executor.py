"""Unit and integration tests for action-executor."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app
from src.audit import audit_store
from src.executor import action_engine
from src.verifier import verifier
from services.common.models import ActionType


@pytest.mark.asyncio
async def test_healthz_and_readyz():
    """Verify health and readiness probes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        res = await client.get("/readyz")
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_idempotent_action_execution(monkeypatch):
    """Verify approved action executes idempotently and records audit entry."""
    # Mock render_sim call
    async def mock_control_post(*args, **kwargs):
        class MockRes:
            status_code = 200
            def json(self):
                return {"status": "APPLIED", "message": "Renderer config rolled back to v2.4.0"}
            def raise_for_status(self):
                pass
        return MockRes()

    monkeypatch.setattr(action_engine._http_client, "post", mock_control_post)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "run_id": "run-approval-01",
            "option_id": "opt-01",
            "tenant_id": "t07",
            "user_id": "usr-supervisor-01",
            "action_type": ActionType.ROLLBACK_RENDERER_CONFIG.value,
            "parameters": {"target_version": "v2.4.0", "target_tile_size": 256},
        }

        # 1. First execution -> SUCCESS
        res1 = await client.post("/actions/execute", json=payload)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "SUCCESS"
        assert data1["action_type"] == "rollback_renderer_config"
        idempotency_key = data1["idempotency_key"]

        # 2. Duplicate second click with identical key -> ALREADY_APPLIED
        res2 = await client.post("/actions/execute", json=payload)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["status"] == "ALREADY_APPLIED"
        assert data2["idempotency_key"] == idempotency_key

        # 3. Check audit trail
        audit_res = await client.get("/audit?tenant_id=t07")
        assert audit_res.status_code == 200
        records = audit_res.json()
        assert len(records) >= 1
        assert records[-1]["run_id"] == "run-approval-01"
        assert records[-1]["user_id"] == "usr-supervisor-01"


@pytest.mark.asyncio
async def test_verification_endpoint(monkeypatch):
    """Verify post-remediation verification endpoint."""
    async def mock_post(url, *args, **kwargs):
        class MockRes:
            status_code = 200
            def json(self):
                if "impact" in str(url):
                    return {"delay_minutes": 0, "is_remediated": True, "at_risk_deliverables": []}
                return {"result": {"status": "healthy"}}
            def raise_for_status(self):
                pass
        return MockRes()

    monkeypatch.setattr(verifier._http_client, "post", mock_post)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/actions/verify", json={"tenant_id": "t07", "run_id": "run-verify-01"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "VERIFIED"
        assert data["is_recovered"] is True
        assert data["verification_impact"]["delay_minutes"] == 0
