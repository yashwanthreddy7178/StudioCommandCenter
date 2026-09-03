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

# Applied before any client is constructed: on a network that inspects TLS the
# default certifi bundle cannot verify the served certificate, and every
# outbound call fails. No-op in a container.
from services.common.tls import enable_system_trust_store

enable_system_trust_store()


logger = setup_logging("action-executor-api")

app = FastAPI(
    title="Studio Production Commander - Action Executor",
    description="Idempotent remediation execution service with audit logging and verification",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
    # Context carried from the run so the Grafana annotation and incident say
    # something a human can act on, rather than just naming the action type.
    # Optional: an approval that omits them still executes and still annotates.
    option_title: str = ""
    production_consequence: str = ""
    at_risk_deliverables: List[str] = Field(default_factory=list)


class VerifyActionRequest(BaseModel):
    """Payload to trigger verification of an applied remediation.

    The pre-action figures are supplied by the caller so recovery is measured as
    a change against the state that prompted the action, rather than asserted.
    """
    tenant_id: str
    run_id: str
    delay_minutes_before: Optional[int] = None
    observed_fpm_before: Optional[float] = None
    settle_seconds: Optional[float] = None


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
        option_title=req.option_title,
        production_consequence=req.production_consequence,
        at_risk_deliverables=req.at_risk_deliverables,
    )
    return result


@app.post("/actions/verify", response_model=Dict[str, Any])
async def verify_action(req: VerifyActionRequest) -> Dict[str, Any]:
    """Runs the post-remediation settle window and telemetry verification."""
    return await verifier.verify_recovery(
        tenant_id=req.tenant_id,
        run_id=req.run_id,
        delay_minutes_before=req.delay_minutes_before,
        observed_fpm_before=req.observed_fpm_before,
        settle_seconds=req.settle_seconds,
    )


@app.get("/audit", response_model=List[AuditRecord])
async def get_audit_trail(
    tenant_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> List[AuditRecord]:
    """Returns immutable audit records."""
    return await audit_store.list_audit(tenant_id=tenant_id, run_id=run_id)
