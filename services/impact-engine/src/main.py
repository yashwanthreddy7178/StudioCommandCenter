"""FastAPI application entrypoint for impact-engine."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.config import settings
from src.db import get_db_session, init_db
from src.schema import Deliverable, Production, Shot
from src.calculator import calculate_production_impact
from services.common.models import ImpactProjection
from services.common.telemetry import setup_logging

logger = setup_logging("impact-engine")


class ImpactProjectionRequest(BaseModel):
    """Payload to request a deterministic production impact calculation."""
    tenant_id: str
    affected_workers: List[str] = Field(default_factory=lambda: ["w-03", "w-07", "w-11", "w-17"])
    observed_throughput_fpm: float = 41.2
    baseline_throughput_fpm: float = 118.6
    queue_depth: int = 18432
    as_of: datetime = Field(default_factory=datetime.utcnow)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initializes database and seeds production metadata on startup."""
    await init_db()
    yield


app = FastAPI(
    title="Studio Production Commander - Deterministic Impact Engine",
    description="Deterministic VFX production impact engine and delivery deadline projector",
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
    return {"status": "ok", "service": settings.service_name}


@app.get("/readyz", status_code=status.HTTP_200_OK)
async def readyz(session: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    res = await session.execute(select(Production).limit(1))
    has_prod = res.scalars().first() is not None
    return {"ready": has_prod, "service": settings.service_name}


@app.post("/impact/project", response_model=ImpactProjection)
async def project_impact(
    req: ImpactProjectionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ImpactProjection:
    """Calculates deterministic delivery deadline impact for an incident state."""
    logger.info(
        "Calculating deterministic impact",
        extra={"tenant_id": req.tenant_id, "observed_fpm": req.observed_throughput_fpm, "queue": req.queue_depth}
    )
    projection = await calculate_production_impact(
        session=session,
        tenant_id=req.tenant_id,
        affected_workers=req.affected_workers,
        observed_throughput_fpm=req.observed_throughput_fpm,
        baseline_throughput_fpm=req.baseline_throughput_fpm,
        queue_depth=req.queue_depth,
        as_of=req.as_of,
    )
    return projection


@app.get("/productions", response_model=List[Dict[str, Any]])
async def list_productions(session: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    res = await session.execute(select(Production))
    prods = res.scalars().all()
    return [{"production_id": p.production_id, "title": p.title, "studio": p.studio, "status": p.status} for p in prods]


@app.get("/shots", response_model=List[Dict[str, Any]])
async def list_shots(session: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    res = await session.execute(select(Shot).limit(100))
    shots = res.scalars().all()
    return [
        {
            "shot_id": s.shot_id,
            "scene_id": s.scene_id,
            "code": s.code,
            "priority": s.priority,
            "status": s.status,
            "frame_start": s.frame_start,
            "frame_end": s.frame_end,
        }
        for s in shots
    ]


@app.get("/deliverables", response_model=List[Dict[str, Any]])
async def list_deliverables(session: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    res = await session.execute(select(Deliverable))
    delivs = res.scalars().all()
    return [
        {
            "deliverable_id": d.deliverable_id,
            "production_id": d.production_id,
            "name": d.name,
            "deadline_utc": d.deadline_utc.isoformat(),
        }
        for d in delivs
    ]
