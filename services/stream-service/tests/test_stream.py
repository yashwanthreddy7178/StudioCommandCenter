"""Unit and integration tests for stream-service."""
from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.sse import event_generator


@pytest.fixture
def short_stream(monkeypatch):
    """Shrinks the stream's ceiling so a test can outlive it.

    Every one of these tests drives a run that never terminates, which is exactly
    the case the ceiling exists for. At the production value the suite would sit
    for half an hour per test.
    """
    monkeypatch.setattr(settings, "sse_max_stream_sec", 1.0)
    monkeypatch.setattr(settings, "sse_poll_interval_sec", 0.05)


class _FakeResponse:
    status_code = 200

    def __init__(self, events):
        self._events = events

    def json(self):
        return self._events


class _FakeClient:
    """Stands in for the agent-worker HTTP client."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return _FakeResponse(self._events)


@pytest.mark.asyncio
async def test_healthz_and_readyz():
    """Verify health and readiness probes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        res = await client.get("/readyz")
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_stream_connection_header(short_stream):
    """The SSE endpoint answers with an event-stream content type, and closes.

    Bounded deliberately. The generator's only exit used to be the client going
    away, and an ASGI transport never delivers that signal, so leaving the
    request open here hung the whole suite instead of failing it.
    """
    async def _open_and_read() -> str:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            async with client.stream("GET", "/runs/run-test/events") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                return "".join([chunk async for chunk in response.aiter_text()])

    body = await asyncio.wait_for(_open_and_read(), timeout=20)
    assert "CONNECTED" in body


@pytest.mark.asyncio
async def test_stream_closes_after_a_terminal_event(monkeypatch, short_stream):
    """A run that has reported its outcome must end the stream.

    Without this the connection kept polling agent-worker twice a second for a
    run that was already over, for as long as the browser stayed open.
    """
    events = [
        {"seq": 1, "event_type": "PLAN", "title": "Investigation Plan Initialized"},
        {"seq": 2, "event_type": "COMPLETED", "title": "No Remediation Required"},
        {"seq": 3, "event_type": "PLAN", "title": "must not be delivered"},
    ]
    monkeypatch.setattr("src.sse.httpx.AsyncClient", lambda *a, **k: _FakeClient(events))

    frames = [f async for f in event_generator("run-test", since_seq=0)]

    payloads = [
        json.loads(line[5:].strip())
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data:")
    ]
    types = [p.get("event_type") for p in payloads]

    assert types[-1] == "COMPLETED", f"stream did not end on the terminal event: {types}"
    assert "must not be delivered" not in "".join(frames)


@pytest.mark.asyncio
async def test_approval_does_not_close_the_stream(monkeypatch, short_stream):
    """The wait for a human is not the end of the run.

    Closing on APPROVAL_REQUIRED would drop the connection precisely while the
    supervisor is deciding, so the verification that follows their approval would
    never be delivered.
    """
    events = [
        {"seq": 1, "event_type": "APPROVAL_REQUIRED", "title": "Human Approval Required"},
    ]
    monkeypatch.setattr("src.sse.httpx.AsyncClient", lambda *a, **k: _FakeClient(events))

    frames = [f async for f in event_generator("run-test", since_seq=0)]
    joined = "".join(frames)

    # It ends only because the ceiling is short here, not because of the approval.
    assert "APPROVAL_REQUIRED" in joined
    assert "STREAM_TIMEOUT" in joined
