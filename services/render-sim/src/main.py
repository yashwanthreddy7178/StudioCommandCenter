"""FastAPI application entrypoint for render-sim."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from src.config import settings
from src.engine import engine
from src.control import execute_control_action
from src.models import ControlActionRequest, IncidentTriggerRequest
from services.common.telemetry import setup_logging

logger = setup_logging("render-sim-api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manages simulator lifecycle on startup and shutdown."""
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(
    title="Studio Production Commander - Render Simulator",
    description="Multi-tenant discrete-event render farm simulator and control plane",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def healthz() -> Dict[str, str]:
    """Process liveness probe."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/readyz", status_code=status.HTTP_200_OK)
async def readyz() -> Dict[str, Any]:
    """Dependency readiness probe."""
    return {
        "ready": True,
        "service": settings.service_name,
        "worlds_count": len(engine.worlds),
    }


@app.get("/worlds", response_model=List[Dict[str, Any]])
async def list_worlds() -> List[Dict[str, Any]]:
    """Returns the state of all simulated tenant worlds."""
    return [world.to_dict() for world in engine.worlds.values()]


@app.get("/worlds/{tenant_id}", response_model=Dict[str, Any])
async def get_world(tenant_id: str) -> Dict[str, Any]:
    """Returns the state of a specific tenant world."""
    world = engine.get_world(tenant_id)
    if not world:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant world '{tenant_id}' not found"
        )
    return world.to_dict()


@app.post("/scenario/trigger-incident", response_model=Dict[str, Any])
async def trigger_incident(req: IncidentTriggerRequest) -> Dict[str, Any]:
    """Injects a render regression incident into a specific tenant world."""
    try:
        world = engine.trigger_incident(
            tenant_id=req.tenant_id,
            scenario_type=req.scenario_type,
            affected_worker_ids=req.affected_worker_ids,
            new_version=req.new_renderer_version,
            new_tile_size=req.new_tile_size,
        )
        return {
            "status": "INCIDENT_TRIGGERED",
            "world": world.to_dict(),
        }
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@app.post("/scenario/reset/{tenant_id}", response_model=Dict[str, Any])
async def reset_world(tenant_id: str) -> Dict[str, Any]:
    """Resets a tenant world back to clean baseline."""
    try:
        world = engine.reset_world(tenant_id)
        return {
            "status": "WORLD_RESET",
            "world": world.to_dict(),
        }
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@app.post("/control/apply", response_model=Dict[str, Any])
async def apply_control_action(req: ControlActionRequest) -> Dict[str, Any]:
    """Executes a remediation action against the render control plane."""
    try:
        result = execute_control_action(
            tenant_id=req.tenant_id,
            action_type=req.action_type,
            parameters=req.parameters,
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@app.get("/telemetry/{tenant_id}", response_model=Dict[str, Any])
async def get_telemetry(tenant_id: str) -> Dict[str, Any]:
    """Returns in-memory buffered metrics and logs for a tenant world."""
    metrics = [
        m for m in engine.exporter.buffer.metrics
        if m.get("labels", {}).get("tenant_id") == tenant_id
    ]
    logs = [
        l for l in engine.exporter.buffer.logs
        if l.get("labels", {}).get("tenant_id") == tenant_id
    ]
    return {
        "tenant_id": tenant_id,
        "metrics_samples_count": len(metrics),
        "logs_entries_count": len(logs),
        "recent_metrics": metrics[-20:] if metrics else [],
        "recent_logs": logs[-20:] if logs else [],
    }
