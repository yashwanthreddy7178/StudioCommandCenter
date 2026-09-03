"""FastAPI application entrypoint for stream-service."""
from __future__ import annotations

from typing import Any, Dict
from fastapi import FastAPI, Header, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from src.config import settings
from src.sse import event_generator
from services.common.telemetry import setup_logging

logger = setup_logging("stream-service")

app = FastAPI(
    title="Studio Production Commander - Stream Service",
    description="High-concurrency Server-Sent Events (SSE) fan-out service",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # Wildcard origin with credentials is rejected outright by browsers, and
    # nothing needs it: auth is a JWT bearer token set on the request, not a
    # cookie the browser attaches on its own.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def healthz() -> Dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/readyz", status_code=status.HTTP_200_OK)
async def readyz() -> Dict[str, Any]:
    return {"ready": True, "service": settings.service_name}


@app.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    since_seq: int = Query(0, description="Sequence index to resume streaming from"),
    last_event_id: str = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Streams run step events via SSE with reconnection support."""
    # Check Last-Event-ID header if query param not explicitly supplied
    resume_seq = since_seq
    if last_event_id and last_event_id.isdigit():
        resume_seq = max(resume_seq, int(last_event_id))

    logger.info("Opening SSE stream for run", extra={"run_id": run_id, "since_seq": resume_seq})

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no", # Disable proxy buffering
    }

    return StreamingResponse(
        event_generator(run_id=run_id, since_seq=resume_seq),
        media_type="text/event-stream",
        headers=headers,
    )
