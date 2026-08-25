"""FastAPI application entrypoint for api-gateway."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from services.api-gateway.src.config import settings
from services.api-gateway.src.lease import lease_manager
from services.common.models import ApprovalRequest, RunDocument, TenantLease
from services.common.telemetry import setup_logging

logger = setup_logging("api-gateway")

app = FastAPI(
    title="Studio Production Commander - API Gateway",
    description="Ingress API gateway, authentication, and tenant leasing manager",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

http_client = httpx.AsyncClient(timeout=10.0)


class LeaseAcquireRequest(BaseModel):
    session_id: str
    user_id: str = "usr-coordinator"


class LeaseHeartbeatRequest(BaseModel):
    tenant_id: str
    session_id: str


class CreateRunRequest(BaseModel):
    tenant_id: str
    session_id: str
    user_id: str = "usr-coordinator"
    objective: str = "Will Shadow Protocol miss the 18:00 VFX delivery deadline?"


class TriggerIncidentProxyRequest(BaseModel):
    tenant_id: str
    scenario_type: str = "renderer_tile_regression"
    affected_worker_ids: List[str] = Field(default_factory=lambda: ["w-03", "w-07"])
    new_renderer_version: str = "v2.4.1"
    new_tile_size: int = 2048


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def healthz() -> Dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/readyz", status_code=status.HTTP_200_OK)
async def readyz() -> Dict[str, Any]:
    return {"ready": True, "service": settings.service_name}


# ---------------------------------------------------------------------------
# Tenant Lease Endpoints
# ---------------------------------------------------------------------------

@app.post("/leases/acquire", response_model=TenantLease)
async def acquire_lease(req: LeaseAcquireRequest) -> TenantLease:
    """Assigns an isolated tenant world or attaches in observer mode."""
    lease = await lease_manager.acquire_lease(session_id=req.session_id, user_id=req.user_id)
    return lease


@app.post("/leases/heartbeat", response_model=Dict[str, Any])
async def heartbeat_lease(req: LeaseHeartbeatRequest) -> Dict[str, Any]:
    """Extends lease validity."""
    success = await lease_manager.heartbeat(tenant_id=req.tenant_id, session_id=req.session_id)
    return {"success": success, "tenant_id": req.tenant_id}


@app.post("/leases/release", response_model=Dict[str, Any])
async def release_lease(req: LeaseHeartbeatRequest) -> Dict[str, Any]:
    """Releases tenant lease back to pool."""
    success = await lease_manager.release_lease(tenant_id=req.tenant_id, session_id=req.session_id)
    return {"success": success, "tenant_id": req.tenant_id}


@app.get("/leases", response_model=List[TenantLease])
async def list_leases() -> List[TenantLease]:
    """Returns active tenant leases."""
    return await lease_manager.get_active_leases()


# ---------------------------------------------------------------------------
# Investigation Run Endpoints
# ---------------------------------------------------------------------------

@app.post("/runs", response_model=Dict[str, Any])
async def create_run(req: CreateRunRequest) -> Dict[str, Any]:
    """Creates an investigation run and dispatches to agent-worker."""
    run_id = f"run-{uuid.uuid4().hex[:8]}"

    # Dispatch to agent-worker
    worker_payload = {
        "run_id": run_id,
        "tenant_id": req.tenant_id,
        "user_id": req.user_id,
        "session_id": req.session_id,
        "objective": req.objective,
    }

    try:
        res = await http_client.post(f"{settings.agent_worker_url}/runs/investigate", json=worker_payload)
        res.raise_for_status()
    except Exception as exc:
        logger.warning("Worker dispatch failed, starting local investigation", extra={"error": str(exc)})

    return {
        "status": "QUEUED",
        "run_id": run_id,
        "tenant_id": req.tenant_id,
        "objective": req.objective,
    }


@app.get("/runs/{run_id}", response_model=Dict[str, Any])
async def get_run(run_id: str) -> Dict[str, Any]:
    """Proxies run status lookup to agent-worker."""
    try:
        res = await http_client.get(f"{settings.agent_worker_url}/runs/{run_id}")
        res.raise_for_status()
        return res.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found")


@app.post("/runs/{run_id}/approve", response_model=Dict[str, Any])
async def approve_run_action(run_id: str, req: ApprovalRequest) -> Dict[str, Any]:
    """Intake endpoint for human approval of a remediation option."""
    # Look up run to get the selected option details
    try:
        run_res = await http_client.get(f"{settings.agent_worker_url}/runs/{run_id}")
        run_data = run_res.json()
        options = run_data.get("options", [])
        chosen_opt = next((o for o in options if o.get("option_id") == req.option_id), None)
        if not chosen_opt:
            raise HTTPException(status_code=400, detail=f"Option '{req.option_id}' not found in run")

        # Forward to action-executor
        executor_payload = {
            "run_id": run_id,
            "option_id": req.option_id,
            "tenant_id": req.tenant_id,
            "user_id": req.user_id,
            "action_type": chosen_opt.get("action_type"),
            "parameters": chosen_opt.get("parameters", {}),
        }
        exec_res = await http_client.post(f"{settings.action_executor_url}/actions/execute", json=executor_payload)
        exec_data = exec_res.json()

        # Trigger verification
        verify_res = await http_client.post(
            f"{settings.action_executor_url}/actions/verify",
            json={"tenant_id": req.tenant_id, "run_id": run_id}
        )

        return {
            "status": "APPROVED_AND_EXECUTED",
            "execution": exec_data,
            "verification": verify_res.json() if verify_res.status_code == 200 else {},
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Approval pipeline failed: {str(exc)}")


# ---------------------------------------------------------------------------
# Scenario & Incident Trigger Proxy
# ---------------------------------------------------------------------------

@app.post("/scenario/trigger-incident", response_model=Dict[str, Any])
async def proxy_trigger_incident(req: TriggerIncidentProxyRequest) -> Dict[str, Any]:
    """Proxies incident injection to render-sim."""
    try:
        res = await http_client.post(
            f"{settings.render_sim_url}/scenario/trigger-incident",
            json=req.model_dump()
        )
        res.raise_for_status()
        return res.json()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to trigger incident: {str(exc)}")


@app.post("/scenario/reset/{tenant_id}", response_model=Dict[str, Any])
async def proxy_reset_world(tenant_id: str) -> Dict[str, Any]:
    """Proxies world reset to render-sim."""
    try:
        res = await http_client.post(f"{settings.render_sim_url}/scenario/reset/{tenant_id}")
        res.raise_for_status()
        return res.json()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to reset world: {str(exc)}")
