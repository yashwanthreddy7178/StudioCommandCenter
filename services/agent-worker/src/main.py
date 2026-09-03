"""FastAPI application entrypoint for agent-worker."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from src.config import settings
from src.agent.planner import planner
from src.firestore_store import store
from src.agent.tools import tool_client
from services.common.models import (
    EventType,
    EvidencePayload,
    RunDocument,
    RunState,
    StepEvent,
)
from services.common.telemetry import setup_logging

# Applied before any client is constructed: on a network that inspects TLS the
# default certifi bundle cannot verify the served certificate, and every
# outbound call fails. No-op in a container.
from services.common.tls import enable_system_trust_store
from services.common.tracing import configure_tracing, shutdown_tracing

enable_system_trust_store()

# Installed before the first ADK import resolves a tracer, so the GenAI spans ADK
# emits for every LLM call, tool invocation and token count are exported rather
# than dropped into the default no-op provider. This is what lets the agent be
# debugged in Grafana the same way the agent debugs the render farm.
tracing_enabled = configure_tracing(
    service_name=settings.service_name,
    service_instance_id=settings.service_instance_id,
    endpoint_url=settings.grafana_otlp_endpoint_url,
    otlp_instance_id=settings.grafana_otlp_instance_id,
    access_policy_token=settings.grafana_access_policy_token,
)

logger = setup_logging("agent-worker-api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Flushes buffered spans on shutdown.

    An investigation's most interesting spans are its last ones, and they sit in
    the batch processor queue until something flushes them.
    """
    yield
    shutdown_tracing()


app = FastAPI(
    title="Studio Production Commander - Agent Worker",
    description="Autonomous VFX incident investigation agent using Google ADK and Gemini",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InvestigateRequest(BaseModel):
    """Payload to launch an investigation run."""
    run_id: str
    tenant_id: str
    user_id: str = "usr-coordinator"
    session_id: str = "sess-default"
    objective: str = "Will Shadow Protocol miss the 18:00 VFX delivery deadline?"


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def healthz() -> Dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/readyz", status_code=status.HTTP_200_OK)
async def readyz() -> Dict[str, Any]:
    return {
        "ready": True,
        "service": settings.service_name,
        "planning_model": settings.planning_model,
        "trace_export": tracing_enabled,
    }


@app.post("/runs/investigate", response_model=Dict[str, Any])
async def start_investigation(req: InvestigateRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Enqueues and starts an autonomous investigation run."""
    run_doc = RunDocument(
        run_id=req.run_id,
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        session_id=req.session_id,
        objective=req.objective,
        state=RunState.QUEUED,
    )
    await store.save_run(run_doc)

    async def _execute_loop() -> None:
        try:
            completed_run = await planner.run_investigation(run_doc, store)
            await store.save_run(completed_run)
        except Exception as exc:
            logger.error("Investigation failed", extra={"run_id": req.run_id, "error": str(exc)})
            run_doc.state = RunState.FAILED
            run_doc.error_message = str(exc)
            await store.save_run(run_doc)

    background_tasks.add_task(_execute_loop)

    return {
        "status": "INVESTIGATION_STARTED",
        "run_id": req.run_id,
        "tenant_id": req.tenant_id,
    }


@app.get("/runs/{run_id}", response_model=RunDocument)
async def get_run(run_id: str) -> RunDocument:
    """Returns the state of an investigation run."""
    run = await store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found")
    return run


@app.post("/runs/{run_id}/verify", response_model=Dict[str, Any])
async def verify_run(run_id: str, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Starts post-remediation verification for a run and returns immediately.

    The settle window is 90 seconds, so this cannot be done inside the approval
    request without holding a browser connection open for its duration. The run
    moves to VERIFYING and the outcome arrives on the event stream the client is
    already reading.
    """
    run = await store.get_run(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found"
        )

    run.state = RunState.VERIFYING
    await store.save_run(run)

    async def _verify() -> None:
        seq = (run.step_count or 0) + 1
        try:
            result = await tool_client.verify_remediation(
                tenant_id=run.tenant_id,
                run_id=run_id,
                delay_minutes_before=run.impact.delay_minutes if run.impact else None,
                observed_fpm_before=run.impact.observed_throughput_fpm if run.impact else None,
            )
        except Exception as exc:
            logger.error("Verification failed", extra={"run_id": run_id, "error": str(exc)})
            run.state = RunState.DEGRADED
            await store.save_run(run)
            await store.emit_event(
                StepEvent(
                    seq=seq,
                    run_id=run_id,
                    tenant_id=run.tenant_id,
                    event_type=EventType.DEGRADED,
                    title="Verification Could Not Complete",
                    description=str(exc)[:200],
                    payload={"error": str(exc)[:300]},
                )
            )
            return

        recovered = bool(result.get("is_recovered"))
        run.state = RunState.COMPLETED if recovered else RunState.DEGRADED
        await store.save_run(run)
        await store.emit_event(
            StepEvent(
                seq=seq,
                run_id=run_id,
                tenant_id=run.tenant_id,
                event_type=EventType.VERIFICATION,
                title=f"Verification: {result.get('status', 'UNKNOWN')}",
                description=result.get("reason", ""),
                payload=result,
            )
        )

    background_tasks.add_task(_verify)
    return {"status": "VERIFYING", "run_id": run_id}


@app.get("/runs/{run_id}/events", response_model=List[StepEvent])
async def get_run_events(run_id: str, since_seq: int = 0) -> List[StepEvent]:
    """Returns step events for a run."""
    return await store.get_events(run_id, since_seq=since_seq)


@app.get("/evidence/{evidence_id}", response_model=EvidencePayload)
async def get_evidence(evidence_id: str) -> EvidencePayload:
    """Returns a raw evidence payload."""
    ev = await store.get_evidence(evidence_id)
    if not ev:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Evidence '{evidence_id}' not found")
    return ev
