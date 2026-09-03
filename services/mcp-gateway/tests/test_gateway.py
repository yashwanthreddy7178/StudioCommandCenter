"""Unit and integration tests for mcp-gateway."""
from __future__ import annotations

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from src import mcp_client as mcp_client_module
from src.main import app
from src.allowlist import (
    MCP_ALLOWLIST,
    MCP_WRITE_ALLOWLIST,
    FORBIDDEN_TOOLS,
    validate_tool_allowed,
    validate_write_tool_allowed,
    ToolAllowlistError,
)
from src.rewriter import inject_tenant_promql, inject_tenant_logql, rewrite_tool_parameters


@pytest.fixture
def stub_upstream(monkeypatch):
    """Replaces the Grafana MCP upstream with a counting stub.

    The gateway has no local fallback by design, so exercising cache and
    singleflight behaviour requires a double here. It also lets a test assert how
    many upstream calls actually happened, which is the point of singleflight.
    """
    calls: dict = {}

    async def _fake_call(tool_name: str, parameters: dict, tenant_id: str):
        calls[tool_name] = calls.get(tool_name, 0) + 1
        # list_datasources is the gateway resolving a UID for itself, not an
        # agent-initiated call, so it is counted separately from tool traffic.
        if tool_name == "list_datasources":
            return [{"uid": "test-ds-uid", "type": parameters.get("type", "prometheus")}]
        await asyncio.sleep(0.05)
        if tool_name == "list_loki_label_names":
            return {"labels": ["tenant_id", "worker_id", "level"]}
        return {"metrics": ["render_worker_frame_duration_seconds"]}

    monkeypatch.setattr(
        mcp_client_module.mcp_client, "call_upstream_mcp", _fake_call
    )
    return calls


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


def test_tenant_injection_promql_real_expressions():
    """Tenant isolation must survive the PromQL a model actually writes.

    Once the model selects its own queries these stop being bare metric names:
    aggregations, grouping clauses and range selectors all appear. An earlier
    implementation injected a matcher into every identifier, turning
    `by (renderer_version)` into `by (renderer_version{tenant_id="t01"})`, which
    Prometheus rejects as a parse error.
    """
    cases = [
        "render_queue_depth_frames",
        'render_worker_frame_duration_seconds{renderer_version="v2.4.1"}',
        "avg(render_worker_frame_duration_seconds) by (renderer_version)",
        "sum by (renderer_version) (render_worker_gpu_utilization_ratio)",
        "rate(render_worker_active_jobs[5m])",
        "topk(3, render_worker_frame_duration_seconds)",
        "count(render_worker_frame_duration_seconds > 100) by (renderer_version)",
        "histogram_quantile(0.95, sum(rate(render_worker_frame_duration_seconds[5m])) by (le))",
    ]
    for query in cases:
        result = inject_tenant_promql(query, "t01")
        assert 'tenant_id="t01"' in result, query
        # No leftover masking sentinels and no duplicated label sets.
        assert "\x00" not in result, query
        assert "}{" not in result, query
        # Grouping labels are labels, not metrics.
        assert 'renderer_version{' not in result, query
        assert '(le{' not in result, query
        # Applying it twice must not change the result.
        assert inject_tenant_promql(result, "t01") == result, query


def test_instant_queries_are_scoped_to_live_series():
    """Bare selectors must be restricted to series still being written.

    Changing a label value forks a series. A worker moving from renderer_version
    v2.4.0 to v2.4.1 leaves the old series queryable for the whole lookback
    window, and an instant query stamps both with the query time rather than the
    sample time, so the stale one cannot be identified afterwards. Without this,
    a rolled-back worker can still read as degraded.
    """
    from src.rewriter import rewrite_tool_parameters

    scoped = rewrite_tool_parameters(
        "query_prometheus",
        {"expr": "render_worker_frame_duration_seconds", "queryType": "instant"},
        "t01",
    )["expr"]
    assert scoped == 'last_over_time(render_worker_frame_duration_seconds{tenant_id="t01"}[1m])'

    # The tenant matcher belongs inside the selector, not outside the wrapper.
    assert scoped.index('tenant_id="t01"') < scoped.index("[1m]")

    # An expression already carrying a range vector must not be wrapped:
    # rate(last_over_time(m[1m])[5m]) is not valid PromQL.
    ranged = rewrite_tool_parameters(
        "query_prometheus",
        {"expr": "rate(render_worker_active_jobs[5m])", "queryType": "instant"},
        "t01",
    )["expr"]
    assert "last_over_time" not in ranged
    assert 'tenant_id="t01"' in ranged


