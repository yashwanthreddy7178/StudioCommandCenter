"""FastAPI application entrypoint for impact-engine."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Dict, List
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from src.config import settings
from src.db import get_db_session, init_db
from src.schema import Deliverable, Production, Scene, Sequence, Shot
from src.calculator import calculate_production_impact
from services.common.models import ImpactProjection
from services.common.telemetry import setup_logging

logger = setup_logging("impact-engine")


class ImpactProjectionRequest(BaseModel):
    """Payload to request a deterministic production impact calculation."""
    tenant_id: str
    # No default incident shape: callers state which workers are affected.
    affected_workers: List[str] = Field(default_factory=list)
    observed_throughput_fpm: float = 41.2
    baseline_throughput_fpm: float = 118.6
    queue_depth: int = 2800
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
    allow_credentials=False,
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


class ReanchorDeadlineRequest(BaseModel):
    """Payload to move deliverable deadlines to a window ahead of now."""
    minutes_from_now: int = 185


@app.post("/production/reanchor-deadline", response_model=Dict[str, Any])
async def reanchor_deadline(
    req: ReanchorDeadlineRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Moves every deliverable deadline to a fixed window ahead of the current time.

    Seeded deadlines are anchored once at startup. A service left running past
    that point measures every projection against a deadline already in the past,
    which reports a large delay no matter how healthy the fleet is. Re-anchoring
    alongside a scenario reset restores a meaningful window; the deadline stays
    fixed for the duration of a run, so a projection is still a comparison
    against a deadline rather than a rolling target.
    """
    deadline = datetime.utcnow() + timedelta(minutes=req.minutes_from_now)
    res = await session.execute(select(Deliverable))
    deliverables = res.scalars().all()
    for deliverable in deliverables:
        deliverable.deadline_utc = deadline
    await session.commit()

    logger.info(
        "Re-anchored deliverable deadlines",
        extra={"count": len(deliverables), "deadline_utc": deadline.isoformat()},
    )
    return {
        "status": "REANCHORED",
        "deliverables_updated": len(deliverables),
        "deadline_utc": deadline.isoformat(),
    }


@app.get("/production/sequences", response_model=List[Dict[str, Any]])
async def list_sequences(session: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """Returns each sequence with its shot counts, progress and deliverable.

    Serves the production board, which previously rendered a hardcoded array:
    shot counts happened to match the seed, but progress was a constant that
    flipped on a boolean and the deliverable names included one that does not
    exist. Everything here is counted from the production metadata.
    """
    result = await session.execute(
        select(Sequence).options(
            selectinload(Sequence.scenes).selectinload(Scene.shots).selectinload(Shot.deliverables)
        )
    )

    payload: List[Dict[str, Any]] = []
    for sequence in result.scalars().all():
        shots = [shot for scene in sequence.scenes for shot in scene.shots]
        if not shots:
            continue
        completed = sum(1 for s in shots if s.status == "COMPLETE")
        rendering = sum(1 for s in shots if s.status == "RENDERING")
        deliverables = sorted({d.deliverable_id for s in shots for d in s.deliverables})
        payload.append({
            "sequence_id": sequence.sequence_id,
            "name": sequence.name,
            "total_shots": len(shots),
            "completed_shots": completed,
            "rendering_shots": rendering,
            "progress_pct": round(100.0 * completed / len(shots), 1),
            "priority": "HIGH" if any(s.priority >= 1 for s in shots) else "NORMAL",
            "deliverables": deliverables,
        })

    payload.sort(key=lambda s: (s["priority"] != "HIGH", s["name"]))
    return payload


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
