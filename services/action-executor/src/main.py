"""FastAPI application entrypoint for action-executor."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from src.config import settings
from src.executor import action_engine
from src.verifier import verifier
from src.audit import audit_store
from services.common.models import ActionType, AuditRecord
from services.common.telemetry import setup_logging

logger = setup_logging("action-executor-api")

app = FastAPI(
    title="Studio Production Commander - Action Executor",
    description="Idempotent remediation execution service with audit logging and verification",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExecuteActionRequest(BaseModel):
    """Payload to execute an approved remediation action."""
    run_id: str
    option_id: str
    tenant_id: str
    user_id: str = "usr-supervisor"
    action_type: ActionType
    parameters: Dict[str, Any] = Field(default_factory=dict)


class VerifyActionRequest(BaseModel):
    """Payload to trigger verification of an applied remediation."""
    tenant_id: str
    run_id: str


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def healthz() -> Dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/readyz", status_code=status.HTTP_200_OK)
async def readyz() -> Dict[str, Any]:
    return {"ready": True, "service": settings.service_name}


@app.post("/actions/execute", response_model=Dict[str, Any])
async def execute_action(req: ExecuteActionRequest) -> Dict[str, Any]:
    """Idempotently executes an approved remediation action and records an audit log."""
    result = await action_engine.execute_approved_action(
        run_id=req.run_id,
        option_id=req.option_id,
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        action_type=req.action_type,
        parameters=req.parameters,
    )
    return result


@app.post("/actions/verify", response_model=Dict[str, Any])
async def verify_action(req: VerifyActionRequest) -> Dict[str, Any]:
    """Runs the post-remediation settle window and telemetry verification."""
    result = await verifier.verify_recovery(tenant_id=req.tenant_id, run_id=req.run_id)
    return result


@app.get("/audit", response_model=List[AuditRecord])
async def get_audit_trail(
    tenant_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> List[AuditRecord]:
    """Returns immutable audit records."""
    return await audit_store.list_audit(tenant_id=tenant_id, run_id=run_id)
