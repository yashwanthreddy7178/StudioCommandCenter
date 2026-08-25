"""Server-Sent Events (SSE) generator for investigation run event streams."""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator
import httpx
from services.stream-service.src.config import settings
from services.common.telemetry import setup_logging

logger = setup_logging("stream-service-sse")


async def event_generator(run_id: str, since_seq: int = 0) -> AsyncGenerator[str, None]:
    """Streams run step events to the browser using Server-Sent Events (SSE)."""
    current_seq = since_seq
    last_heartbeat = time.time()

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Initial connection notification
        yield f"event: connected\ndata: {json.dumps({'run_id': run_id, 'connected': True})}\n\n"

        while True:
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

                        yield f"id: {seq}\nevent: {event_type}\ndata: {data_str}\n\n"
                        current_seq = max(current_seq, seq)

                        # If run reached a terminal state or approval state, we can adjust polling rate
                        if event_type in {"APPROVAL_REQUIRED", "COMPLETED", "FAILED"}:
                            logger.info("Run reached milestone in stream", extra={"run_id": run_id, "event": event_type})

                # Heartbeat keep-alive
                now = time.time()
                if now - last_heartbeat >= settings.sse_heartbeat_interval_sec:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now

                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                logger.info("Client disconnected from SSE stream", extra={"run_id": run_id})
                break
            except Exception as exc:
                logger.warning("Error fetching run events for SSE", extra={"run_id": run_id, "error": str(exc)})
                await asyncio.sleep(1.0)
