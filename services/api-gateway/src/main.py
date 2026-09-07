"""FastAPI application entrypoint for api-gateway."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from src.config import settings
from src.lease import lease_manager
from services.common.models import ApprovalRequest, RunDocument, TenantLease
from services.common.auth import (
    internal_auth,
    SingleCredentialAuthMiddleware,
    check_credentials,
    issue_token,
)
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
    # Wildcard origin with credentials is rejected outright by browsers, and
    # nothing needs it: auth is a signed bearer token the app sets on the
    # request, not a cookie the browser attaches on its own.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Every route below is published to the internet by nginx, so the whole app
# sits behind one operator login. /healthz, /readyz and /auth/login stay open.
app.add_middleware(SingleCredentialAuthMiddleware)

http_client = httpx.AsyncClient(timeout=10.0, auth=internal_auth())


class LoginRequest(BaseModel):
    """The single operator credential."""
    username: str
    password: str


@app.post("/auth/login", response_model=Dict[str, Any])
async def login(req: LoginRequest) -> Dict[str, Any]:
    """Exchanges the operator credential for a signed token.

    One shared login, not a user system: there is one username and one password,
    and this hands back a token proving they were presented. Deliberately vague
    on failure -- saying which half was wrong would let the username be probed.
    """
    if not check_credentials(req.username, req.password):
        logger.warning("Rejected login attempt", extra={"username": req.username[:64]})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    token, expires_at = issue_token(req.username)
    logger.info("Operator signed in", extra={"username": req.username})
    return {"token": token, "expires_at": expires_at}


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
    # Left unset so render-sim's own default applies. Duplicating the worker
    # list here silently overrode it and degraded only two workers.
    affected_worker_ids: Optional[List[str]] = None
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

async def _require_lease(tenant_id: str, session_id: str) -> None:
    """Refuses an action on a tenant this session does not hold.

    The tenant id arrives in the request body, so without this a caller could
    name any tenant and act on a world leased by someone else. It matters most
    on the approval route, which executes a real remediation.
    """
    if not await lease_manager.holds_lease(tenant_id, session_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Session does not hold a live lease on tenant '{tenant_id}'. "
                "Acquire a lease before acting on a tenant world."
            ),
        )


@app.post("/runs", response_model=Dict[str, Any])
async def create_run(req: CreateRunRequest) -> Dict[str, Any]:
    """Creates an investigation run and dispatches to agent-worker."""
    await _require_lease(req.tenant_id, req.session_id)
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
        # Returning QUEUED here would leave the client waiting on an event stream
        # that no worker is ever going to write to.
        logger.error("Worker dispatch failed", extra={"run_id": run_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"agent-worker is unavailable, run not started: {str(exc)[:200]}",
        )

    return {
        "status": "QUEUED",
        "run_id": run_id,
        "tenant_id": req.tenant_id,
        "objective": req.objective,
    }


@app.get("/runs/{run_id}", response_model=Dict[str, Any])
async def get_run(run_id: str) -> Dict[str, Any]:
    """Proxies run status lookup to agent-worker.

    A missing run and an unreachable worker are different answers, and this
    reported both as 404: an agent-worker that was down looked exactly like a run
    that never existed, which is the wrong thing to tell a client polling for a
    run it just created.
    """
    try:
        res = await http_client.get(f"{settings.agent_worker_url}/runs/{run_id}")
    except Exception as exc:
        logger.error("Run lookup failed", extra={"run_id": run_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"agent-worker is unreachable, run status unknown: {str(exc)[:200]}",
        )

    if res.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found"
        )
    if res.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"agent-worker failed to return run '{run_id}'",
        )
    return res.json()


@app.post("/runs/{run_id}/approve", response_model=Dict[str, Any])
async def approve_run_action(run_id: str, req: ApprovalRequest) -> Dict[str, Any]:
    """Intake endpoint for human approval of a remediation option."""
    await _require_lease(req.tenant_id, req.session_id)

    # Look up run to get the selected option details
    try:
        run_res = await http_client.get(f"{settings.agent_worker_url}/runs/{run_id}")
        run_data = run_res.json()
        options = run_data.get("options", [])
        chosen_opt = next((o for o in options if o.get("option_id") == req.option_id), None)
        if not chosen_opt:
            raise HTTPException(status_code=400, detail=f"Option '{req.option_id}' not found in run")

        # Forward to action-executor, carrying the option's own description and
        # the deliverables the impact engine flagged. The executor writes both
        # back to Grafana after the action lands, so the annotation an SRE reads
        # names the production consequence rather than just the action type.
        impact = run_data.get("impact") or {}
        executor_payload = {
            "run_id": run_id,
            "option_id": req.option_id,
            "tenant_id": req.tenant_id,
            "user_id": req.user_id,
            "action_type": chosen_opt.get("action_type"),
            "parameters": chosen_opt.get("parameters", {}),
            "option_title": chosen_opt.get("title", ""),
            "production_consequence": chosen_opt.get("production_consequence", ""),
            "at_risk_deliverables": impact.get("at_risk_deliverables", []),
        }
        exec_res = await http_client.post(f"{settings.action_executor_url}/actions/execute", json=executor_payload)
        exec_data = exec_res.json()

        # Hand verification to agent-worker, which owns the run and knows the
        # pre-action projection to measure recovery against. It settles for 90
        # seconds, so this returns immediately and the outcome reaches the client
        # on the event stream rather than holding the approval request open.
        verify_started = False
        try:
            verify_res = await http_client.post(
                f"{settings.agent_worker_url}/runs/{run_id}/verify"
            )
            verify_started = verify_res.status_code == 200
        except Exception as exc:
            logger.warning(
                "Could not start verification",
                extra={"run_id": run_id, "error": str(exc)},
            )

        return {
            "status": "APPROVED_AND_EXECUTED",
            "execution": exec_data,
            "verification_started": verify_started,
            "run_state": "VERIFYING" if verify_started else "DEGRADED",
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
        # Unset fields are dropped so render-sim applies its own defaults rather
        # than receiving an explicit null that overrides them.
        res = await http_client.post(
            f"{settings.render_sim_url}/scenario/trigger-incident",
            json=req.model_dump(exclude_none=True),
        )
        res.raise_for_status()
        return res.json()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to trigger incident: {str(exc)}")


@app.post("/scenario/reset/{tenant_id}", response_model=Dict[str, Any])
async def proxy_reset_world(tenant_id: str) -> Dict[str, Any]:
    """Resets the tenant world and restores a usable delivery window.

    Deliverable deadlines are anchored once when impact-engine seeds, so a stack
    left running drifts past them and every projection is then measured against a
    deadline in the past. Re-anchoring here makes the reset a single operation
    rather than a manual step that has to be remembered.
    """
    try:
        res = await http_client.post(f"{settings.render_sim_url}/scenario/reset/{tenant_id}")
        res.raise_for_status()
        world = res.json()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to reset world: {str(exc)}")

    deadline_utc = None
    try:
        anchor = await http_client.post(
            f"{settings.impact_engine_url}/production/reanchor-deadline",
            json={"minutes_from_now": settings.delivery_window_minutes},
        )
        if anchor.status_code == 200:
            deadline_utc = anchor.json().get("deadline_utc")
    except Exception as exc:
        # The world is reset either way; the caller is told the deadline was not.
        logger.warning("Could not re-anchor deadline", extra={"error": str(exc)})

    return {**world, "deadline_utc": deadline_utc}
