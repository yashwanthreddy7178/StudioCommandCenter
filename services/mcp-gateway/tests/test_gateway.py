"""Unit and integration tests for mcp-gateway."""
from __future__ import annotations

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app
from src.allowlist import MCP_ALLOWLIST, FORBIDDEN_TOOLS, validate_tool_allowed, ToolAllowlistError
from src.rewriter import inject_tenant_promql, inject_tenant_logql, rewrite_tool_parameters


def test_allowlist_validation():
    """Verify allowable and forbidden tools."""
    # Allowed tools pass
    for tool in MCP_ALLOWLIST:
        validate_tool_allowed(tool)

    # Unknown tools raise ToolAllowlistError
    with pytest.raises(ToolAllowlistError) as exc:
        validate_tool_allowed("unknown_custom_tool")
    assert "not in the Grafana MCP allowlist" in str(exc.value)

    # Forbidden assistant tools raise ToolAllowlistError
    for forbidden in FORBIDDEN_TOOLS:
        with pytest.raises(ToolAllowlistError) as exc:
            validate_tool_allowed(forbidden)
        assert "strictly forbidden" in str(exc.value)


def test_tenant_injection_promql():
    """Verify tenant_id matcher injection into PromQL expressions."""
    # Metric with braces
    res = inject_tenant_promql('render_worker_gpu_utilization_ratio{worker_id="w-03"}', "t07")
    assert 'tenant_id="t07"' in res
    assert 'worker_id="w-03"' in res

    # Bare metric
    res = inject_tenant_promql('render_queue_depth_frames', "t02")
    assert res == 'render_queue_depth_frames{tenant_id="t02"}'


def test_tenant_injection_logql():
    """Verify tenant_id matcher injection into LogQL queries."""
    res = inject_tenant_logql('{job="render"} |= "error"', "t07")
    assert '{tenant_id="t07", job="render"} |= "error"' == res


@pytest.mark.asyncio
async def test_gateway_allowlist_endpoint_blocking():
    """Verify gateway API rejects forbidden tools with 403."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Forbidden assistant tool
        res = await client.post(
            "/call",
            json={
                "tool_name": "ask_assistant",
                "parameters": {"question": "why is the render slow?"},
                "tenant_id": "t01",
            }
        )
        assert res.status_code == 403
        assert "forbidden" in res.json()["detail"].lower()

        # Unknown tool
        res = await client.post(
            "/call",
            json={
                "tool_name": "delete_all_servers",
                "parameters": {},
                "tenant_id": "t01",
            }
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_gateway_caching_and_logging():
    """Verify caching, deduplication, and structured call logs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First call (miss)
        res1 = await client.post(
            "/call",
            json={
                "tool_name": "list_prometheus_metric_names",
                "parameters": {},
                "tenant_id": "t07",
                "run_id": "run-test-01",
            }
        )
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["cache_hit"] is False
        assert "metrics" in data1["result"]

        # Second call within TTL (hit)
        res2 = await client.post(
            "/call",
            json={
                "tool_name": "list_prometheus_metric_names",
                "parameters": {},
                "tenant_id": "t07",
                "run_id": "run-test-01",
            }
        )
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["cache_hit"] is True
        assert data2["result"] == data1["result"]

        # Verify structured logs endpoint
        logs_res = await client.get("/logs?tenant_id=t07")
        assert logs_res.status_code == 200
        logs = logs_res.json()
        assert len(logs) >= 2
        assert logs[-1]["tenant_id"] == "t07"
        assert logs[-1]["run_id"] == "run-test-01"


@pytest.mark.asyncio
async def test_singleflight_concurrent_deduplication():
    """Verify concurrent requests with identical keys collapse to single upstream call."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Launch 5 concurrent calls
        req_payload = {
            "tool_name": "list_alert_rules",
            "parameters": {"filter": "render"},
            "tenant_id": "t09",
        }
        tasks = [client.post("/call", json=req_payload) for _ in range(5)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code == 200
            assert "rules" in r.json()["result"]
