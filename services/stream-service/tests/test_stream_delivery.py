"""Regression test for SSE frame naming."""
from __future__ import annotations

import json

import pytest

from src.sse import event_generator


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
async def test_frames_are_unnamed_so_onmessage_receives_them(monkeypatch):
    """SSE frames must not carry an `event:` field.

    EventSource routes a named frame to addEventListener(<name>) and never calls
    onmessage. The client listens on onmessage and reads event_type out of the
    payload, so naming these delivered a healthy-looking stream in which every
    event was silently discarded and the UI never left its loading state.
    """
    events = [
        {"seq": 1, "event_type": "PLAN", "title": "Investigation Plan Initialized"},
        {"seq": 2, "event_type": "TOOL_CALL", "title": "Querying Grafana"},
    ]
    monkeypatch.setattr("src.sse.httpx.AsyncClient", lambda *a, **k: _FakeClient(events))

    frames = []
    generator = event_generator("run-test", since_seq=0)
    async for frame in generator:
        frames.append(frame)
        if len(frames) >= 3:
            await generator.aclose()
            break

    assert frames, "the stream produced no frames"
    for frame in frames:
        assert not any(
            line.startswith("event:") for line in frame.splitlines()
        ), f"frame carries an event: name and would bypass onmessage: {frame!r}"

    payloads = [
        json.loads(line[5:].strip())
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data:")
    ]
    # Every frame, including the handshake, must carry its type in the payload.
    assert all("event_type" in p for p in payloads)
    assert payloads[0]["event_type"] == "CONNECTED"
    assert "PLAN" in [p["event_type"] for p in payloads]
