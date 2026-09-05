"""Server-Sent Events (SSE) generator for investigation run event streams."""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator
import httpx
from src.config import settings
from services.common.telemetry import setup_logging

logger = setup_logging("stream-service-sse")

# Events after which a run has nothing further to say.
#
# APPROVAL_REQUIRED is deliberately absent: the stream has to stay open across
# the wait for a human, or the verification that follows the approval would never
# reach the browser. DEGRADED is absent too - it is emitted for a single failed
# tool call mid-investigation, not only at the end of a run.
TERMINAL_EVENTS = {"COMPLETED", "ERROR", "VERIFICATION"}


async def event_generator(run_id: str, since_seq: int = 0) -> AsyncGenerator[str, None]:
    """Streams run step events to the browser using Server-Sent Events (SSE)."""
    current_seq = since_seq
    last_heartbeat = time.time()

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Initial connection notification
        handshake = {"run_id": run_id, "connected": True, "event_type": "CONNECTED"}
        yield f"data: {json.dumps(handshake)}\n\n"

        started = time.time()
        finished = False

        while not finished:
            try:
                # Fetch new events from agent-worker
                url = f"{settings.agent_worker_url}/runs/{run_id}/events?since_seq={current_seq}"
                res = await client.get(url)
                if res.status_code == 200:
                    events = res.json()
                    for event in events:
                        seq = event.get("seq", current_seq + 1)
                        event_type = event.get("event_type", "message")
                        data_str = json.dumps(event)

                        # Deliberately unnamed. An `event:` field routes the frame
                        # to addEventListener(<name>) and bypasses onmessage
                        # entirely, so naming these silently dropped every event
                        # at the browser while the stream looked healthy. The
                        # client reads event_type from the payload instead.
                        yield f"id: {seq}\ndata: {data_str}\n\n"
                        current_seq = max(current_seq, seq)

                        if event_type in TERMINAL_EVENTS:
                            logger.info(
                                "Run reached a terminal event, closing stream",
                                extra={"run_id": run_id, "event": event_type},
                            )
                            finished = True
                            break

                if finished:
                    break

                now = time.time()

                # Without this the loop had no exit at all except the client
                # going away, and under an ASGI transport that signal never
                # arrives -- which is why the test suite hung rather than failed.
                if now - started >= settings.sse_max_stream_sec:
                    logger.info(
                        "SSE stream reached its duration ceiling",
                        extra={"run_id": run_id},
                    )
                    closing = {
                        "run_id": run_id,
                        "event_type": "STREAM_TIMEOUT",
                        "description": (
                            "Stream closed after reaching its duration limit. "
                            "Reopen it to resume from the last event id."
                        ),
                    }
                    yield f"data: {json.dumps(closing)}\n\n"
                    break

                if now - last_heartbeat >= settings.sse_heartbeat_interval_sec:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now

                await asyncio.sleep(settings.sse_poll_interval_sec)

            except asyncio.CancelledError:
                logger.info("Client disconnected from SSE stream", extra={"run_id": run_id})
                break
            except Exception as exc:
                logger.warning("Error fetching run events for SSE", extra={"run_id": run_id, "error": str(exc)})
                await asyncio.sleep(1.0)
