"""FastAPI application entrypoint for agent-worker."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from src.config import settings
from src.agent.planner import planner
from src.firestore_store import store
from src.agent.tools import tool_client
from services.common.models import EvidencePayload, RunDocument, RunState, StepEvent
from services.common.telemetry import setup_logging

logger = setup_logging("agent-worker-api")

app = FastAPI(
    title="Studio Production Commander - Agent Worker",
    description="Autonomous VFX incident investigation agent using Google ADK and Gemini",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
