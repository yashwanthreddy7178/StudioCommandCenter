"""Seed script to populate realistic VFX production metadata."""
from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.schema import (
    Deliverable,
    Production,
    RenderJob,
    Scene,
    Sequence,
    Shot,
)


async def seed_production_database(session: AsyncSession) -> None:
    """Populates production metadata if tables are empty."""
    # Check if production already exists
    res = await session.execute(select(Production).limit(1))
    if res.scalars().first() is not None:
        return

    # Base deadline 18:00 UTC today
    now = datetime.utcnow()
    deadline = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if deadline < now:
        deadline = now + timedelta(hours=3, minutes=5)

    # 1. Production
    prod = Production(
        production_id="prod-shadow-protocol",
        title="Shadow Protocol",
        studio="Apex VFX Studios",
        status="IN_PRODUCTION",
    )
    session.add(prod)

    # 2. Deliverables
    deliv1 = Deliverable(
        deliverable_id="SP_VFX_R04",
        production_id="prod-shadow-protocol",
        name="Shadow Protocol - 4K Review Master R04",
        deadline_utc=deadline,
    )
    session.add(deliv1)

    # 3. Sequences
    seq_chase = Sequence(sequence_id="seq-chase", production_id="prod-shadow-protocol", name="Final Chase")
    seq_rooftop = Sequence(sequence_id="seq-rooftop", production_id="prod-shadow-protocol", name="Rooftop Pursuit")
    seq_lab = Sequence(sequence_id="seq-lab", production_id="prod-shadow-protocol", name="Laboratory Infiltration")
    session.add_all([seq_chase, seq_rooftop, seq_lab])

    # 4. Scenes
    sc_chase = Scene(scene_id="sc-04-chase", sequence_id="seq-chase", code="SC_04_CHASE")
    sc_roof = Scene(scene_id="sc-02-rooftop", sequence_id="seq-rooftop", code="SC_02_ROOF")
    sc_lab = Scene(scene_id="sc-01-lab", sequence_id="seq-lab", code="SC_01_LAB")
    session.add_all([sc_chase, sc_roof, sc_lab])

    # 5. Shots
    created_shots = []
    # 1200 shots for Chase (High priority)
    for i in range(1, 1201):
        shot = Shot(
            shot_id=f"shot-chase-{i:04d}",
            scene_id="sc-04-chase",
            code=f"CHASE_{i:04d}",
            frame_start=1001,
            frame_end=1120,
            priority=1,
            status="QUEUED" if i > 50 else "RENDERING",
        )
        shot.deliverables.append(deliv1)
        created_shots.append(shot)

    # 642 shots for Rooftop (High priority)
    for i in range(1, 643):
        shot = Shot(
            shot_id=f"shot-roof-{i:04d}",
            scene_id="sc-02-rooftop",
            code=f"ROOF_{i:04d}",
            frame_start=1001,
            frame_end=1096,
            priority=1,
            status="QUEUED" if i > 30 else "RENDERING",
        )
        shot.deliverables.append(deliv1)
        created_shots.append(shot)

    # 800 shots for Lab (Normal priority)
    for i in range(1, 801):
        shot = Shot(
            shot_id=f"shot-lab-{i:04d}",
            scene_id="sc-01-lab",
            code=f"LAB_{i:04d}",
            frame_start=1001,
            frame_end=1080,
            priority=0,
            status="QUEUED",
        )
        created_shots.append(shot)

    session.add_all(created_shots)

    # 6. Render Jobs assigned to workers
    workers = ["w-01", "w-02", "w-03", "w-04", "w-05", "w-06", "w-07", "w-08"]
    jobs = []
    for idx, worker_id in enumerate(workers):
        assigned_shot = created_shots[idx * 5]
        job = RenderJob(
            job_id=f"job-{worker_id}-001",
            shot_id=assigned_shot.shot_id,
            worker_id=worker_id,
            frame=1042,
            state="RUNNING",
            started_at=now - timedelta(minutes=10),
        )
        jobs.append(job)

    session.add_all(jobs)
    await session.commit()
