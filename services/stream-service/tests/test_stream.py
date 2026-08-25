"""Unit and integration tests for stream-service."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from services.stream-service.src.main import app


@pytest.mark.asyncio
async def test_healthz_and_readyz():
    """Verify health and readiness probes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        res = await client.get("/readyz")
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_stream_connection_header():
    """Verify SSE streaming endpoint returns text/event-stream content type."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request stream with short read
        async with client.stream("GET", "/runs/run-test/events") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