def test_tenant_injection_logql():
    """Verify tenant isolation is applied as a structured-metadata label filter.

    OTLP log attributes reach Loki as structured metadata rather than stream
    labels, so putting tenant_id inside the stream selector matches nothing and
    returns an empty result instead of an isolated one.
    """
    res = inject_tenant_logql('{job="render"} |= "error"', "t07")
    assert res == '{job="render"} | tenant_id="t07" |= "error"'

    # Already-constrained queries are left alone rather than double-filtered.
    assert inject_tenant_logql(res, "t07") == res


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
async def test_gateway_caching_and_logging(stub_upstream):
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
async def test_singleflight_concurrent_deduplication(stub_upstream):
    """Verify concurrent requests with identical keys collapse to single upstream call."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Launch 5 concurrent calls
        req_payload = {
            "tool_name": "list_loki_label_names",
            "parameters": {},
            "tenant_id": "t09",
        }
        tasks = [client.post("/call", json=req_payload) for _ in range(5)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code == 200
            assert "labels" in r.json()["result"]

        # The point of singleflight: five identical concurrent keys collapse into
        # one upstream call rather than five.
        assert stub_upstream["list_loki_label_names"] == 1

        # And the datasource UID is resolved once and reused, not re-fetched per
        # request, even when the five arrive concurrently on a cold cache.
        assert stub_upstream["list_datasources"] == 1


def test_write_tools_are_unreachable_from_the_query_path():
    """The agent's path must reject every write tool by name.

    This is the property the whole approval gate rests on. The model chooses tool
    names, so if a write tool were reachable from /call, a prompt injection in
    telemetry could edit Grafana with no human in the loop. Asserted per tool
    rather than in aggregate, so adding a write tool without gating it fails here.
    """
    for tool_name in MCP_WRITE_ALLOWLIST:
        with pytest.raises(ToolAllowlistError):
            validate_tool_allowed(tool_name)

    assert not (MCP_ALLOWLIST & MCP_WRITE_ALLOWLIST)


def test_write_path_accepts_only_write_tools():
    """The write path is not a second way to run queries."""
    for tool_name in MCP_WRITE_ALLOWLIST:
        validate_write_tool_allowed(tool_name)

    for tool_name in ("query_prometheus", "query_loki_logs", "list_incidents"):
        with pytest.raises(ToolAllowlistError):
            validate_write_tool_allowed(tool_name)

    for tool_name in FORBIDDEN_TOOLS:
        with pytest.raises(ToolAllowlistError):
            validate_write_tool_allowed(tool_name)


@pytest.mark.asyncio
async def test_write_endpoint_rejects_a_query_tool_and_applies_a_write(stub_upstream):
    """/write refuses read tools, and a permitted write reaches upstream once."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        refused = await client.post(
            "/write",
            json={
                "tool_name": "query_prometheus",
                "parameters": {"expr": "up"},
                "tenant_id": "t01",
                "approval_id": "approval-abc",
            },
        )
        assert refused.status_code == 403

        # A write tool on the query path is refused just as firmly.
        refused_read_path = await client.post(
            "/call",
            json={
                "tool_name": "create_annotation",
                "parameters": {"text": "should not happen"},
                "tenant_id": "t01",
            },
        )
        assert refused_read_path.status_code == 403

        applied = await client.post(
            "/write",
            json={
                "tool_name": "create_annotation",
                "parameters": {"text": "rollback applied", "tags": ["run:r1"]},
                "tenant_id": "t01",
                "run_id": "r1",
                "approval_id": "approval-abc",
            },
        )
        assert applied.status_code == 200
        body = applied.json()
        # Writes are never served from cache; each one must reach Grafana.
        assert body["cache_hit"] is False
        assert stub_upstream.get("create_annotation") == 1


@pytest.mark.asyncio
async def test_write_requires_an_approval_id(stub_upstream):
    """A write with no approval behind it is rejected before it is attempted."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/write",
            json={
                "tool_name": "create_annotation",
                "parameters": {"text": "unapproved"},
                "tenant_id": "t01",
            },
        )
        assert res.status_code == 422
        assert "create_annotation" not in stub_upstream
