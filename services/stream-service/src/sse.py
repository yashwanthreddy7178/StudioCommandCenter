"""Server-Sent Events (SSE) generator for investigation run event streams."""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator
import httpx
from src.config import settings
from services.common.auth import internal_auth
from services.common.telemetry import setup_logging

logger = setup_logging("stream-service-sse")

# Events after which a run has nothing further to say.
#
# APPROVAL_REQUIRED is deliberately absent: the stream has to stay open across
# the wait for a human, or the verification that follows the approval would never
# reach the browser. DEGRADED is absent too - it is emitted for a single failed
# tool call mid-investigation, not only at the end of a run.
#
# VERIFICATION was here and should not have been. It closed the stream while the
# browser was still waiting for a COMPLETED that never arrived, so the launch
# button stayed on "Investigating" for the rest of the session and the browser
# quietly reconnected to a run that was over. It is not reliably an ending
# either: a verification can report NOT_RECOVERED or PARTIALLY_RECOVERED, which
# are outcomes, not conclusions. agent-worker now emits COMPLETED when the run
# actually ends, so this service no longer has to infer it.
TERMINAL_EVENTS = {"COMPLETED", "ERROR"}


async def event_generator(run_id: str, since_seq: int = 0) -> AsyncGenerator[str, None]:
    """Streams run step events to the browser using Server-Sent Events (SSE)."""
    current_seq = since_seq
    last_heartbeat = time.time()

    async with httpx.AsyncClient(timeout=10.0, auth=internal_auth()) as client:
        # Initial connection notification
        handshake = {"run_id": run_id, "connected": True, "event_type": "CONNECTED"}
        yield f"data: {json.dumps(handshake)}\n\n"

        started = time.time()
        finished = False

        while not finished:
            # Checked before the fetch, and outside the try.
            #
            # The ceiling used to sit after the upstream call. An agent-worker
            # that was unreachable sent every iteration straight to the exception
            # handler below, which sleeps and loops, so the duration limit was
            # never evaluated: the connection stayed open retrying once a second
            # until the client went away. Under Cloud Run's 3600s request timeout
            # that is an hour of held connection per browser, per run, and the
            # service that would have closed it is the one that is down.
            now = time.time()
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
