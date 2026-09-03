"""Unit and integration tests for action-executor."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app
from src.audit import audit_store
from src.executor import action_engine
from src.grafana_writeback import grafana_writeback
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


def _instant(samples):
    """Prometheus instant-query payload shaped like a real MCP result."""
    return {
        "data": [
            {"metric": {"worker_id": w, "renderer_version": v}, "value": [0, str(x)]}
            for w, v, x in samples
        ]
    }


def _scalar(value):
    return {"data": [{"metric": {}, "value": [0, str(value)]}]}


def _telemetry_mock(durations, throughput, delay_minutes):
    """Builds an mcp-gateway/impact-engine double returning the given fleet state."""
    async def mock_post(url, *args, **kwargs):
        payload = kwargs.get("json", {})
        class MockRes:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                if "impact" in str(url):
                    return {"delay_minutes": delay_minutes, "at_risk_deliverables": []}
                expr = payload.get("parameters", {}).get("expr", "")
                if expr == "render_worker_frame_duration_seconds":
                    return {"result": _instant(durations)}
                if expr == "render_throughput_frames_per_minute":
                    return {"result": _scalar(throughput)}
                if expr == "render_baseline_throughput_frames_per_minute":
                    return {"result": _scalar(18.5)}
                return {"result": _scalar(2800)}
        return MockRes()
    return mock_post


HEALTHY_FLEET = [
    ("w-01", "v2.4.0", 22.0), ("w-02", "v2.4.0", 24.0),
    ("w-03", "v2.4.0", 22.5), ("w-04", "v2.4.0", 23.0),
]
STILL_DEGRADED_FLEET = [
    ("w-01", "v2.4.1", 145.0), ("w-02", "v2.4.0", 24.0),
    ("w-03", "v2.4.1", 143.0), ("w-04", "v2.4.0", 23.0),
]


@pytest.mark.asyncio
async def test_verification_reports_recovery(monkeypatch):
    """A fleet back at baseline with the deadline met verifies as recovered."""
    monkeypatch.setattr(
        verifier._http_client, "post", _telemetry_mock(HEALTHY_FLEET, 18.5, 0)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/actions/verify", json={
            "tenant_id": "t07", "run_id": "run-verify-01",
            "delay_minutes_before": 149, "settle_seconds": 0,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "VERIFIED"
        assert data["is_recovered"] is True
        assert data["still_degraded_workers"] == []
        assert data["delay_minutes_recovered"] == 149


@pytest.mark.asyncio
async def test_verification_reports_failure(monkeypatch):
    """Workers still degraded must not be reported as recovered.

    The previous implementation hardcoded is_recovered=True and a restored
    throughput figure, so a failed remediation verified as a success.
    """
    monkeypatch.setattr(
        verifier._http_client, "post", _telemetry_mock(STILL_DEGRADED_FLEET, 11.8, 90)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/actions/verify", json={
            "tenant_id": "t07", "run_id": "run-verify-02",
            "delay_minutes_before": 149, "settle_seconds": 0,
        })
        data = res.json()
        assert data["status"] == "NOT_RECOVERED"
        assert data["is_recovered"] is False
        assert sorted(data["still_degraded_workers"]) == ["w-01", "w-03"]


@pytest.mark.asyncio
async def test_verification_reports_partial_recovery(monkeypatch):
    """Healthy workers but a missed deadline is partial, not success."""
    monkeypatch.setattr(
        verifier._http_client, "post", _telemetry_mock(HEALTHY_FLEET, 18.5, 40)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/actions/verify", json={
            "tenant_id": "t07", "run_id": "run-verify-03",
            "delay_minutes_before": 149, "settle_seconds": 0,
        })
        data = res.json()
        assert data["status"] == "PARTIALLY_RECOVERED"
        assert data["is_recovered"] is False
        assert data["delay_minutes_recovered"] == 109


@pytest.mark.asyncio
async def test_verification_without_telemetry_is_unverified(monkeypatch):
    """No post-action telemetry means no conclusion, not an assumed success."""
    async def failing_post(url, *args, **kwargs):
        raise RuntimeError("mcp-gateway unreachable")

    monkeypatch.setattr(verifier._http_client, "post", failing_post)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/actions/verify", json={
            "tenant_id": "t07", "run_id": "run-verify-04", "settle_seconds": 0,
        })
        data = res.json()
        assert data["status"] == "UNVERIFIED"
        assert data["is_recovered"] is False


class _StubResponse:
    """Minimal httpx-shaped response for the gateway write endpoint."""

    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        pass


@pytest.fixture
def applied_control_action(monkeypatch):
    """Makes the render control plane report a successful rollback."""

    async def _control_post(*args, **kwargs):
        return _StubResponse(
            {"status": "APPLIED", "message": "Renderer config rolled back to v2.4.0"}
        )

    monkeypatch.setattr(action_engine._http_client, "post", _control_post)


@pytest.mark.asyncio
async def test_approved_action_is_written_back_to_grafana(
    applied_control_action, monkeypatch
):
    """An approved remediation annotates Grafana, and opens an incident when a
    deliverable is at risk.

    Reading Grafana and then acting silently leaves no record where the people who
    own the stack are looking, so the write-back is part of the action, not a
    decoration on it.
    """
    writes: list = []

    async def _gateway_post(url, json=None, **kwargs):
        writes.append(json)
        return _StubResponse({"result": {"id": len(writes)}})

    monkeypatch.setattr(grafana_writeback._http_client, "post", _gateway_post)

    result = await action_engine.execute_approved_action(
        run_id="run-writeback-01",
        option_id="opt-01",
        tenant_id="t11",
        user_id="usr-supervisor-02",
        action_type=ActionType.ROLLBACK_RENDERER_CONFIG,
        parameters={"target_version": "v2.4.0"},
        option_title="Rollback Renderer Configuration",
        production_consequence="Clears the projected 47 minute delay.",
        at_risk_deliverables=["sq_18_finale"],
    )

    assert result["status"] == "SUCCESS"
    tools = [w["tool_name"] for w in writes]
    assert tools == ["create_annotation", "create_incident"]

    # Every write carries the approval that authorised it, so the audit trail can
    # name it later.
    assert all(w["approval_id"] == result["idempotency_key"] for w in writes)

    annotation = writes[0]["parameters"]
    assert "usr-supervisor-02" in annotation["text"]
    assert "47 minute delay" in annotation["text"]
    assert "run:run-writeback-01" in annotation["tags"]

    incident = writes[1]["parameters"]
    assert incident["title"] == "Rollback Renderer Configuration"
    # The farm is simulated, so incidents must not be filed as live ones.
    assert incident["isDrill"] is True
    assert "sq_18_finale" in incident["attachCaption"]
    # Labels need both halves of the key/label pair. A label carrying only one is
    # accepted and then silently dropped, so the incident arrives with no tenant
    # or run on it and nothing reports a problem.
    assert incident["labels"] == [
        {"key": "tenant", "label": "t11"},
        {"key": "run", "label": "run-writeback-01"},
    ]
    assert all({"key", "label"} <= set(l) for l in incident["labels"])

    writeback = result["result"]["grafana_writeback"]
    assert writeback["annotation"]["status"] == "CREATED"
    assert writeback["incident"]["status"] == "CREATED"


@pytest.mark.asyncio
async def test_grafana_failure_does_not_fail_the_remediation(
    applied_control_action, monkeypatch
):
    """A Grafana outage must not turn an applied rollback into a reported failure.

    The control plane has already changed by the time the write-back runs. Failing
    the action here would tell a supervisor the fleet was untouched when it was.
    """

    async def _gateway_post(*args, **kwargs):
        raise RuntimeError("grafana unreachable")

    monkeypatch.setattr(grafana_writeback._http_client, "post", _gateway_post)

    result = await action_engine.execute_approved_action(
        run_id="run-writeback-02",
        option_id="opt-01",
        tenant_id="t12",
        user_id="usr-supervisor-03",
        action_type=ActionType.ROLLBACK_RENDERER_CONFIG,
        parameters={"target_version": "v2.4.0"},
        at_risk_deliverables=["sq_18_finale"],
    )

    assert result["status"] == "SUCCESS"
    writeback = result["result"]["grafana_writeback"]
    # The failure is reported rather than swallowed, so the audit record shows it.
    assert writeback["annotation"]["status"] == "FAILED"
    assert writeback["incident"]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_no_incident_without_a_deliverable_at_risk(
    applied_control_action, monkeypatch
):
    """A routine remediation annotates, but does not open an incident."""
    writes: list = []

    async def _gateway_post(url, json=None, **kwargs):
        writes.append(json)
        return _StubResponse({"result": {}})

    monkeypatch.setattr(grafana_writeback._http_client, "post", _gateway_post)

    await action_engine.execute_approved_action(
        run_id="run-writeback-03",
        option_id="opt-02",
        tenant_id="t13",
        user_id="usr-supervisor-04",
        action_type=ActionType.SCALE_RENDER_WORKERS,
        parameters={"additional_workers": 4},
        at_risk_deliverables=[],
    )

    assert [w["tool_name"] for w in writes] == ["create_annotation"]
